# Production Launch Status

آخرین به‌روزرسانی: 2026-09-25

## وضعیت canonical فعلی

- شاخهٔ production: `main`
- HEAD تأییدشده: `0e3384bee94043d159c158bd7dbcdcdc86db4fdd` (docs-only merge PR #129؛ runtime semantics از `0d7209e` تغییری نکرده)
- مرزهای privileged مربوط به GitHub Actions bot اکنون به account ID عددی پایدار `41898282` متکی‌اند، نه نام قابل‌تغییر `github-actions[bot]`.
- hardening مربوط به checkout credential isolation از PR #125 حفظ شده و workflowها credential را persist نمی‌کنند.
- push-CI مربوط به merge #123 روی main شامل Workflow Security Lint موفق بوده است.
- Watchdog واقعی روی همین canonical HEAD با event=`workflow_run` و conclusion=`success` اجرا شده است (run `35952633920`).
- این شواهد activation مرز identity را روی canonical main تأیید می‌کنند؛ برای ادعای recovery end-to-end کامل همچنان باید یک Daily recovery/re-arm واقعی که با bot actor ایجاد شده و Watchdog جانشین آن مشاهده شود ثبت شود.

## هشدار عملیاتی X

- محدودیت/چالش Cloudflare در لبهٔ `x.com` روی GitHub-hosted runner همچنان باید fail-closed تلقی شود مگر اینکه evidence جدید production خلاف آن را ثابت کند.
- failure منبع X نباید cursor را جلو ببرد یا پنجره را COMPLETE اعلام کند.
- dependency upgrade یا TLS impersonation به‌تنهایی recovery محسوب نمی‌شود؛ egress سالم باید با evidence production اثبات شود.
- انتقال X cookie/secret به سرویس یا میزبان تازه صرفاً برای دورزدن این محدودیت مجاز نیست.

## وضعیت runtime

- محیط اجرا: GitHub Actions؛ بدون Render و بدون نیاز به کارت بانکی
- اجرای دستیار: cadence زمان‌بندی‌شده با watchdog/recovery bounded؛ بدون Daily→Daily self-dispatch loop
- فیک شبانه: هر روز ساعت ۲۲:۰۰ تهران (`18:30 UTC`)
- مدل اصلی ترجمه: `gemini-3.5-flash-lite` با fallback محدود به `gemini-3.1-flash-lite`
- مقصد: فقط چت خصوصی review؛ autopublish عمومی وجود ندارد

## شواهد production مهم

- merge canonical hardening: `0d7209eb7e9d593b34d690b216fadbe8a369ca92`
- Watchdog canonical پس از merge: run `35952633920`، success، روی همان HEAD
- actor ID رسمی GitHub Actions bot که در recovery evidence مشاهده شده: `41898282`
- آخرین نمونهٔ bot-actor workflow_dispatch مشاهده‌شده پیش از merge: Watchdog run `35943130816` روی `2ba111a74a92e7720823193f3cdd671b1003f187`، actor=`github-actions[bot]`, actor id=`41898282`, success

## کنترل‌های لانچ

- workflowهای Daily و Fanfic state مشترک را با concurrency controls محافظت می‌کنند.
- Telegram hard dependency است؛ X و translation/provider health باید صریحاً healthy/degraded گزارش شوند.
- خطای quota ترجمه نباید باعث ارسال raw update یا seen شدن نادرست آیتم شود.
- media قبل از ارسال با validation موجود بررسی می‌شود و delivery/retry باید idempotent باقی بماند.
- recovery material و private review data نباید در log یا artifact plaintext افشا شوند.
- validation-only work نباید Telegram live ارسال کند یا production state را mutate کند.

## frontier بعدی

1. گیت جداسازی Fanfic از پشتهٔ Daily فقط در PR جداگانهٔ promotion از `main@0e3384b`، پس از بررسی تمام CIهای همان head، به حالت blocking برود. شواهد real-main Maintenance #164 و combined-tree Maintenance #165 در `research/stage-d-fanfic-isolation-promotion-2026-09-25.md` ثبت شده‌اند؛ PR #119 همچنان آزمایشی و غیرقابل ادغام مستقیم است.
2. پس از merge، Maintenance و Fanfic و Daily و Watchdog واقعی را روی `main` بررسی کن و سپس بسته‌شدن این گیت Stage D را اعلام کن.
3. benchmark ابزار codebase-memory-mcp در PR #131 همچنان draft و مستقل از production است؛ فقط پس از آزمایش ایزوله، دربارهٔ نصب توسعه‌دهنده تصمیم بگیر.
4. PRهای قدیمی باز (#64، #49، #48، #37، #36، #21، #18، #3) را قبل از هر استفاده دوباره با main فعلی مقایسه کن؛ باز بودن آن‌ها به معنی canonical یا merge-ready بودن نیست.
5. dependency/repository تازه فقط وقتی وارد شود که gap مشخص، license/security/maintenance/rollback و عدم افشای private data آن ثبت شده باشد.

## چک انسانی کوتاه

برای UI واقعی بات، `/start`، `/status` و `/fic` همچنان چک‌های غیرمخرب مالک هستند؛ automation جای تأیید ظاهر و دسترسی حساب واقعی را نمی‌گیرد.

جزئیات rollback، چرخش کلید و recovery در [`audit/OPERATIONS_RUNBOOK.md`](audit/OPERATIONS_RUNBOOK.md) است.
