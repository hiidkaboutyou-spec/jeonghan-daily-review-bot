from __future__ import annotations

"""Explicit production installer for translation v2.

The existing writer class identity is preserved for compatibility. V2 behavior is
bound only to the concrete production writer instance, so v1 regression tests and
other callers remain untouched.
"""

import logging
from types import MethodType
from typing import Any

from .ai import CaptionWriter, GroupCopy
from . import channel_translation as v1
from .channel_entities import canonicalize_group, entity_failures
from .channel_quality import rerank_for_mode
from .gemini_structured import generate_json_v2
from .translation_safety import (
    manual_review_body,
    metadata_only,
    safe_metadata_body,
    semantic_quality_failures,
)
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
)
from .channel_translation_playbook import unavailable_translation

logger = logging.getLogger(__name__)

_ORIGINAL_V1_WRITE_GROUP = v1.ChannelStyleCaptionWriter.write_group
_ORIGINAL_PARSE_BODIES = v1._parse_bodies


def _parse_v2_bodies(parsed: dict[str, Any] | None, expected: list[str]) -> dict[str, str] | None:
    """Accept a valid one-item response even when Gemini mutates its opaque ID.

    The source/body association is unambiguous for a one-item group, so rejecting a
    non-empty body solely because the model rewrote an internal routing ID turns a
    successful translation into `direct_generation_unavailable`. Multi-item groups
    stay strict because positional recovery there could associate text with the
    wrong source update.
    """
    exact = _ORIGINAL_PARSE_BODIES(parsed, expected)
    if exact is not None:
        return exact
    if len(expected) != 1 or not isinstance(parsed, dict):
        return None
    items = parsed.get("items", [])
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        return None
    body = str(items[0].get("body", "")).strip()
    if not body:
        return None
    returned_id = str(items[0].get("id", "")).strip()
    logger.warning(
        "V2 recovered one-item structured response with mismatched routing id (present=%s)",
        bool(returned_id),
    )
    return {expected[0]: body}


def _finalize_output(self, group, copy: GroupCopy) -> GroupCopy:
    if not isinstance(getattr(self, "last_diagnostics", None), dict):
        self.last_diagnostics = {}
    copy = canonicalize_group(group, copy)
    bodies = dict(copy.bodies)
    manual: dict[str, list[str]] = {}
    for item in group.updates:
        if metadata_only(item):
            bodies[item.id] = safe_metadata_body(item)
            continue
        body = bodies.get(item.id, item.text)
        if body.startswith("⚠️ ترجمهٔ خودکار در دسترس نبود"):
            manual[item.id] = ["translation model unavailable"]
            continue
        failures = semantic_quality_failures(item, body)
        if failures:
            manual[item.id] = failures
            bodies[item.id] = manual_review_body(body, failures)
    self.last_manual_review = manual
    self.last_diagnostics["manual_review_ids"] = sorted(manual)
    return GroupCopy(copy.title, copy.category, bodies)


def _installed_write_group(self, group, *, mode: str = "default") -> GroupCopy:
    if group.updates and all(metadata_only(item) for item in group.updates):
        self.last_diagnostics = {"output_mode": "metadata_only_no_generation"}
        return _finalize_output(
            self,
            group,
            GroupCopy(
                title=group.title,
                category=group.category,
                bodies={item.id: safe_metadata_body(item) for item in group.updates},
            ),
        )

    source_text = "\n".join(item.translation_source() for item in group.updates)
    try:
        analysis = analyze_source(
            source_text,
            hinted_content_type=legacy_category_to_content_type(group.category, source_text),
        )
    except Exception as exc:
        logger.error("V2 source analysis failed; using hardened v1: %s", v1._safe_error(exc))
        return _finalize_output(self, group, _ORIGINAL_V1_WRITE_GROUP(self, group, mode=mode))

    if analysis.source_language == "fa":
        # Already-Persian source needs no translation call. Preserve its voice and
        # spend the limited model quota only on real EN/KO/JA/mixed translation.
        self.last_diagnostics = {"output_mode": "persian_source_no_generation"}
        return _finalize_output(
            self,
            group,
            GroupCopy(
                title=group.title,
                category=group.category,
                bodies={item.id: item.translation_source() for item in group.updates},
            ),
        )

    try:
        examples = self.memory.retrieve_examples(
            source_text,
            analysis,
            limit=2 if is_trivial_source(source_text, analysis) else 3,
        )
        examples = rerank_for_mode(examples, mode)
        glossary = self.memory.relevant_glossary(source_text, source_text)
    except Exception as exc:
        logger.error("V2 style retrieval failed; using hardened v1: %s", v1._safe_error(exc))
        return _finalize_output(self, group, _ORIGINAL_V1_WRITE_GROUP(self, group, mode=mode))

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
        "historical_style_examples_sent": len(examples),
    }

    if client is None:
        self.last_diagnostics["fallback"] = "gemini_unavailable_source_preserved"
        return _finalize_output(
            self,
            group,
            GroupCopy(
                title=group.title,
                category=group.category,
                bodies={item.id: unavailable_translation(item.translation_source()) for item in group.updates},
            ),
        )

    direct = self._direct_group(group, analysis, examples, glossary, mode, client)
    if direct is None:
        self.last_diagnostics["fallback"] = "direct_generation_unavailable"
        fallback = GroupCopy(
            title=group.title,
            category=group.category,
            bodies={item.id: unavailable_translation(item.translation_source()) for item in group.updates},
        )
        return _finalize_output(self, group, fallback)

    direct = canonicalize_group(group, direct)
    failed_ids: list[str] = []
    for item in group.updates:
        candidate = direct.bodies.get(item.id, "")
        source = item.translation_source()
        failures = verify_hard_facts(source, candidate, analyze_source(source))
        failures.extend(entity_failures(source, candidate))
        failures.extend(semantic_quality_failures(item, candidate))
        if failures:
            failed_ids.append(item.id)

    if not failed_ids:
        self.last_diagnostics["output_mode"] = "styled_direct"
        return _finalize_output(self, group, direct)

    self.last_diagnostics["direct_failed_ids"] = failed_ids
    repaired = self._repair_failed_items(group, direct, failed_ids, analysis, client)
    if repaired is not None:
        repaired = canonicalize_group(group, repaired)
        still_bad: list[str] = []
        for item in group.updates:
            candidate = repaired.bodies.get(item.id, "")
            source = item.translation_source()
            failures = verify_hard_facts(source, candidate, analyze_source(source))
            failures.extend(entity_failures(source, candidate))
            failures.extend(semantic_quality_failures(item, candidate))
            if failures:
                still_bad.append(item.id)
        if not still_bad:
            self.last_diagnostics["output_mode"] = "styled_direct_repaired"
            return _finalize_output(self, group, repaired)
        self.last_diagnostics["repair_failed_ids"] = still_bad

    # Keep the model candidate for private human review rather than replacing it
    # with a knowingly weak dictionary fallback. _finalize_output marks each
    # semantic failure and the bot never auto-publishes it.
    bodies = dict(direct.bodies)
    self.last_diagnostics["output_mode"] = "styled_direct_needs_review"
    return _finalize_output(self, group, GroupCopy(direct.title or group.title, group.category, bodies))


def install_direct_v2(writer):
    """Bind v2 behavior only to this production writer instance."""
    if getattr(writer, "_channel_direct_v2_installed", False):
        return writer
    # V2 calls v1._parse_bodies dynamically. Install a narrowly-scoped recovery
    # that is only permissive for one-item responses, where routing by order is
    # unambiguous. Multi-item production groups retain exact-ID validation.
    v1._parse_bodies = _parse_v2_bodies
    writer._direct_group = MethodType(V2Methods._direct_group, writer)
    writer._repair_failed_items = MethodType(V2Methods._repair_failed_items, writer)
    # Bind the current SDK adapter used by the real production writer. It accepts
    # both response.parsed and response.text and records bounded, redacted failure
    # diagnostics instead of silently collapsing every unusable response to None.
    writer._generate_json_v2 = MethodType(generate_json_v2, writer)
    writer.write_group = MethodType(_installed_write_group, writer)
    # The current free project accepts about twenty request starts per minute.
    # Keep headroom for preflight/other project traffic instead of allowing one
    # large X batch to turn the remaining drafts into translation-outage notices.
    writer._gemini_min_request_interval_seconds = 3.5
    writer._gemini_next_request_at = 0.0
    writer._channel_direct_v2_installed = True
    return writer


def harden_legacy_instance(writer: CaptionWriter) -> CaptionWriter:
    """Keep CaptionWriter type identity while canonicalizing its delivered output."""
    if getattr(writer, "_channel_entity_hardened", False):
        return writer
    original = writer.write_group

    def hardened(self, group, *, mode: str = "default") -> GroupCopy:
        return canonicalize_group(group, original(group, mode=mode))

    writer.write_group = MethodType(hardened, writer)
    writer._channel_entity_hardened = True
    return writer
