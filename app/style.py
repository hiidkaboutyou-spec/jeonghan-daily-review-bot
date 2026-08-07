from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import EventGroup, Update

RLM = "\u200f"
PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
DECORATIVE_START_RE = re.compile(r"^[\s\u200e\u200f\u202a-\u202e\u2066-\u2069،,𐄂-🿿✦✧☆★୨︵꒰𓋜𖥨⿻⌕࣪˖]+")
TOKEN_RE = re.compile(r"[0-9A-Za-z_\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+")


@dataclass(slots=True)
class StyleSample:
    text: str
    category: str
    date: str
    tokens: set[str]


class StyleMemory:
    def __init__(self, root: Path):
        self.root = root
        self.profile = self._load_json(root / "data" / "channel_voice_profile.json")
        self.samples = self._load_samples(root / "data" / "channel_memory.jsonl")

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token.lower() for token in TOKEN_RE.findall(text) if len(token) > 1}

    def _load_samples(self, path: Path) -> list[StyleSample]:
        result: list[StyleSample] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return result
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            result.append(
                StyleSample(
                    text=text,
                    category=str(item.get("category", "general")),
                    date=str(item.get("date", "")),
                    tokens=self._tokens(text),
                )
            )
        return result

    def retrieve(self, query: str, category: str, limit: int = 8) -> list[str]:
        query_tokens = self._tokens(query)
        scored: list[tuple[float, StyleSample]] = []
        for sample in self.samples:
            overlap = len(query_tokens & sample.tokens)
            union = max(1, len(query_tokens | sample.tokens))
            lexical = overlap / union
            category_bonus = 0.35 if sample.category == category else 0.0
            recent_bonus = 0.08 if sample.date >= "2025-01-01" else 0.0
            length_bonus = min(len(sample.text), 500) / 5000
            score = lexical + category_bonus + recent_bonus + length_bonus
            if score > 0.08:
                scored.append((score, sample))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [sample.text[:1200] for _, sample in scored[:limit]]


def ensure_rtl_line(line: str, *, header: bool = False) -> str:
    if not line:
        return line
    stripped = line.lstrip("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069 ")
    if not PERSIAN_RE.search(stripped):
        return line
    if header and not stripped.startswith("،،"):
        stripped = "،، " + stripped
    return RLM + stripped


def apply_rtl(text: str, *, first_line_is_header: bool = True) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    first_nonempty = True
    for line in lines:
        if not line.strip():
            output.append("")
            continue
        header = first_nonempty and first_line_is_header
        output.append(ensure_rtl_line(line.strip(), header=header))
        first_nonempty = False
    return "\n".join(output).strip()


class ThemeEngine:
    def __init__(self, themes: dict[str, Any], timezone):
        self.themes = themes.get("themes", {})
        self.timezone = timezone

    def _variant(self, category: str, event_key: str) -> dict[str, Any]:
        family = self.themes.get(category) or self.themes.get("general") or {}
        variants = list(family.get("variants", [])) or [{"prefix": "،، 🪽⌕໋  ִ˒˒", "label": "آپدیت جونگهان"}]
        digest = int(hashlib.sha1(event_key.encode("utf-8")).hexdigest()[:8], 16)
        return variants[digest % len(variants)]

    def header(self, group: EventGroup) -> str:
        variant = self._variant(group.category, group.key)
        date = group.started_at.astimezone(self.timezone).strftime("%y%m%d")
        label = str(variant.get("label", group.title)).format(title=group.title, date=date)
        prefix = str(variant.get("prefix", "،، 🪽⌕໋  ִ˒˒"))
        return ensure_rtl_line(f"{prefix} {label}", header=True)

    def caption(
        self,
        group: EventGroup,
        update: Update,
        body: str,
        part: int,
        total: int,
    ) -> str:
        header = self.header(group)
        clean_body = body.strip() or update.text.strip()
        body_lines = [ensure_rtl_line(line.strip()) if PERSIAN_RE.search(line) else line.strip() for line in clean_body.splitlines()]
        part_line = ""
        if total > 1:
            part_line = ensure_rtl_line(f"بخش {part} از {total}")
        source = f"⌕ @{update.author} · {update.url}"
        chunks = [header, "\n".join(body_lines).strip()]
        if part_line:
            chunks.append(part_line)
        chunks.append(source)
        caption = "\n\n".join(chunk for chunk in chunks if chunk)
        return caption[:4000]
