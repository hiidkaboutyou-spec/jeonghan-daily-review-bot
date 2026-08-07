# Jeonghan Daily Review Bot

بات خصوصی برای جمع‌آوری، مرتب‌سازی و آماده‌سازی آپدیت‌های یون جونگهان از X و ارسال آن‌ها به چت خصوصی بررسی در تلگرام.

## قابلیت‌های فعلی

- اسکن ۲۲ منبع تنظیم‌شده در `config/sources.json`
- سرچ جداگانهٔ نام/هشتگ جونگهان به انگلیسی، کره‌ای و ژاپنی برای recall بهتر
- فیلتر سخت‌گیرانهٔ محتوای فروش/خرید/ترید، فوتوکارت، آلبوم و پست‌های نامرتبط
- تشخیص تفاوت اکانت‌های اختصاصی جونگهان با اکانت‌های عمومی/شیپ؛ منابع عمومی فقط وقتی وارد دیلی می‌شوند که خود پست واقعاً به جونگهان مربوط باشد
- `🕑 ۲ ساعت اخیر`: بازفرستادن تمام موارد دو ساعت گذشته حتی اگر قبلاً دیده شده باشند
- `🗂 ۲۴ ساعت منبع`: دریافت موارد مرتبط یک منبع از قدیمی‌ترین به جدیدترین
- `🔎 سرچ آرشیو`: سرچ با تاریخ یا توضیح آزاد، پیشنهاد حداکثر ۸ رویداد و سپس جمع‌آوری thread/event انتخاب‌شده
- گروه‌بندی و ترتیب chronological برای لایوها و رویدادها
- عکس original و انتخاب بهترین MP4 موجود؛ fallback با yt-dlp و FFmpeg برای ویدئو
- ترجمه و کپشن فارسی با Gemini + fallback ترجمه در صورت خطای مدل/Quota
- حافظهٔ لحن از `data/channel_memory.jsonl` و `data/channel_voice_profile.json`
- تم‌های ثابت و RTL صحیح برای سیمبل‌های فارسی
- منوی ثابت تلگرام با دکمه‌های ضروری
- `📚 فن‌فیک`: اجرای فوری دو لیست جدا از پیشنهادهای X و بهترین‌های AO3
- Nightly Fanfic Digest هر شب حدود ساعت ۲۲ تهران؛ English-only، دسته‌بندی بر اساس ship، لینک و خلاصهٔ فارسی
- هیچ انتشار خودکار در کانال عمومی؛ خروجی ابتدا در review chat می‌آید

## منوی تلگرام

- `🕑 ۲ ساعت اخیر`
- `🗂 ۲۴ ساعت منبع`
- `🔎 سرچ آرشیو`
- `📚 فن‌فیک`
- `📋 وضعیت`
- `❔ راهنما`

`/start` یا `/menu` منوی ثابت را نمایش می‌دهد. دستورهای `/recent2h`, `/source24`, `/search`, `/sources`, `/fic`, `/status`, `/help` نیز فعال‌اند.

## ساختار اصلی

- `app/` — کد اصلی
- `config/sources.json` — منابع، اولویت‌ها و keywordها
- `config/themes.json` — تم‌ها و سیمبل‌ها
- `config/settings.json` — تنظیمات runtime
- `data/` — حافظه و پروفایل لحن
- `tests/` — تست‌های داخلی
- `.github/workflows/main.yml` — Validation + اجرای بات
- `.github/workflows/fic-digest.yml` — فن‌فیک شبانه
- `.state/` — state runtime که در GitHub Actions Cache نگه‌داری می‌شود و در Git commit نمی‌شود

## Secrets

این مقادیر فقط در `Settings → Secrets and variables → Actions` نگه‌داری شوند:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN_USER_ID`
- `TELEGRAM_REVIEW_CHAT_ID`
- `X_COOKIE`
- `GEMINI_API_KEY`

هیچ Token/Cookie واقعی نباید داخل فایل‌های ریپو نوشته شود.

## Validation

قبل از هر اجرای واقعی و بعد از تغییر کد، Workflow این موارد را بررسی می‌کند:

```bash
python -m compileall -q app tests
python -m app --check
python -m unittest discover -s tests -p "test_*.py" -v
```

Validation روی push از اجرای طولانی بات جداست، بنابراین تغییرات جدید پشت پنجرهٔ live در صف نمی‌مانند.

## اضافه‌کردن منبع

هر منبع در `config/sources.json` یک فیلد `jeonghan_only` دارد:

```json
{
  "handle": "username_without_at",
  "label": "نام نمایشی",
  "enabled": true,
  "priority": 30,
  "include_replies": true,
  "jeonghan_only": false
}
```

`jeonghan_only: true` فقط برای اکانتی استفاده شود که عملاً مختص جونگهان است. برای اکانت‌های گروه، شیپ یا چندعضوی مقدار `false` بماند تا relevance filter سخت‌گیرانه اعمال شود.
