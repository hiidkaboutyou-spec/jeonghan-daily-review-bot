from __future__ import annotations

import logging

from .channel_translation import ChannelStyleCaptionWriter
from .reminder_runtime import ReminderReviewApplication

logger = logging.getLogger(__name__)


class ChannelStyleReviewApplication(ReminderReviewApplication):
    """Production private-review application with channel-style translation v1.

    The inherited Application creates the legacy writer first, which is deliberately
    retained as a safe fallback. We replace it only after the real channel corpus,
    profile, glossary and style index have initialized successfully.
    """

    def __init__(self, settings):
        super().__init__(settings)
        self.channel_style_enabled = False
        self.channel_style_error = ""
        legacy_writer = self.writer
        try:
            sample_count = int(getattr(self.memory, "sample_count", len(getattr(self.memory, "samples", []))) or 0)
            profile = getattr(self.memory, "profile", {}) or {}
            glossary = getattr(self.memory, "glossary", {}) or {}
            authority = profile.get("authority", {}) if isinstance(profile, dict) else {}
            authority_count = authority.get("unique_textual_messages", profile.get("unique_textual_messages", 0)) if isinstance(profile, dict) else 0
            base_weight = authority.get("chronological_base_weight", profile.get("base_style_weight", 0)) if isinstance(profile, dict) else 0
            recency = authority.get("recency_weighting", profile.get("chronological_weighting", "")) if isinstance(profile, dict) else ""
            if sample_count <= 0:
                raise RuntimeError("channel style corpus/index is empty")
            if int(authority_count or 0) != 16306:
                raise RuntimeError("channel style authority metadata is invalid")
            if float(base_weight or 0) != 1.0:
                raise RuntimeError("channel style base weight is invalid")
            if str(recency).upper() not in {"NONE", "NO", "0"}:
                raise RuntimeError("channel style recency weighting must be NONE")
            if not isinstance(glossary.get("categories"), dict):
                raise RuntimeError("channel style glossary is invalid")
            self.writer = ChannelStyleCaptionWriter(
                settings.gemini_api_key,
                settings.gemini_model,
                self.memory,
            )
            self.channel_style_enabled = True
            logger.info("Channel style translation v1 active with %s historical examples.", sample_count)
        except Exception as exc:
            self.writer = legacy_writer
            self.channel_style_error = type(exc).__name__
            logger.error("Channel style translation disabled; safe legacy writer retained: %s", type(exc).__name__)
