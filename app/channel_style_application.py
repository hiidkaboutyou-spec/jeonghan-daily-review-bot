from __future__ import annotations

import logging

from .ai import CaptionWriter
from .channel_style_safety import validate_production_style_memory
from .channel_translation_v2 import ChannelStyleCaptionWriter, SafeLegacyCaptionWriter
from .config import ROOT
from .reminder_runtime import ReminderReviewApplication

logger = logging.getLogger(__name__)


class ChannelStyleReviewApplication(ReminderReviewApplication):
    """Production private-review application using translation v2 first.

    The compatibility writer is retained only as a safe fallback, but even that
    fallback applies source-authorized channel entity spelling (for example
    Jeonghan -> جونگهان) before a private-review draft is delivered.
    """

    def __init__(self, settings):
        super().__init__(settings)
        self.channel_style_enabled = False
        self.channel_style_error = ""
        self.channel_style_indexed_examples = 0

        # Never retain the raw legacy writer as the final production fallback.
        # It uses a generic machine-translation path and cannot enforce the
        # channel's canonical entity spellings.
        legacy_writer = SafeLegacyCaptionWriter(
            settings.gemini_api_key,
            settings.gemini_model,
            self.memory,
        )
        self.legacy_writer = legacy_writer

        ok, reason, indexed = validate_production_style_memory(self.memory, ROOT)
        self.channel_style_indexed_examples = indexed
        if not ok:
            self.writer = self.legacy_writer
            self.channel_style_error = reason
            logger.error("Channel style translation disabled; hardened legacy writer retained: %s", reason)
            return

        try:
            self.writer = ChannelStyleCaptionWriter(
                settings.gemini_api_key,
                settings.gemini_model,
                self.memory,
            )
        except Exception as exc:
            self.writer = self.legacy_writer
            self.channel_style_error = type(exc).__name__
            logger.error(
                "Channel translation v2 initialization failed; hardened legacy writer retained: %s",
                type(exc).__name__,
            )
            return

        self.channel_style_enabled = True
        logger.info(
            "Channel translation v2 active as PRIMARY production writer with %s historical examples.",
            indexed,
        )
