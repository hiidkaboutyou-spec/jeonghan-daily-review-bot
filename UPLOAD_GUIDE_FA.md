# راهنمای خیلی سادهٔ نصب نسخهٔ 4

در این بسته فقط یک پوشهٔ اصلی می‌بینی: `jeonghan-daily-review-bot-v2`.
لازم نیست فایل مخفی یا پوشهٔ `.github` بسازی؛ Workflow فعلی Repository درست است.

1. ZIP را Extract کن.
2. در GitHub وارد صفحهٔ اصلی Repository `jeonghan-daily-review-bot` شو.
3. `Add file → Upload files` را بزن.
4. پوشهٔ کامل `jeonghan-daily-review-bot-v2` را از Finder داخل کادر GitHub بکش.
5. صبر کن فهرست فایل‌ها کامل شود. مسیر جدید `jeonghan-daily-review-bot-v2/src/organizer.py` باید در فهرست باشد.
6. پایین صفحه روی `Commit changes` بزن.
7. وارد `Actions → Jeonghan Daily Review Bot → Run workflow` شو و اول `mode: check` را اجرا کن.
8. وقتی سبز شد، دوباره `Run workflow` را با `mode: live` اجرا کن.

Secretهای قبلی، Workflow و وضعیت ذخیره‌شدهٔ بات با این کار پاک نمی‌شوند. هیچ پوشه‌ای را دستی حذف نکن.

بعد از سبز شدن اجرای `live`، داخل چت خصوصی بات `/start` را بفرست. در اجرای بعدی این چهار دکمه می‌آیند:

- `🕑 همهٔ دو ساعت اخیر`
- `🔎 جست‌وجوی آرشیو`
- `🗂 انتخاب منبع ۲۴ساعته`
- `📋 صف درخواست‌ها`

بات هنوز Review-only است: هیچ‌چیز را خودکار داخل کانال نمی‌فرستد و همهٔ خروجی‌ها اول به چت خصوصی تو می‌آیند.
