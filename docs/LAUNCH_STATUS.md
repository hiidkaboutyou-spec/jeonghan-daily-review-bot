# Production Launch Status

آخرین به‌روزرسانی: 2026-09-25

## وضعیت canonical فعلی

- شاخهٔ production: `main`
- آخرین commit با شواهد مستقل اجرای production: `4c8b3daf19c25ec492378af2006c235f3d25c37d` (merge PR #132؛ جداسازی Fanfic در Maintenance به حالت blocking ارتقا یافت). SHA فعلی `main` را پیش از هر تغییر از GitHub بررسی کن؛ mergeهای صرفاً مستنداتی بعدی نیز HEAD را جابه‌جا می‌کنند.
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

## شواهد پس از ارتقای Stage D

- PR #132 با ۹ workflow موفق روی head دقیق `fa9c5439e2b95ccb7016115578f4c6c89cb7e50f` ادغام شد؛ قرارداد جداسازی `app.fic_digest` و audit مکمل در Maintenance اکنون blocking هستند.
- Maintenance واقعی `main` #167 (run `36134777829`): هر سه قرارداد `1 kept / 0 broken`؛ هر دو audit بازیابی و audit Fanfic بدون نقض/خطا؛ شاهد package-init بارگذاری شد. Artifact `10863490655`، digest: `sha256:9ff9d417fef529d3ebbf7ced5952b79a689c499974d18b9e081df460fba5784d`.
- Fanfic واقعی #1040 (run `36134777806`)، Daily واقعی #4359 (run `36134777746`) و Watchdog جانشین #3502 (run `36135239744` با رویداد `workflow_run`) روی همان `main@4c8b3da` همگی موفق شدند. گیت Stage D Fanfic با شواهد پس از merge بسته شده است.
- این شواهد به‌تنهایی کامل‌بودن گردآوری X را اثبات نمی‌کنند؛ هشدار X پایین همچنان معتبر است.

## frontier بعدی

1. از Stage D معماریِ تکمیل‌شده به هدف محصول برگرد: برای منابع پیکربندی‌شده، سالم/ناقص/اثبات‌نشده بودن هر پنجرهٔ X و جلوگیری از پیشروی cursor در failure را با شواهد production بررسی کن. PR تشخیصی قدیمی #64 را فقط بعد از مقایسه با `main` تازه و اطمینان از عدم افشای cookie/secret بازبینی کن.
2. benchmark ایزولهٔ `codebase-memory-mcp` در PR #131 هنوز draft است؛ فقط با نتیجهٔ برتر و rollback تأییدشده، نصب توسعه‌دهنده انجام بده. ابزارهای حافظهٔ دیگر را همزمان نصب نکن.
3. PRهای قدیمی (#49، #48، #37، #36، #21، #18، #3) را پیش از استفاده با `main` تازه مقایسه کن؛ باز ماندن آن‌ها به معنی merge-ready بودن نیست.
4. وابستگی تازه فقط پس از ثبت gap، license، security، maintenance، rollback و مرز private data اضافه شود.

## چک انسانی کوتاه

برای UI واقعی بات، `/start`، `/status` و `/fic` همچنان چک‌های غیرمخرب مالک هستند؛ automation جای تأیید ظاهر و دسترسی حساب واقعی را نمی‌گیرد.

جزئیات rollback، چرخش کلید و recovery در [`audit/OPERATIONS_RUNBOOK.md`](audit/OPERATIONS_RUNBOOK.md) است.
