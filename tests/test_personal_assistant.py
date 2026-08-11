from app.personal_assistant import assistant_main_keyboard, parse_assistant_intent


def test_assistant_routes_recent_updates_without_commands():
    assert parse_assistant_intent("چه خبر؟").kind == "recent2h"
    assert parse_assistant_intent("آپدیت جدید چی اومده").kind == "recent2h"


def test_assistant_routes_24_hour_source_with_persian_digits():
    intent = parse_assistant_intent("۲۴ ساعت @girlsupermodel")
    assert intent.kind == "source24"
    assert intent.argument == "@girlsupermodel"


def test_assistant_routes_archive_search_and_dates():
    intent = parse_assistant_intent("پیدا کن لایوی که داشت بازی می‌کرد")
    assert intent.kind == "search"
    assert "لایوی" in intent.argument
    assert parse_assistant_intent("2026-07-14").kind == "search"


def test_assistant_routes_private_workflows():
    assert parse_assistant_intent("پیش‌نویس‌ها").kind == "inbox"
    assert parse_assistant_intent("یادآورها").kind == "reminders"
    assert parse_assistant_intent("AO3 رو بیار").kind == "fic"
    assert parse_assistant_intent("بات سالمه؟").kind == "dashboard"


def test_unknown_plain_text_becomes_safe_archive_search():
    intent = parse_assistant_intent("جونگهان با فودن")
    assert intent.kind == "search"
    assert intent.argument == "جونگهان با فودن"


def test_slash_commands_and_existing_buttons_still_delegate():
    assert parse_assistant_intent("/recent2h").kind == "delegate"
    assert parse_assistant_intent("🔎 سرچ آرشیو").kind == "delegate"


def test_assistant_keyboard_exposes_core_private_actions():
    keyboard = assistant_main_keyboard()
    labels = [button["text"] for row in keyboard["keyboard"] for button in row]
    assert "✨ دستیار من" in labels
    assert "📥 پیش‌نویس‌ها" in labels
    assert "🕑 ۲ ساعت اخیر" in labels
    assert "🗂 ۲۴ ساعت منبع" in labels
    assert "🔎 سرچ آرشیو" in labels
    assert "📚 فن‌فیک" in labels
    assert "⏰ یادآورها" in labels
    assert keyboard["is_persistent"] is True
