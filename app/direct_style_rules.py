"""Deterministic, evidence-bound Direct User Style Rules for shadow candidates.

This module owns presentation planning only. It never owns factual text,
retrieval, Telegram delivery, or learned preferences. Every factual slot in a
header must be supplied by current Update/Event evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

DIRECT_STYLE_RULES_VERSION = 1
DIRECT_STYLE_RULES_MODE = "shadow"
DEFAULT_AUTHORITY_ORDER = (
    "factual_fidelity",
    "direct_user_style_rules",
    "stable_real_user_edit_preferences",
    "historical_style_examples",
    "generic_style_heuristics",
)
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "direct_style_rules.json"
_PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_URL_LINE_RE = re.compile(r"^\s*(?:https?://|www\.)\S+\s*$", re.I)
_LIST_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_DIALOGUE_RE = re.compile(r"(?m)^\s*[^\s:：]{1,28}\s*[:：]")


def _clean(value: object, limit: int = 160) -> str:
    return str(value or "").strip()[:limit]


def _norm(value: object) -> str:
    text = _clean(value).casefold().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def _safe_sequence(value: object, limit: int = 20) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(_clean(item, 120) for item in list(value)[:limit] if _clean(item, 120))


@dataclass(frozen=True, slots=True)
class DirectStyleEvidence:
    """Current-evidence-only inputs. Historical examples are intentionally absent."""

    content_type: str = "OTHER"
    category: str = "general"
    platform: str = ""
    account: str = ""
    brand: str = ""
    date: str = ""
    title: str = ""
    is_story: bool = False
    is_dialogue: bool = False
    has_jeonghan: bool = False
    ambiguous: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "DirectStyleEvidence":
        row = value if isinstance(value, Mapping) else {}
        return cls(
            content_type=_clean(row.get("content_type"), 48) or "OTHER",
            category=_norm(row.get("category")) or "general",
            platform=_norm(row.get("platform")),
            account=_norm(row.get("account")).lstrip("@"),
            brand=_norm(row.get("brand")),
            date=_clean(row.get("date"), 16),
            title=_clean(row.get("title"), 160),
            is_story=bool(row.get("is_story", False)),
            is_dialogue=bool(row.get("is_dialogue", False)),
            has_jeonghan=bool(row.get("has_jeonghan", False)),
            ambiguous=bool(row.get("ambiguous", False)),
        )


@dataclass(frozen=True, slots=True)
class DirectStyleRule:
    rule_id: str
    priority: int
    category: str
    platforms: tuple[str, ...] = ()
    accounts: tuple[str, ...] = ()
    brands: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    content_types: tuple[str, ...] = ()
    story: bool | None = None
    require_jeonghan: bool = False
    required_evidence: tuple[str, ...] = ()
    header_template: str = ""
    body_prefix_policy: str = "none"
    symbol_policy: str = "none"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DirectStyleRule":
        story = value.get("story") if "story" in value else None
        return cls(
            rule_id=_clean(value.get("rule_id"), 80),
            priority=int(value.get("priority", 0) or 0),
            category=_clean(value.get("category"), 48),
            platforms=tuple(_norm(item) for item in _safe_sequence(value.get("platforms"))),
            accounts=tuple(_norm(item).lstrip("@") for item in _safe_sequence(value.get("accounts"))),
            brands=tuple(_norm(item) for item in _safe_sequence(value.get("brands"))),
            categories=tuple(_norm(item) for item in _safe_sequence(value.get("categories"))),
            content_types=_safe_sequence(value.get("content_types")),
            story=bool(story) if story is not None else None,
            require_jeonghan=bool(value.get("require_jeonghan", False)),
            required_evidence=_safe_sequence(value.get("required_evidence")),
            header_template=_clean(value.get("header_template"), 240),
            body_prefix_policy=_clean(value.get("body_prefix_policy"), 48) or "none",
            symbol_policy=_clean(value.get("symbol_policy"), 48) or "none",
        )

    def matches(self, evidence: DirectStyleEvidence) -> bool:
        if evidence.ambiguous:
            return False
        platform = _norm(evidence.platform)
        account = _norm(evidence.account).lstrip("@")
        brand = _norm(evidence.brand)
        category = _norm(evidence.category)
        if self.platforms and platform not in self.platforms:
            return False
        if self.accounts and account not in self.accounts:
            return False
        if self.brands and brand not in self.brands:
            return False
        if self.story is not None and evidence.is_story is not self.story:
            return False
        if self.require_jeonghan and not evidence.has_jeonghan:
            return False
        if self.categories or self.content_types:
            if category not in self.categories and evidence.content_type not in self.content_types:
                return False
        return True


@dataclass(frozen=True, slots=True)
class StyleDirective:
    rule_id: str = ""
    category: str = "generic"
    header: str = ""
    body_prefix: str = ""
    symbol: str = ""
    applied: bool = False
    fallback_reason: str = "no_matching_direct_rule"
    authority_order: tuple[str, ...] = DEFAULT_AUTHORITY_ORDER

    def render(self, body: str, *, is_dialogue: bool = False) -> str:
        value = str(body or "").strip()
        if not self.applied:
            return value
        if self.body_prefix and not is_dialogue:
            value = _prefix_first_safe_persian_line(value, self.body_prefix)
        return f"{self.header}\n{value}".strip() if self.header else value

    def factual_projection(self, candidate: str, *, is_dialogue: bool = False) -> tuple[str, tuple[str, ...]]:
        value = str(candidate or "").strip()
        failures: list[str] = []
        if not self.applied:
            return value, ()
        if self.header:
            first, separator, rest = value.partition("\n")
            if first != self.header or not separator:
                failures.append("direct_rule_header_changed")
            else:
                value = rest
        if self.body_prefix and not is_dialogue:
            value, removed = _remove_first_safe_persian_prefix(value, self.body_prefix)
            if not removed and _has_safe_persian_line(value):
                failures.append("direct_rule_body_prefix_changed")
        return value.strip(), tuple(failures)

    def metadata(self) -> dict[str, Any]:
        return {
            "version": DIRECT_STYLE_RULES_VERSION,
            "mode": DIRECT_STYLE_RULES_MODE,
            "rule_id": self.rule_id[:80],
            "category": self.category[:48],
            "applied": bool(self.applied),
            "fallback_reason": self.fallback_reason[:80],
            "symbol": self.symbol[:32],
            "authority_order": list(self.authority_order),
            "text_persisted": False,
        }


@dataclass(frozen=True, slots=True)
class DirectStyleRuleSet:
    version: int
    authority_order: tuple[str, ...]
    symbol_pool: tuple[str, ...]
    body_prefixes: tuple[str, ...]
    max_recent_symbols: int
    rules: tuple[DirectStyleRule, ...]

    @classmethod
    def load(cls, path: Path = _CONFIG_PATH) -> "DirectStyleRuleSet":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            raw = {}
        authority = _safe_sequence(raw.get("authority_order"), 10)
        if authority != DEFAULT_AUTHORITY_ORDER:
            authority = DEFAULT_AUTHORITY_ORDER
        rules = tuple(
            sorted(
                (DirectStyleRule.from_mapping(item) for item in raw.get("rules", []) if isinstance(item, Mapping)),
                key=lambda item: (-item.priority, item.rule_id),
            )
        )
        return cls(
            version=int(raw.get("version", DIRECT_STYLE_RULES_VERSION) or DIRECT_STYLE_RULES_VERSION),
            authority_order=authority,
            symbol_pool=_safe_sequence(raw.get("symbol_pool"), 20),
            body_prefixes=_safe_sequence(raw.get("body_prefixes"), 8),
            max_recent_symbols=max(1, min(int(raw.get("max_recent_symbols", 4) or 4), 8)),
            rules=rules,
        )


class DirectStylePlanner:
    def __init__(self, rule_set: DirectStyleRuleSet | None = None):
        self.rule_set = rule_set or DirectStyleRuleSet.load()

    def plan(
        self,
        evidence: DirectStyleEvidence | Mapping[str, Any] | None,
        *,
        context_key: str,
        recent_symbols: Sequence[object] = (),
    ) -> StyleDirective:
        current = evidence if isinstance(evidence, DirectStyleEvidence) else DirectStyleEvidence.from_mapping(evidence)
        if current.ambiguous:
            return StyleDirective(fallback_reason="ambiguous_current_evidence", authority_order=self.rule_set.authority_order)
        for rule in self.rule_set.rules:
            if not rule.matches(current):
                continue
            missing = _missing_evidence(rule, current)
            if missing:
                return StyleDirective(
                    rule_id=rule.rule_id,
                    category=rule.category,
                    fallback_reason="missing_current_evidence:" + ",".join(missing),
                    authority_order=self.rule_set.authority_order,
                )
            symbol = ""
            if rule.symbol_policy == "rotate":
                symbol = select_rotating_symbol(context_key, self.rule_set.symbol_pool, recent_symbols)
                if not symbol:
                    return StyleDirective(
                        rule_id=rule.rule_id,
                        category=rule.category,
                        fallback_reason="symbol_pool_unavailable",
                        authority_order=self.rule_set.authority_order,
                    )
            prefix = ""
            if rule.body_prefix_policy == "deterministic_family" and not current.is_dialogue:
                prefix = select_body_prefix(context_key, self.rule_set.body_prefixes)
            try:
                header = rule.header_template.format(date=current.date, title=current.title, symbol=symbol)
            except (KeyError, ValueError):
                return StyleDirective(
                    rule_id=rule.rule_id,
                    category=rule.category,
                    fallback_reason="invalid_rule_template",
                    authority_order=self.rule_set.authority_order,
                )
            return StyleDirective(
                rule_id=rule.rule_id,
                category=rule.category,
                header=header,
                body_prefix=prefix,
                symbol=symbol,
                applied=True,
                fallback_reason="",
                authority_order=self.rule_set.authority_order,
            )
        return StyleDirective(authority_order=self.rule_set.authority_order)


def _missing_evidence(rule: DirectStyleRule, evidence: DirectStyleEvidence) -> list[str]:
    missing: list[str] = []
    for name in rule.required_evidence:
        if name == "platform" and not evidence.platform:
            missing.append(name)
        elif name == "account" and not evidence.account:
            missing.append(name)
        elif name == "brand" and not evidence.brand:
            missing.append(name)
        elif name == "date" and not re.fullmatch(r"\d{6}", evidence.date):
            missing.append(name)
        elif name == "title" and (not evidence.title or not _LATIN_RE.search(evidence.title)):
            missing.append(name)
        elif name == "jeonghan" and not evidence.has_jeonghan:
            missing.append(name)
        elif name == "story" and not evidence.is_story:
            missing.append(name)
    return missing


def select_rotating_symbol(context_key: str, pool: Sequence[object], recent_symbols: Sequence[object] = ()) -> str:
    clean_pool = tuple(_clean(item, 32) for item in pool if _clean(item, 32))
    if not clean_pool:
        return ""
    recent = {_clean(item, 32) for item in list(recent_symbols)[-8:] if _clean(item, 32) in clean_pool}
    start = int.from_bytes(hashlib.sha256(_clean(context_key, 240).encode("utf-8")).digest()[:4], "big") % len(clean_pool)
    for offset in range(len(clean_pool)):
        candidate = clean_pool[(start + offset) % len(clean_pool)]
        if candidate not in recent:
            return candidate
    return clean_pool[(start + 1) % len(clean_pool)]


def select_body_prefix(context_key: str, prefixes: Sequence[object]) -> str:
    clean = tuple(_clean(item, 40) + (" " if not str(item).endswith(" ") else "") for item in prefixes if _clean(item, 40))
    if not clean:
        return ""
    index = int.from_bytes(hashlib.sha256(("prefix:" + _clean(context_key, 240)).encode("utf-8")).digest()[:4], "big") % len(clean)
    return clean[index]


def _prefix_first_safe_persian_line(text: str, prefix: str) -> str:
    lines = str(text or "").splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or _URL_LINE_RE.match(stripped) or _LIST_LINE_RE.match(stripped):
            continue
        if _PERSIAN_RE.search(stripped) and not _DIALOGUE_RE.match(stripped):
            lines[index] = prefix + stripped
            break
    return "\n".join(lines).strip()


def _remove_first_safe_persian_prefix(text: str, prefix: str) -> tuple[str, bool]:
    lines = str(text or "").splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or _URL_LINE_RE.match(stripped) or _LIST_LINE_RE.match(stripped):
            continue
        if stripped.startswith(prefix.strip()):
            lines[index] = stripped[len(prefix.strip()):].lstrip()
            return "\n".join(lines).strip(), True
        if _PERSIAN_RE.search(stripped):
            return "\n".join(lines).strip(), False
    return "\n".join(lines).strip(), False


def _has_safe_persian_line(text: str) -> bool:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or _URL_LINE_RE.match(stripped) or _LIST_LINE_RE.match(stripped):
            continue
        if _PERSIAN_RE.search(stripped) and not _DIALOGUE_RE.match(stripped):
            return True
    return False


def configured_symbols() -> tuple[str, ...]:
    return DirectStyleRuleSet.load().symbol_pool


__all__ = [
    "DEFAULT_AUTHORITY_ORDER",
    "DIRECT_STYLE_RULES_MODE",
    "DIRECT_STYLE_RULES_VERSION",
    "DirectStyleEvidence",
    "DirectStylePlanner",
    "DirectStyleRule",
    "DirectStyleRuleSet",
    "StyleDirective",
    "configured_symbols",
    "select_body_prefix",
    "select_rotating_symbol",
]
