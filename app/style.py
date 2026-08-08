from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .channel_style_runtime import ChannelStyleMemory
from .models import EventGroup, Update

RLM = "\u200f"
PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
DECORATIVE_START_RE = re.compile(r"^[\s\u200e\u200f\u202a-\u202e\u2066-\u2069،,𐄂-🿿✦✧☆★୨︵꒰𓋜𖥨⿻⌕࣪˖]+")


class StyleMemory(ChannelStyleMemory):
    """Backward-compatible name for the versioned channel-style memory."""

    def __init__(self, root: Path, db_path: Path | None = None):
        super().__init__(root, db_path=db_path)


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
        body_lines = [
            ensure_rtl_line(line.strip()) if PERSIAN_RE.search(line) else line.strip()
            for line in clean_body.splitlines()
        ]
        part_line = ""
        if total > 1:
            part_line = ensure_rtl_line(f"بخش {part} از {total}")
        source = f"⌕ @{update.author} · {update.url}"
        chunks = [header, "\n".join(body_lines).strip()]
        if part_line:
            chunks.append(part_line)
        chunks.append(source)
        # Never silently cut a source/caption. TelegramBot will reject an over-limit
        # message explicitly so the update remains pending instead of being marked seen.
        return "\n\n".join(chunk for chunk in chunks if chunk)
