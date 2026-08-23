# Jeonghan Daily Review Bot

بات خصوصی برای جمع‌آوری، مرتب‌سازی، ترجمه و آماده‌سازی آپدیت‌های یون جونگهان از X و ارسال آن‌ها به چت خصوصی بررسی در تلگرام. این پروژه مسیر انتشار خودکار در کانال عمومی ندارد.

## وضعیت لانچ

نسخهٔ production روی شاخهٔ `main` و فقط با GitHub Actions اجرا می‌شود؛ Render یا کارت بانکی لازم نیست. اجرای live با cron تقریباً هر پنج دقیقه انجام می‌شود و Nightly Fanfic Digest هر روز ساعت ۲۲:۰۰ تهران (`18:30 UTC`) اجرا می‌شود. loop خودکارِ بدون فاصله عمداً وجود ندارد تا سهمیهٔ Actions/X/Gemini مصرف نشود. آخرین وضعیت و لینک‌های اثبات production در [`docs/LAUNCH_STATUS.md`](docs/LAUNCH_STATUS.md) ثبت شده‌اند.

## قابلیت‌های اصلی

- اسکن ۳۲ منبع تنظیم‌شده با حالت‌های `full_feed` و `keyword_filter` و جست‌وجوی چندزبانهٔ EN/KR/JP
- replay دو ساعت اخیر، دریافت کامل ۲۴ ساعت یک منبع و جست‌وجوی آرشیو خصوصی
- گروه‌بندی chronological رویدادها و لایوها
- رسانهٔ باکیفیت با direct URL و fallbackهای yt-dlp/FFmpeg و gallery-dl اختیاری
- dedup رسانه با شناسه/هش و receiptهای پایدار
- ترجمهٔ فارسی ChannelStyle با corpus محلی 16,306 نمونه، hard fidelity checks و fallback امن
- callbackهای تلگرام با limit واقعی UTF-8 bytes؛ payloadهای بلند با token opaque و mapping خصوصی SQLite
- پیام‌های بلند بدون truncation؛ بخش‌بندی پویا با برچسب `بخش X از Y`، plan پایدار، receipt هر بخش، keyboard فقط روی بخش آخر و pacing کوتاه و rate-aware برای ادامهٔ امن بعد از failure
- handling محدود و typed برای Telegram 429/5xx/network/permanent errors
- inbox، reminder، source health و private archive SQLite
- `📚 فن‌فیک` و Nightly AO3/X digest با pagination محدود، pacing، fic-state و multipart receipts
- هیچ autopublish عمومی؛ همهٔ خروجی‌ها در review chat خصوصی می‌مانند
- برنامه‌ریزی shadow و reference-only برای بسته‌های Forward-ready؛ جزئیات و مرزهای ایمنی در [`docs/forward-ready-private-review-ux-foundation.md`](docs/forward-ready-private-review-ux-foundation.md)

## منوی تلگرام

`/start` یا `/menu` منوی ثابت را نمایش می‌دهد. دستورهای `/recent2h`, `/source24`, `/search`, `/sources`, `/fic`, `/status`, `/help` فعال‌اند.

## ساختار

- `app/` — runtime اصلی
- `config/` — sources/themes/settings و channel-style metadata
- `data/channel_style/` — corpus sharded لحن
- `tests/` — regression suite
- `tools/` — corpus/benchmark و encrypted state-backup tooling
- `docs/audit/` — گزارش forensic، matrix و runbook
- `.github/workflows/main.yml` — validation + private-review runtime
- `.github/workflows/fic-digest.yml` — nightly fic digest
- `.github/workflows/translation-benchmark.yml` — benchmark مستقل ChannelStyle
- `.state/state.json` — state JSON runtime
- `.state/private-review.sqlite3` — private archive/callback/delivery/fic state

`.state/` در Git commit نمی‌شود.

## Persistence: cache در برابر recovery backup

GitHub Actions Cache فقط برای سرعت و continuity best-effort استفاده می‌شود و database یا durable backup محسوب نمی‌شود. Runtime علاوه بر cache می‌تواند یک artifact رمزگذاری‌شدهٔ recovery بسازد.

Workflowها از `STATE_BACKUP_KEY` استفاده می‌کنند و اگر این secret وجود نداشته باشد، یک کلید پایدار و mask‌شده از توکن بات برای همان process مشتق می‌کنند. کلید اختصاصی همچنان برای استقلال از چرخش توکن توصیه می‌شود.

وقتی کلید recovery در runtime موجود باشد:

1. cacheهای JSON و SQLite ابتدا restore می‌شوند؛
2. اگر state لازم از cache موجود نباشد، workflow artifactهای غیرمنقضی `private-state-backup` را از جدیدترین به قدیمی‌تر بررسی می‌کند؛
3. هر candidate قبل از mutation با AES-256-GCM authenticate و سپس JSON/SQLite validate می‌شود؛
4. اولین backup معتبر restore می‌شود؛
5. بعد از runtime، SQLite checkpoint/quick-check می‌شود و backup جدید فقط به شکل ciphertext در artifact ذخیره می‌شود.

مقدار کلید هرگز در log چاپ نمی‌شود. چرخاندن `TELEGRAM_BOT_TOKEN` کلید مشتق‌شده را هم عوض می‌کند؛ بنابراین پیش از چرخش توکن، یک `STATE_BACKUP_KEY` اختصاصی تنظیم کنید یا از سلامت state فعلی مطمئن شوید.

فرمت کلید: base64 یک کلید تصادفی دقیقاً 32-byte. نمونهٔ تولید امن:

```bash
python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

خروجی این فرمان را فقط در GitHub Actions Secret با نام `STATE_BACKUP_KEY` قرار دهید؛ آن را داخل فایل، issue، PR یا log نگذارید.

## Secrets و variables

### Required برای اجرای واقعی main/nightly

- `TELEGRAM_BOT_TOKEN` — secret
- `TELEGRAM_ADMIN_USER_ID` — secret configuration value؛ باید positive integer باشد
- `TELEGRAM_REVIEW_CHAT_ID` — secret configuration value؛ non-zero numeric chat ID
- `X_COOKIE` — secret؛ باید حداقل `auth_token` و `ct0` داشته باشد

### Optional

- `GEMINI_API_KEY` — در نبود آن fallback فعال است
- `STATE_BACKUP_KEY` — کلید اختصاصی encrypted recovery؛ نبود آن با مشتق‌سازی پایدار از bot token پوشش داده می‌شود
- `SENTRY_DSN` — observability فنیِ scrubbed در main workflow
- `GEMINI_MODEL` — env/Actions variable override؛ default کد `gemini-3.5-flash-lite`

هیچ secret واقعی نباید در tracked file، artifact plaintext، issue یا log نوشته شود.

## Shared private SQLite

`private-review.sqlite3` شامل داده‌های private review مانند archive، callback-token mappings، multipart receipts، media-delivery identity و fic observations است. Main runtime و fic runtime از یک concurrency group استفاده می‌کنند تا همزمان این state مشترک را ننویسند.

## Telegram limits و retry behavior

- callback data بر اساس UTF-8 byte length کنترل می‌شود؛ بیش از limit به token opaque تبدیل می‌شود و truncate نمی‌شود.
- long text به‌صورت lossless split می‌شود و keyboard فقط روی part نهایی قرار می‌گیرد.
- 429 با `retry_after` و retry محدود مدیریت می‌شود؛ transient platform failures poison counter را بالا نمی‌برند.
- Telegram client-side idempotency key برای media send ندارد؛ residual crash-window در `docs/audit/KNOWN_LIMITATIONS.md` توضیح داده شده است.

## AO3 digest

AO3 از HTML public pages خوانده می‌شود؛ CI به live AO3 وابسته نیست. Search pagination حداکثر 25 page دارد، requestها paced و bounded هستند و یک صفحهٔ بدون ship واجدشرایط باعث stop اشتباه نمی‌شود. Fic work/chapter/update observations و delivery receipts در همان private SQLite نگه‌داری می‌شوند.

## Translation benchmark

Benchmark انسانی/مدلی از workflow اصلی جداست و checkpoint/resume دارد. `429 RESOURCE_EXHAUSTED` باعث bounded exit و حفظ checkpoint می‌شود؛ completed cases دوباره تولید نمی‌شوند. سبز بودن unit tests به معنی PASS شدن human-quality gate نیست.

## Validation

Workflowهای validation اجرا می‌کنند:

```bash
python -m pip check
python -m compileall -q app tests tools
python -m app --check
python -m unittest discover -s tests -p "test_*.py" -v
```

Lint/type-check جداگانه‌ای در repository configure نشده است؛ بنابراین چیزی به‌عنوان check موجود ادعا نمی‌شود.

## عملیات و forensic status

برای setup، recovery، key rotation، production verification و rollback به `docs/audit/OPERATIONS_RUNBOOK.md` مراجعه کنید. وضعیت دقیق هر capability در `docs/audit/FEATURE_VERIFICATION_MATRIX.md` ثبت شده است.
