from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from . import channel_part4_hardening as hardening
from . import channel_style_runtime as runtime
from . import channel_translation as translation
from .ai import GroupCopy
from .translation_safety import semantic_quality_failures

HUMAN_GATE_VERSION = 1
HUMAN_GATE_FINGERPRINT = f"channel-human-gate-v{HUMAN_GATE_VERSION}"

_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_METADATA_LABEL_RE = re.compile(r"(?mi)^(fan trans|source)\s*:")
_FORMAL_REACTION_MARKERS = ("به نظر می‌رسد", "به نظر میرسد", "مورد اشاره قرار گرفت", "می‌تواند", "می تواند")
_MANUAL_REVIEW_PREFIX = "⚠️ نیاز به بازبینی دستی"
_MACHINE_PERSIAN_MARKERS = (
    "می کند", "می دهد", "می شود", "نمی کند", "او دیگر", "خود را", "با استفاده از",
    "به وضوح", "متعلق به", "بلافاصله", "ناگهان اینجاست", "تا حالا خوردی",
    "به روز رسانی", "از جمله", "من الان", "آیا ما می توانیم", "قول بده",
)
_LABEL_CANONICAL = {
    "jeonghan": "جونگهان",
    "yoon jeonghan": "جونگهان",
    "정한": "جونگهان",
    "윤정한": "جونگهان",
    "ジョンハン": "جونگهان",
    "joshua": "جاشوآ",
    "조슈아": "جاشوآ",
    "ジョシュア": "جاشوآ",
    "seungcheol": "سونگچول",
    "s.coups": "سونگچول",
    "scoups": "سونگچول",
    "승철": "سونگچول",
    "에스쿱스": "سونگچول",
    "エスクプス": "سونگچول",
}
_BASE_VERIFY = hardening.verify_hard_facts


def _source_emoji_failures(source: str, output: str) -> list[str]:
    src = Counter(_EMOJI_RE.findall(str(source or "")))
    out = Counter(_EMOJI_RE.findall(str(output or "")))
    failures: list[str] = []
    for emoji, count in src.items():
        missing = count - out.get(emoji, 0)
        if missing > 0:
            failures.append(f"missing source emoji: {emoji} x{missing}")
    return failures


def _metadata_line_failures(source: str, output: str) -> list[str]:
    failures: list[str] = []
    source_labels = [m.group(1).casefold() for m in _METADATA_LABEL_RE.finditer(str(source or ""))]
    for label in source_labels:
        if not re.search(rf"(?mi)^{re.escape(label)}\s*:", str(output or "")):
            failures.append(f"metadata line boundary lost: {label}")
    return failures


def verify_hard_facts(source: str, output: str, analysis=None) -> list[str]:
    failures = list(_BASE_VERIFY(source, output, analysis))
    failures.extend(_source_emoji_failures(source, output))
    failures.extend(_metadata_line_failures(source, output))
    return list(dict.fromkeys(failures))


def _canonicalize_speaker_labels(source: str, output: str) -> str:
    result = str(output or "")
    source_cf = str(source or "").casefold()
    for alias, canonical in _LABEL_CANONICAL.items():
        if alias.casefold() not in source_cf:
            continue
        result = re.sub(
            rf"(?mi)^(\s*){re.escape(alias)}\s*([:：])",
            lambda m: f"{m.group(1)}{canonical}{m.group(2)}",
            result,
        )
    return result


def _restore_metadata_linebreaks(source: str, output: str) -> str:
    result = str(output or "")
    labels = [m.group(1) for m in _METADATA_LABEL_RE.finditer(str(source or ""))]
    for label in labels:
        result = re.sub(
            rf"(?i)(?<!^)\s+(?={re.escape(label)}\s*:)",
            "\n",
            result,
        )
    return result.strip()


def _restore_metadata_labels(source: str, output: str) -> str:
    """Restore source-authoritative English metadata labels after translation."""
    result = str(output or "")
    labels = [m.group(1) for m in _METADATA_LABEL_RE.finditer(str(source or ""))]
    translated = {"fan trans": ("فن ترنس", "ترجمه فن", "ترجمهٔ فن"), "source": ("منبع",)}
    for label in labels:
        variants = translated.get(label.casefold(), ())
        for variant in variants:
            result = re.sub(
                rf"(?mi)^(\s*){re.escape(variant)}\s*[:：]",
                lambda m: f"{m.group(1)}{label}:",
                result,
            )
    return result


def _restore_speaker_labels(source: str, output: str) -> str:
    """Align generated turn labels with the exact ordered labels from SOURCE."""
    source_turns = list(runtime._SPEAKER_RE.finditer(str(source or "")))
    output_turns = list(runtime._SPEAKER_RE.finditer(str(output or "")))
    if len(source_turns) < 2 or len(source_turns) != len(output_turns):
        return str(output or "")
    result = str(output or "")
    replacements = []
    for source_match, output_match in zip(source_turns, output_turns):
        source_label = source_match.group(1).strip()
        start, end = output_match.span(1)
        replacements.append((start, end, source_label))
    for start, end, label in reversed(replacements):
        result = result[:start] + label + result[end:]
    return result


def _needs_human_polish(source: str, output: str, analysis) -> bool:
    candidate = str(output or "")
    if candidate.startswith(_MANUAL_REVIEW_PREFIX):
        return True
    if any(marker in candidate for marker in _MACHINE_PERSIAN_MARKERS):
        return True
    if verify_hard_facts(source, output, analysis):
        return True
    if analysis.source_language in {"ja"}:
        return True
    source_cf = str(source or "").casefold()
    if any(marker in source_cf for marker in ("모르겠다", "nuance", "ニュアンス", "서운하다", "ありがとね")):
        return True
    if analysis.content_type in {"SHORT_REACTION", "PHOTO_REACTION", "VIDEO_REACTION"} and any(
        marker in candidate for marker in _FORMAL_REACTION_MARKERS
    ):
        return True
    for alias, canonical in _LABEL_CANONICAL.items():
        if re.search(rf"(?mi)^\s*{re.escape(alias)}\s*[:：]", str(output or "")) and alias != canonical:
            return True
    return False


_BaseWriter = translation.ChannelStyleCaptionWriter


class ChannelStyleCaptionWriter(_BaseWriter):
    """Human-gate quality hardening shared by production and PART 4."""

    def _human_polish(self, group, current: GroupCopy) -> GroupCopy:
        client = self._client_or_none()
        if client is None:
            return current
        needs = []
        for item in group.updates:
            analysis = runtime.analyze_source(item.text)
            body = current.bodies.get(item.id, item.text)
            if _needs_human_polish(item.text, body, analysis):
                needs.append({"id": item.id, "source": item.text, "current": body, "content_type": analysis.content_type})
        if not needs:
            return current

        prompt = f"""
تو PASS 3 HUMAN QUALITY POLISH برای یک بات خصوصی فارسی هستی.
SOURCE مرجع حقیقت است. فقط کیفیت ترجمه و لحن را بهتر کن؛ هیچ fact جدیدی نساز و چیزی را حذف نکن.
قوانین سخت:
- تمام emojiهای SOURCE را با همان تعداد حفظ کن؛ ㅋㅋㅋ/ㅎㅎㅎ، URL، hashtag، quote، number/date و attribution هم دست‌نخورده بمانند.
- اگر SOURCE خط‌های metadata مثل `fan trans:` یا `source:` دارد، هرکدام در خط جدا بمانند.
- speaker turnها را prose نکن. برای label عضوهای شناخته‌شده از spelling کانال استفاده کن: Jeonghan/정한/ジョンハン → جونگهان، Joshua/조슈아/ジョシュア → جاشوآ، Seungcheol/S.Coups/승철 → سونگچول. داخل hashtag و @mention چیزی را transliterate نکن.
- reaction/social غیررسمی باید فارسی طبیعی و خودمانی باشد؛ عبارت‌های کتابی مثل «به نظر می‌رسد» یا «مورد اشاره قرار گرفت» را وقتی source خودمانی است به شکل channel-native بازنویسی کن.
- اگر CURRENT با `⚠️ نیاز به بازبینی دستی` شروع می‌شود، هشدار را در خروجی تکرار نکن؛ فقط ترجمهٔ اصلاح‌شده را برگردان.
- فارسی را با ضمیر و فعل طبیعی محاوره بنویس: «موهاشو مرتب کرد»، «هنوز چیزی خوردی؟»، «چرا یهو جاشوآ اومد وسط؟». از «او دیگر»، «خود را»، «بلافاصله»، «ناگهان اینجاست»، «از جمله» و «با استفاده از» در پست خودمانی دوری کن.
- اصطلاحات فن‌پیج را درست نگه دار: CARAT/캐럿 → کارات، Weverse → ویورس، live → لایو، mask/마스크 → ماسک، fancall → فن‌کال.
- nuance کره‌ای/ژاپنی را pragmatic ترجمه کن، نه تحت‌اللفظی عجیب. `모르겠다` یعنی «نمی‌دونم/نمی‌دونم دیگه»، نه «بلد نیستم». `ありがとね` یک «ممنون/مرسی» نرم‌تر و صمیمی‌تر است، نه «ممنون‌ها».
- توضیح داخل پرانتز یا gloss اضافه فقط وقتی SOURCE خودش چنین توضیحی دارد مجاز است.
- هیچ اطلاعاتی از حافظه/نمونه‌های تاریخی به متن اضافه نکن.
ITEMS: {json.dumps(needs, ensure_ascii=False)}
فقط JSON: {{"title":"{translation._json_escape(current.title or group.title)}","items":[{{"id":"...","body":"..."}}]}}
""".strip()
        if hasattr(self, "_generate_json_v2"):
            parsed = self._generate_json_v2(
                client,
                prompt,
                translation._group_schema(),
                temperature=0.08,
                purpose="human quality polish",
                system_instruction=(
                    "SOURCE تنها مرجع حقیقت است. ترجمهٔ فارسی طبیعی و خودمانی تولید کن. "
                    "speaker label/emoji، عدد، تاریخ، URL، hashtag، laughter و labelهای "
                    "fan trans:/source: را دقیقاً حفظ کن و تاریخ را بین تقویم‌ها تبدیل نکن."
                ),
            )
        else:
            parsed = self._generate_json(
                client,
                prompt,
                translation._group_schema(),
                temperature=0.08,
                purpose="human quality polish",
            )
        bodies = translation._parse_bodies(parsed, [item.id for item in group.updates]) if parsed else None
        if bodies is None:
            return current
        repaired = dict(current.bodies)
        for item in group.updates:
            candidate = bodies.get(item.id, repaired.get(item.id, item.text))
            candidate = re.sub(r"^⚠️ نیاز به بازبینی دستی[^\n]*\n+", "", candidate).strip()
            candidate = _canonicalize_speaker_labels(item.text, candidate)
            candidate = _restore_speaker_labels(item.text, candidate)
            candidate = _restore_metadata_labels(item.text, candidate)
            candidate = _restore_metadata_linebreaks(item.text, candidate)
            if verify_hard_facts(item.text, candidate, runtime.analyze_source(item.text)):
                continue
            remaining = [
                reason for reason in semantic_quality_failures(item, candidate)
                if reason != "low-information source needs editorial judgment"
            ]
            if remaining:
                continue
            repaired[item.id] = candidate
        self.last_diagnostics["human_quality_polish"] = "applied"
        return GroupCopy(current.title, current.category, repaired)

    def write_group(self, group, *, mode: str = "default") -> GroupCopy:
        result = super().write_group(group, mode=mode)
        result = self._human_polish(group, result)
        repaired: dict[str, str] = {}
        for item in group.updates:
            body = result.bodies.get(item.id, item.text)
            body = _canonicalize_speaker_labels(item.text, body)
            body = _restore_speaker_labels(item.text, body)
            body = _restore_metadata_labels(item.text, body)
            body = _restore_metadata_linebreaks(item.text, body)
            repaired[item.id] = body
        return GroupCopy(result.title, result.category, repaired)


hardening.verify_hard_facts = verify_hard_facts
runtime.verify_hard_facts = verify_hard_facts
translation.verify_hard_facts = verify_hard_facts
translation.ChannelStyleCaptionWriter = ChannelStyleCaptionWriter


def invalidated_resume_cursor(payload: dict) -> list[dict]:
    """Retain only the last attempted case id; never retain stale quality evidence."""
    cases = payload.get("cases", [])
    if not isinstance(cases, list) or not cases:
        return []
    latest = cases[-1]
    if not isinstance(latest, dict) or not latest.get("case_id"):
        return []
    return [
        {
            "case_id": str(latest["case_id"]),
            "output_mode": "invalidated_resume_cursor",
            "verifier_result": "INVALIDATED",
            "verifier_failures": ["production_writer_fingerprint_changed"],
        }
    ]


def _patch_cached_benchmark_resume() -> None:
    """Invalidate completed cases after writer changes while retaining the resume cursor."""
    if "tools.run_translation_benchmark_cached" not in sys.modules:
        return
    try:
        from tools import run_translation_benchmark as benchmark
    except Exception:
        return
    if getattr(benchmark, "_human_gate_fingerprint_patch", False):
        return
    original_load = benchmark._load_resume
    original_write = benchmark._write_checkpoint

    def load_resume(output_path: Path) -> list[dict]:
        if not output_path.exists():
            return []
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if payload.get("production_writer_fingerprint") != HUMAN_GATE_FINGERPRINT:
            print("PART4 resume invalidated: production writer fingerprint changed", flush=True)
            # Keep only ordering progress. Deliberately strip output/verifier data so
            # a stale case can never satisfy completed_by_id or the quality gate.
            return invalidated_resume_cursor(payload)
        return original_load(output_path)

    def write_checkpoint(output_path: Path, **kwargs):
        payload = original_write(output_path, **kwargs)
        payload["production_writer_fingerprint"] = HUMAN_GATE_FINGERPRINT
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    benchmark._load_resume = load_resume
    benchmark._write_checkpoint = write_checkpoint
    benchmark._human_gate_fingerprint_patch = True


_patch_cached_benchmark_resume()
