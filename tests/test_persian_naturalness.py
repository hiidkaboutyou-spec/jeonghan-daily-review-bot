from __future__ import annotations

from datetime import datetime, timezone

from app.channel_translation_playbook import translation_demonstrations
from app.channel_translation_v2 import DIRECT_PIPELINE_VERSION
from app.models import Update
from app.translation_safety import natural_persian_failures, semantic_quality_failures


def _update(source: str, *, update_id: str = "1") -> Update:
    return Update(
        id=update_id,
        url=f"https://x.com/source/status/{update_id}",
        author="source",
        author_name="Source",
        text=source,
        created_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )


def test_long_explanation_flags_translationese_sequence_markers():
    update = _update(
        "thread: First Jeonghan said he practiced. Then he explained he was busy. "
        "Later he read comments and promised to come back when he had more time."
    )
    output = "ابتدا جونگهان گفت تمرین کرده. سپس توضیح داد سرش شلوغ بوده. بعداً کامنت‌ها را خواند."

    failures = natural_persian_failures(update, output)

    assert "translationese sequencing or explicit-pronoun narration" in failures


def test_single_ordinary_later_marker_does_not_trigger_repair():
    update = _update(
        "thread: Jeonghan said he was busy and would explain more later after rehearsal."
    )
    output = "جونگهان گفت سرش شلوغه و بعداً بعد از تمرین بیشتر توضیح می‌ده."

    assert natural_persian_failures(update, output) == []


def test_instagram_update_flags_formal_social_translationese():
    update = _update(
        'JEONGHAN Instagram update 🪽\n"summer was here"\n📸 7 photos, including two with Joshua.'
    )
    output = "به‌روزرسانی اینستاگرام جونگهان؛ ۷ عکس، از جمله دو عکس با جاشوآ."

    failures = semantic_quality_failures(update, output)

    assert "formal social-media translationese" in failures


def test_member_interaction_flags_unnatural_collocation():
    update = _update(
        "member interaction\nJeonghan hyung used to think you were really cute, "
        "but I don't think he does anymore."
    )
    output = "جونگهان هیونگ قبلاً بانمک می‌دونستت، ولی فکر نکنم دیگه این‌طور باشه."

    failures = natural_persian_failures(update, output)

    assert "unnatural Persian collocation" in failures


def test_natural_colloquial_reaction_passes_register_gate():
    update = _update(
        "video reaction: jeonghan fixing his hair and then immediately smiling "
        "at the camera 😭 he KNOWS what he's doing"
    )
    output = "جونگهان موهاشو مرتب کرد و بعدم فوری به دوربین لبخند زد 😭 خودش دقیقاً می‌دونه داره چیکار می‌کنه"

    assert natural_persian_failures(update, output) == []


def test_official_notice_is_not_forced_into_colloquial_register():
    update = _update(
        "[NOTICE] JEONGHAN will not participate in the August 18 offline event "
        "due to a previously scheduled commitment."
    )
    output = "ابتدا اعلام شد که جونگهان به‌دلیل برنامه‌ای که از قبل تعیین شده، در رویداد حضوری ۱۸ آگوست شرکت نمی‌کند."

    assert natural_persian_failures(update, output) == []


def test_playbook_contains_persian_first_social_and_relationship_examples():
    reaction = translation_demonstrations("MEMBER_INTERACTION", "en")
    information = translation_demonstrations("INSTAGRAM_UPDATE", "en")

    assert any("قبلاً فکر می‌کرد خیلی بامزه‌ای" in item["target"] for item in reaction)
    assert any("آپدیت اینستاگرام" in item["target"] and "توی دوتاشون" in item["target"] for item in information)
    assert all("به‌روزرسانی" not in item["target"] for item in information)


def test_pipeline_version_records_natural_persian_stage():
    assert DIRECT_PIPELINE_VERSION == "channel-direct-v5-natural-persian"
