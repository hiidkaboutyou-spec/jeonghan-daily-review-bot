from __future__ import annotations

import logging

from .ai import CaptionWriter
from .channel_style_safety import validate_production_style_memory
from .channel_translation import ChannelStyleCaptionWriter
from .channel_translation_v2_install import harden_legacy_instance, install_direct_v2
from .config import ROOT
from .media_delivery_runtime import MediaDedupReviewApplication

logger = logging.getLogger(__name__)


class ChannelStyleReviewApplication(MediaDedupReviewApplication):
    """Production private-review application using translation v2 behavior first.

    Existing writer class identities are preserved for compatibility, but normal
    production behavior is upgraded explicitly to the direct v2 pipeline. The
    compatibility fallback is also hardened so Jeonghan cannot be delivered with
    a generic machine-transliteration spelling. Exact media delivery is additionally
    deduplicated in the production runtime before Telegram upload.
    """

    def __init__(self, settings):
        super().__init__(settings)
        self.channel_style_enabled = False
        self.channel_style_error = ""
        self.channel_style_indexed_examples = 0

        legacy_writer = self.writer
        if not isinstance(legacy_writer, CaptionWriter):
            legacy_writer = CaptionWriter(settings.gemini_api_key, settings.gemini_model, self.memory)
        self.legacy_writer = harden_legacy_instance(legacy_writer)

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
            install_direct_v2(self.writer)
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
            "Channel translation v2 active as PRIMARY production behavior with %s historical examples.",
            indexed,
        )
