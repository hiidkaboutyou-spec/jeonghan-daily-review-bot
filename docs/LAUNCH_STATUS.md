# Production Launch Status

آخرین به‌روزرسانی: 2026-08-12

## وضعیت

- محیط اجرا: GitHub Actions؛ بدون Render و بدون نیاز به کارت بانکی
- شاخهٔ production: `main`
- اجرای دستیار: cron تقریباً هر پنج دقیقه، بدون self-dispatch loop و با یک quiet window کوتاه اطراف nightly
- فیک شبانه: هر روز ساعت ۲۲:۰۰ تهران (`18:30 UTC`)
- مدل اصلی ترجمه: `gemini-3.1-flash-lite`
- مقصد: فقط چت خصوصی review؛ autopublish عمومی وجود ندارد

## شواهد واقعی production

- [اجرای کامل Daily روی main](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/31530929770): Telegram، X و Gemini سالم؛ ChannelStyle primary با 16,306 نمونه؛ encrypted recovery artifact ساخته و آپلود شد.
- [اجرای کامل Nightly Fanfic روی main](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/31531144986): `x=13`، `ao3_pool=48` و `ao3_list=36`؛ تحویل هر دو لیست X و AO3 تأیید شد.
- [آخرین اصلاح پایداری و delivery](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/commit/878da58df1c14dfca645659bba2ad3fdc8d8c244): delivery فیک به revision منتشرشده متصل شد تا retry همان نسخه تکراری نفرستد.

## کنترل‌های لانچ

- workflowهای Daily و Fanfic از یک concurrency group استفاده می‌کنند و state مشترک را هم‌زمان نمی‌نویسند.
- اجرای موفق فیک همان روز مانع auto-dispatch تکراری بعد از commitهای مستنداتی می‌شود.
- Telegram hard dependency است؛ X و Gemini در preflight به‌صورت صریح سالم یا degraded گزارش می‌شوند.
- خطای quota ترجمه یک deadline تصاعدی و پایدار می‌سازد؛ آپدیت خام ارسال/seen نمی‌شود و تا بازشدن سهمیه در صف می‌ماند.
- هر ویدیوی X پیش از ارسال با `ffprobe` اعتبارسنجی و به MP4 سازگار با Telegram/iOS و `faststart` نهایی می‌شود؛ duration، ابعاد و thumbnail صریح همراه `sendVideo` فرستاده می‌شوند و کش ویدیوهای ناسالم قدیمی versioned شده است.
- کلید recovery در log mask می‌شود. اگر secret اختصاصی وجود نداشته باشد، workflow یک کلید پایدار از bot token مشتق می‌کند.
- ciphertext artifact روی cadence محدود و با retention سه‌روزه ذخیره می‌شود؛ plaintext state آپلود نمی‌شود.

## چک انسانی کوتاه

پس از باز کردن لینک عمومی بات، این فرمان‌های غیرمخرب را بفرستید: `/start`، `/status` و `/fic`. این مرحله ظاهر منو و دسترسی حساب خودتان را تأیید می‌کند؛ هیچ تست خودکار نمی‌تواند جای لمس واقعی UI با اکانت مالک را بگیرد.

جزئیات rollback، چرخش کلید و recovery در [`audit/OPERATIONS_RUNBOOK.md`](audit/OPERATIONS_RUNBOOK.md) است.
