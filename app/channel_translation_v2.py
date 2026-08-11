from __future__ import annotations

"""Production translation v2.

The v1 pipeline was intentionally conservative but could spend several Gemini calls
on one item (neutral -> style -> verifier -> optional polish). Under a constrained
API quota that frequently degraded real bot output to the old generic translation
fallback.  V2 makes the normal path one source-grounded, style-aware generation and
keeps a second model call only for items that fail deterministic fidelity checks.

Entity spelling is deterministic and SOURCE-authorized. Historical channel data is
style authority only and can never authorize a person/fact that is absent from the
current source.
"""

import json
import logging
import re
from typing import Any

from .ai import CaptionWriter as LegacyCaptionWriter, GroupCopy, gemini_should_try_next_model
from .channel_quality import commentary_policy, language_guidance, rerank_for_mode
from . import channel_translation as v1
from .channel_style_runtime import (
    CHANNEL_STYLE_VERSION,
    PROMPT_TEMPLATE_VERSION,
    analyze_source,
    is_trivial_source,
    legacy_category_to_content_type,
    verify_hard_facts,
)
from .models import EventGroup
from .translation_safety import semantic_quality_failures
from .channel_translation_playbook import (
    compact_style_examples,
    translation_demonstrations,
    unavailable_translation,
)

logger = logging.getLogger(__name__)

DIRECT_PIPELINE_VERSION = "channel-direct-v4-emotional-fidelity"

EMOTIONAL_FIDELITY_RULES = (
    "اول تشخیص بده نویسنده دقیقاً چه حسی دارد: هیجان، ناباوری، شوخی، طعنه، حرص، "
    "ذوق، لطافت، نگرانی یا لحن خبری. همان حس و همان شدت را منتقل کن؛ نه ضعیفش کن، "
    "نه از طرف کانال هیجان/قضاوت تازه اضافه کن. اصطلاحات اینترنتی را کارکردی ترجمه کن: "
    "`it's giving X` معمولاً «وایب X رو می‌ده»، `what is this crossover` معمولاً "
    "«این دیگه چه کراس‌اوریه؟»، و `no because` در reaction معمولاً «نه آخه...» است. "
    "CAPS، تکرار، مکث، کشیدگی، سؤال بلاغی و جملهٔ نصفه بخشی از حس‌اند؛ در فارسی "
    "طبیعی معادلشان را نگه دار، اما چیزی را که در SOURCE نیست نساز."
)

# This is an editorial spelling rule requested by the channel owner, not a fact
# learned from historical posts. It is applied only when the CURRENT SOURCE names
# Jeonghan, so it cannot introduce him into unrelated posts.
_JEONGHAN_SOURCE_RE = re.compile(
    r"(?i)(?<![\w#@])(?:yoon\s+jeonghan|jeonghan|윤정한|정한|ジョンハン)(?![\w])"
)
_JEONGHAN_OUTPUT_VARIANTS_RE = re.compile(
    r"(?<![#@\w\u200c])(?:"
    r"جونگهان|جونگ‌هان|جونگان|جئونگهان|جئونگان|جیونگهان|جیونگ‌هان|جنگهان|جنگ‌هان"
    r")(?![\w\u200c])"
)
_URL_OR_TAG_RE = re.compile(r"https?://\S+|[#@][\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+")


def source_names_jeonghan(source: str) -> bool:
    """True only for a prose/name occurrence, not a hashtag or @mention."""
    protected_spans = [(m.start(), m.end()) for m in _URL_OR_TAG_RE.finditer(str(source or ""))]
    for match in _JEONGHAN_SOURCE_RE.finditer(str(source or "")):
        if not any(start <= match.start() < end for start, end in protected_spans):
            return True
    return False


def canonicalize_jeonghan(source: str, output: str) -> str:
    """Force the channel's Persian spelling when SOURCE itself names Jeonghan.

    URLs, hashtags and @mentions are left byte-for-byte alone.
    """
    result = str(output or "")
    if not source_names_jeonghan(source):
        return result

    pieces: list[str] = []
    cursor = 0
    for match in _URL_OR_TAG_RE.finditer(result):
        pieces.append(_JEONGHAN_OUTPUT_VARIANTS_RE.sub("جونگهان", result[cursor:match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(_JEONGHAN_OUTPUT_VARIANTS_RE.sub("جونگهان", result[cursor:]))
    return "".join(pieces)


def entity_failures(source: str, output: str) -> list[str]:
    if source_names_jeonghan(source) and not re.search(
        r"(?<![#@\w\u200c])جونگهان(?![\w\u200c])", str(output or "")
    ):
        return ["missing canonical source entity: جونگهان"]
    return []


def _canonicalize_group(group: EventGroup, copy: GroupCopy) -> GroupCopy:
    return GroupCopy(
        title=canonicalize_jeonghan("\n".join(item.text for item in group.updates), copy.title),
        category=copy.category,
        bodies={
            item.id: canonicalize_jeonghan(item.text, copy.bodies.get(item.id, item.text))
            for item in group.updates
        },
    )


class SafeLegacyCaptionWriter(LegacyCaptionWriter):
    """Legacy compatibility writer with deterministic channel entity spelling."""

    def write_group(self, group: EventGroup, *, mode: str = "default") -> GroupCopy:
        return _canonicalize_group(group, super().write_group(group, mode=mode))


_BaseWriter = v1.ChannelStyleCaptionWriter


class ChannelStyleCaptionWriter(_BaseWriter):
    """One-call normal production translation with retrieval-grounded channel style."""

    def write_group(self, group: EventGroup, *, mode: str = "default") -> GroupCopy:
        source_text = "\n".join(item.translation_source() for item in group.updates)
        try:
            analysis = analyze_source(
                source_text,
                hinted_content_type=legacy_category_to_content_type(group.category, source_text),
            )
        except Exception as exc:
            logger.error("V2 source analysis failed; using hardened v1: %s", v1._safe_error(exc))
            return _canonicalize_group(group, super().write_group(group, mode=mode))

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
            return _canonicalize_group(group, super().write_group(group, mode=mode))

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
            self.last_diagnostics["fallback"] = "gemini_unavailable_hardened_v1"
            return _canonicalize_group(group, super().write_group(group, mode=mode))

        direct = self._direct_group(group, analysis, examples, glossary, mode, client)
        if direct is None:
            # Do not immediately run the full multi-call v1 pipeline after an API
            # failure. Its deterministic/legacy fallback is safer for quota and the
            # entity spelling is repaired before delivery.
            self.last_diagnostics["fallback"] = "direct_generation_unavailable"
            fallback = GroupCopy(
                title=group.title,
                category=group.category,
                bodies={item.id: unavailable_translation(item.text) for item in group.updates},
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
            still_bad = []
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

        # Per-item fail closed: keep good direct translations and replace only the
        # unsafe items with a source-preserving translation fallback.
        bodies = dict(direct.bodies)
        for item in group.updates:
            if item.id in failed_ids:
                bodies[item.id] = canonicalize_jeonghan(
                    item.text, v1._translate_preserving_structure(item.text)
                )
        self.last_diagnostics["output_mode"] = "styled_direct_partial_fallback"
        return GroupCopy(direct.title or group.title, group.category, bodies)

    def _direct_group(self, group, analysis, examples, glossary, mode, client) -> GroupCopy | None:
        mode_rule = {
            "default": "طبیعی، عامیانه و دقیق مثل پست‌های واقعی همین کانال؛ نه رسمی و نه ترجمه‌ماشینی.",
            "funnier": "فقط اگر خود منبع جا می‌دهد، بامزه‌تر و شیطون‌تر؛ بدون ساختن شوخی یا fact.",
            "softer": "نرم‌تر و صمیمی‌تر، بدون شیرینی مصنوعی یا تغییر معنی.",
            "precise": "نزدیک‌تر به منبع و کم‌حاشیه‌تر، اما همچنان فارسی طبیعی کانال.",
        }.get(mode, "طبیعی و دقیق مثل همین کانال.")

        source_items = [
            {
                "id": item.id,
                "author": item.author,
                "text": item.translation_source(),
                "language": item.lang,
                "url": item.url,
                "media": [{"kind": media.kind, "url": media.url} for media in item.media],
                "quoted_media": [
                    {"kind": media.kind, "url": media.url} for media in item.quoted_media
                ],
            }
            for item in group.updates
        ]
        compact_profile = {
            key: self.memory.profile.get(key)
            for key in ("register", "syntax", "lexicon", "emotion", "formatting", "code_switching", "dialogue", "explanation")
            if self.memory.profile.get(key) is not None
        }
        paired_examples = translation_demonstrations(
            analysis.content_type, analysis.source_language
        )
        historical_style = compact_style_examples(examples, limit=3)
        canonical_entities = []
        if source_names_jeonghan("\n".join(item.translation_source() for item in group.updates)):
            canonical_entities.append(
                {
                    "source_forms": ["Jeonghan", "Yoon Jeonghan", "정한", "윤정한", "ジョンハン"],
                    "required_persian": "جونگهان",
                    "rule": "در متن فارسی همیشه دقیقاً همین املاء؛ hashtag/URL را تغییر نده",
                }
            )

        system_instruction = (
            "تو مترجم و ویراستار فارسی یک کانال فن‌پیج هستی. SOURCE تنها مرجع حقیقت است و "
            "نمونه‌های تاریخی فقط مرجع لحن‌اند. خروجی باید فارسی روان، عامیانه و طبیعی باشد؛ "
            "نه فارسی کتابی، نه ساختار انگلیسی/کره‌ای/ژاپنی با کلمات فارسی. هیچ نکته، speaker، "
            "اسم، عدد، تاریخ، URL، hashtag، emoji، laughter یا nuance معناداری را حذف یا اختراع نکن. "
            "قواعد CANONICAL ENTITIES قطعی‌اند و از ترجمه آوایی مدل مهم‌ترند. فقط دادهٔ خواسته‌شده "
            "در schema را برگردان و هیچ مقدمه‌ای ننویس. در همان یک پاسخ، اول معنی دقیق را "
            "در ذهنت استخراج کن، بعد آن را به فارسی طبیعی کانال تبدیل کن و در پایان با SOURCE "
            "تطبیق بده؛ پیش‌نویس یا مراحل بررسی را در خروجی ننویس. "
            + EMOTIONAL_FIDELITY_RULES
        )
        prompt = f"""
SOURCE ITEMS:
{json.dumps(source_items, ensure_ascii=False)}

CURRENT SOURCE FACT LEDGER:
{json.dumps(analysis.fact_ledger(), ensure_ascii=False)}

CANONICAL ENTITIES FOR THIS SOURCE ONLY:
{json.dumps(canonical_entities, ensure_ascii=False)}

RELEVANT CHANNEL GLOSSARY (terminology/spelling only, never factual evidence):
{json.dumps(glossary, ensure_ascii=False)}

CHANNEL STYLE DNA:
{json.dumps(compact_profile, ensure_ascii=False)}

PAIRED TRANSLATION DEMONSTRATIONS (learn the source→natural-Persian transformation; facts are fictional):
{json.dumps(paired_examples, ensure_ascii=False)}

HISTORICAL CHANNEL EXCERPTS (monolingual Persian style evidence only; never copy facts):
{json.dumps(historical_style, ensure_ascii=False)}

TRANSLATION REQUIREMENTS:
- {mode_rule}
- لحن کانال فقط روش بیان فارسی است؛ احساس و موضع باید متعلق به نویسندهٔ SOURCE بماند.
- متن را خلاصه نکن. همهٔ نسبت‌ها، علت‌ها، کنایه‌ها، شوخی‌ها و شدت احساس را منتقل کن.
- برای reactionهای کوتاه، کوتاهی و ضرباهنگ را نگه دار؛ آن‌ها را به جملهٔ رسمی و توضیحی تبدیل نکن.
- `I/you/he/she/they` را فقط به اندازه‌ای مشخص کن که خود SOURCE یا quoted context مشخص کرده؛ حدس نزن.
- اگر متن با حروف بزرگ نوشته شده، انرژی آن را با فارسی طبیعی منتقل کن، نه با فارسی کتابی یا علامت تعجب اضافهٔ بی‌دلیل.
- اگر [QUOTED POST] وجود دارد، متن اصلی و quoted post را جدا نگه دار و هر دو را با توجه به رابطه‌شان ترجمه کن.
- {EMOTIONAL_FIDELITY_RULES}
- {language_guidance(analysis.source_language, analysis.content_type)}
- {commentary_policy(analysis.content_type)}
- content type = {analysis.content_type}
- اگر SOURCE چند speaker دارد، هر turn را جدا و به همان ترتیب نگه دار.
- label هر speaker، مخصوصاً emojiهایی مثل 🍒/🪽/🐶، باید عیناً و در ابتدای همان turn بماند؛ آن را عوض یا جابه‌جا نکن.
- ㅋㅋㅋ/ㅎㅎㅎ و emojiهای منبع را همان تعداد حفظ کن مگر خود source معنای دیگری بدهد.
- تاریخ را به تقویم دیگری تبدیل نکن؛ مثلاً 8월 20일 باید «۲۰ آگوست» بماند، نه تاریخ شمسی معادل آن.
- labelهای metadata مثل `fan trans:` و `source:` را دقیقاً با همان حروف انگلیسی و هرکدام در خط جدا نگه دار؛ فقط متن بعد از label را ترجمه کن.
- توضیح مترجمی داخل پرانتز نساز مگر واقعاً برای انتقال nuance ضروری باشد.
- هیچ header/symbol/source line عمومی اضافه نکن؛ آن‌ها بعداً توسط ThemeEngine اضافه می‌شوند.
- فعل و ضمیر را متناسب با نوع متن انتخاب کن: reaction/dialogue خودمانی است («موهاشو»، «چیزی خوردی؟»، «چیکار می‌کنه»)، اما اطلاعیه رسمی روشن و بی‌اغراق می‌ماند.
- ترجمهٔ تحت‌اللفظی ممنوع: `including two with X` یعنی «که توی دوتاشون X هم هست»، `did you eat yet?` یعنی «چیزی خوردی؟»، و `already?` با توجه به بافت «به همین زودی؟/الان؟» است، نه «قبلاً؟».
- نام برند رسمی و عنوان رسمی آهنگ/challenge را ترجمه نکن؛ URL، hashtag و username را عیناً نگه دار.
- متن داخل [QUOTED POST] فقط با همان attribution، برای فهم زمینه و ترجمهٔ دقیق استفاده شود.
""".strip()
        parsed = self._generate_json_v2(
            client,
            prompt,
            v1._group_schema(),
            temperature=0.18 if mode in {"default", "precise"} else 0.28,
            purpose=f"direct channel translation/{mode}",
            system_instruction=system_instruction,
        )
        bodies = v1._parse_bodies(parsed, [item.id for item in group.updates]) if parsed else None
        if bodies is None:
            return None
        return GroupCopy(str((parsed or {}).get("title") or group.title).strip(), group.category, bodies)

    def _repair_failed_items(self, group, direct, failed_ids, analysis, client) -> GroupCopy | None:
        payload = [
            {
                "id": item.id,
                "source": item.translation_source(),
                "candidate": direct.bodies.get(item.id, ""),
                "quality_failures": semantic_quality_failures(
                    item, direct.bodies.get(item.id, "")
                ),
                "canonical_jeonghan": "جونگهان" if source_names_jeonghan(item.text) else None,
            }
            for item in group.updates
            if item.id in failed_ids
        ]
        system_instruction = (
            "تو فقط خطاهای fidelity ترجمه فارسی را تعمیر می‌کنی. SOURCE مرجع حقیقت است. "
            "معنی و لحن درست موجود را بی‌دلیل بازنویسی نکن. اسم Jeonghan/정한/ジョンハン در متن فارسی "
            "اگر در SOURCE آمده باید دقیقاً «جونگهان» باشد. URL/hashtag/emoji/laughter/عدد/speaker را حفظ کن."
            " label هر speaker/emoji و labelهای انگلیسی fan trans:/source: را عیناً و در همان خط حفظ کن. "
            "تاریخ را بین تقویم‌ها تبدیل نکن؛ 8월 20일 یعنی ۲۰ آگوست، نه ۳۰ مرداد. "
            "اگر quality_failures لحن کتابی یا ماشینی را نشان می‌دهد، جمله را به فارسی طبیعی و عامیانهٔ "
            "فن‌پیج تبدیل کن؛ ساختار انگلیسی را با کلمات فارسی تکرار نکن."
        )
        prompt = "FAILED ITEMS:\n" + json.dumps(payload, ensure_ascii=False)
        parsed = self._generate_json_v2(
            client,
            prompt,
            v1._group_schema(),
            temperature=0.02,
            purpose="direct fidelity repair",
            system_instruction=system_instruction,
        )
        repaired_only = v1._parse_bodies(parsed, failed_ids) if parsed else None
        if repaired_only is None:
            return None
        bodies = dict(direct.bodies)
        bodies.update(repaired_only)
        return GroupCopy(direct.title or group.title, group.category, bodies)

    def _generate_json_v2(
        self,
        client,
        prompt: str,
        schema: dict[str, Any],
        *,
        temperature: float,
        purpose: str,
        system_instruction: str,
    ) -> dict[str, Any] | None:
        try:
            from google.genai import types
        except Exception as exc:
            logger.warning("Gemini types unavailable for %s: %s", purpose, v1._safe_error(exc))
            return None

        # Use the configured production model first. Keep the candidate behavior for
        # service resilience, but never make quality depend on a different model.
        for model in self._model_candidates():
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=temperature,
                        response_mime_type="application/json",
                        response_json_schema=schema,
                    ),
                )
                parsed = json.loads(response.text or "{}")
                if isinstance(parsed, dict):
                    return parsed
            except Exception as exc:
                logger.warning("Gemini %s model %s failed: %s", purpose, model, v1._safe_error(exc))
                if not gemini_should_try_next_model(exc):
                    break
        return None
