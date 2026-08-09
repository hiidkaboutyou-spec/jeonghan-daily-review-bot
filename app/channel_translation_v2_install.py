from __future__ import annotations

"""Explicit production installer for translation v2.

The existing writer class identity is preserved for compatibility. V2 behavior is
bound only to the concrete production writer instance, so v1 regression tests and
other callers remain untouched.
"""

import logging
from types import MethodType

from .ai import CaptionWriter, GroupCopy
from . import channel_translation as v1
from .channel_quality import rerank_for_mode
from .channel_style_runtime import (
    CHANNEL_STYLE_VERSION,
    PROMPT_TEMPLATE_VERSION,
    analyze_source,
    is_trivial_source,
    legacy_category_to_content_type,
    verify_hard_facts,
)
from .channel_translation_v2 import (
    DIRECT_PIPELINE_VERSION,
    ChannelStyleCaptionWriter as V2Methods,
    _canonicalize_group,
    canonicalize_jeonghan,
    entity_failures,
)

logger = logging.getLogger(__name__)

_ORIGINAL_V1_WRITE_GROUP = v1.ChannelStyleCaptionWriter.write_group


def _installed_write_group(self, group, *, mode: str = "default") -> GroupCopy:
    source_text = "\n".join(item.text for item in group.updates)
    try:
        analysis = analyze_source(
            source_text,
            hinted_content_type=legacy_category_to_content_type(group.category, source_text),
        )
    except Exception as exc:
        logger.error("V2 source analysis failed; using hardened v1: %s", v1._safe_error(exc))
        return _canonicalize_group(group, _ORIGINAL_V1_WRITE_GROUP(self, group, mode=mode))

    try:
        examples = self.memory.retrieve_examples(
            source_text,
            analysis,
            limit=8 if is_trivial_source(source_text, analysis) else 10,
        )
        examples = rerank_for_mode(examples, mode)
        glossary = self.memory.relevant_glossary(source_text, source_text)
    except Exception as exc:
        logger.error("V2 style retrieval failed; using hardened v1: %s", v1._safe_error(exc))
        return _canonicalize_group(group, _ORIGINAL_V1_WRITE_GROUP(self, group, mode=mode))

    client = self._client_or_none()
    self.last_diagnostics = {
        "pipeline_version": DIRECT_PIPELINE_VERSION,
        "style_version": CHANNEL_STYLE_VERSION,
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        "content_type": analysis.content_type,
        "source_language": analysis.source_language,
        "retrieved_example_ids": [item.example_id for item in examples],
        "retrieval_scores": {item.example_id: round(item.score, 4) for item in examples},
        "glossary_entries": [entry.get("canonical_form") for entry in glossary],
        "rewrite_mode": mode,
        "date_score_contribution": 0,
        "recency_weighting": "NONE",
        "normal_generation_calls_target": 1,
    }

    if client is None:
        self.last_diagnostics["fallback"] = "gemini_unavailable_hardened_v1"
        return _canonicalize_group(group, _ORIGINAL_V1_WRITE_GROUP(self, group, mode=mode))

    direct = self._direct_group(group, analysis, examples, glossary, mode, client)
    if direct is None:
        self.last_diagnostics["fallback"] = "direct_generation_unavailable"
        fallback = GroupCopy(
            title=group.title,
            category=group.category,
            bodies={item.id: v1._translate_preserving_structure(item.text) for item in group.updates},
        )
        return _canonicalize_group(group, fallback)

    direct = _canonicalize_group(group, direct)
    failed_ids: list[str] = []
    for item in group.updates:
        candidate = direct.bodies.get(item.id, "")
        failures = verify_hard_facts(item.text, candidate, analyze_source(item.text))
        failures.extend(entity_failures(item.text, candidate))
        if failures:
            failed_ids.append(item.id)

    if not failed_ids:
        self.last_diagnostics["output_mode"] = "styled_direct"
        return direct

    self.last_diagnostics["direct_failed_ids"] = failed_ids
    repaired = self._repair_failed_items(group, direct, failed_ids, analysis, client)
    if repaired is not None:
        repaired = _canonicalize_group(group, repaired)
        still_bad: list[str] = []
        for item in group.updates:
            candidate = repaired.bodies.get(item.id, "")
            failures = verify_hard_facts(item.text, candidate, analyze_source(item.text))
            failures.extend(entity_failures(item.text, candidate))
            if failures:
                still_bad.append(item.id)
        if not still_bad:
            self.last_diagnostics["output_mode"] = "styled_direct_repaired"
            return repaired
        self.last_diagnostics["repair_failed_ids"] = still_bad

    bodies = dict(direct.bodies)
    for item in group.updates:
        if item.id in failed_ids:
            bodies[item.id] = canonicalize_jeonghan(
                item.text, v1._translate_preserving_structure(item.text)
            )
    self.last_diagnostics["output_mode"] = "styled_direct_partial_fallback"
    return GroupCopy(direct.title or group.title, group.category, bodies)


def install_direct_v2(writer):
    """Bind v2 behavior only to this production writer instance."""
    if getattr(writer, "_channel_direct_v2_installed", False):
        return writer
    writer._direct_group = MethodType(V2Methods._direct_group, writer)
    writer._repair_failed_items = MethodType(V2Methods._repair_failed_items, writer)
    writer._generate_json_v2 = MethodType(V2Methods._generate_json_v2, writer)
    writer.write_group = MethodType(_installed_write_group, writer)
    writer._channel_direct_v2_installed = True
    return writer


def harden_legacy_instance(writer: CaptionWriter) -> CaptionWriter:
    """Keep CaptionWriter type identity while canonicalizing its delivered output."""
    if getattr(writer, "_channel_entity_hardened", False):
        return writer
    original = writer.write_group

    def hardened(self, group, *, mode: str = "default") -> GroupCopy:
        return _canonicalize_group(group, original(group, mode=mode))

    writer.write_group = MethodType(hardened, writer)
    writer._channel_entity_hardened = True
    return writer
