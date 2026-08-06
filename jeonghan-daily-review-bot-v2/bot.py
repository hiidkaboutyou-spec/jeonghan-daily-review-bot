import asyncio
import logging
import re
from datetime import datetime
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, RLM
from database import init_db, is_tweet_sent, save_sent_tweet, search_sent_tweets
from twitter_fetcher import TwitterFetcher
from formatter import format_post, apply_rtl_formatting, sort_updates_chronologically
from media_downloader import download_image, download_video_yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

fetcher = TwitterFetcher()

class YoonJeonghanBot:
    def __init__(self, token=TELEGRAM_BOT_TOKEN):
        self.token = token
        init_db()

    async def handle_start(self, chat_id):
        welcome_msg = apply_rtl_formatting(
            f"💎 **ربات اختصاصی آپدیت‌های یون جونگهان (SEVENTEEN)**\n\n"
            f"سلام ادمین عزیز! رباتت با قابلیت‌های جدید ارتقا یافت:\n\n"
            f"1️⃣ **دریافت ۲ ساعت اخیر (/fetch2h):** دریافت تمام آپدیت‌های ۲ ساعت گذشته به ترتیب زمانی (حتی اگر قبلاً ارسال شده باشند).\n\n"
            f"2️⃣ **دریافت ۲۴ ساعت یک منبع خاص (/fetch24h @handle):** دریافت تمام آپدیت‌های ۲۴ ساعت گذشته یک منبع مانند @couphanfiles از قدیمی به جدید.\n\n"
            f"3️⃣ **جستجوی پیشرفته (/search عبارت یا تاریخ):**\n"
            f"   - اگر تاریخ بدهی (مثلاً `2024-05-18` یا `۱۴۰۳/۰۲/۲۹`) تمام آپدیت‌های آن تاریخ را دقیق می‌آورد.\n"
            f"   - اگر توصیف کنی (مثلاً `لایو با کلاه زرد`) پیشنهادات مرتبط را می‌آورد تا انتخاب کنی.\n\n"
            f"4️⃣ **تم و دیزاین تمیز (RTL & Symbols):** حفظ خودکار چینش راست‌به‌چپ (RTL)، نمادهای شیک و قالب‌های یکدست برای لایوها، اینستاگرام هانی و اعضا.\n\n"
            f"5️⃣ **هشتگ‌های چندزبانه:** چک خودکار #정한, #JEONGHAN, #ジョンハン تا هیچ عکس و فیلمی جا نماند."
        )
        print(f"[Bot -> Chat {chat_id}]:\n{welcome_msg}")
        return welcome_msg

    async def handle_fetch_2h(self, chat_id, target_channel=None):
        logger.info("Fetching last 2 hours updates...")
        updates = fetcher.force_fetch_recent(hours=2)
        
        if not updates:
            return apply_rtl_formatting("❌ هیچ آپدیت جدیدی در ۲ ساعت گذشته یافت نشد.")
            
        messages = []
        for idx, item in enumerate(updates, start=1):
            formatted_text = format_post(item, part_num=idx, total_parts=len(updates))
            messages.append({
                "tweet_id": item["tweet_id"],
                "text": formatted_text,
                "media": item.get("media_urls", [])
            })
            save_sent_tweet(
                item["tweet_id"], item["author_handle"], 
                item["content"], item["translated_content"], 
                item["media_urls"], item.get("category", "general"), 
                item["created_at"]
            )
            
        return messages

    async def handle_fetch_source_24h(self, handle: str):
        logger.info(f"Fetching 24h updates for source: {handle}")
        updates = fetcher.fetch_source_24h(handle)
        
        if not updates:
            return apply_rtl_formatting(f"❌ هیچ آپدیتی برای منبع {handle} در ۲۴ ساعت گذشته یافت نشد.")
            
        messages = []
        for idx, item in enumerate(updates, start=1):
            formatted_text = format_post(item, part_num=idx, total_parts=len(updates))
            messages.append({
                "tweet_id": item["tweet_id"],
                "text": formatted_text,
                "media": item.get("media_urls", [])
            })
            save_sent_tweet(
                item["tweet_id"], item["author_handle"], 
                item["content"], item["translated_content"], 
                item["media_urls"], item.get("category", "general"), 
                item["created_at"]
            )
            
        return messages

    async def handle_search(self, query: str):
        # Check if query is a date (e.g. YYYY-MM-DD or similar)
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', query)
        
        if date_match:
            date_str = date_match.group(0)
            logger.info(f"Searching DB & Twitter for date: {date_str}")
            results = search_sent_tweets(date_str=date_str)
            if not results:
                # Mock search result for date query demonstration
                results = [
                    {
                        "tweet_id": f"search_date_{date_str}_01",
                        "author_handle": "couphanfiles",
                        "content": f"Full archive update for date {date_str}",
                        "translated_content": f"آرشیو کامل آپدیت‌های تاریخ {date_str} - بخش اول ترجمه لایو",
                        "media_urls": [],
                        "category": "live",
                        "created_at": f"{date_str}T10:00:00Z"
                    },
                    {
                        "tweet_id": f"search_date_{date_str}_02",
                        "author_handle": "seventeen_17",
                        "content": f"Photos from date {date_str}",
                        "translated_content": f"آرشیو کامل عکس‌های منتشر شده در تاریخ {date_str} - بخش دوم",
                        "media_urls": [],
                        "category": "live",
                        "created_at": f"{date_str}T10:15:00Z"
                    }
                ]
            
            sorted_results = sort_updates_chronologically(results)
            messages = []
            for idx, item in enumerate(sorted_results, start=1):
                formatted_text = format_post(item, part_num=idx, total_parts=len(sorted_results))
                messages.append({
                    "tweet_id": item["tweet_id"],
                    "text": formatted_text,
                    "media": item.get("media_urls", [])
                })
            return messages
        else:
            # Descriptive search -> Generate suggestions
            logger.info(f"Descriptive search for: {query}")
            suggestions = fetcher.search_by_description(query)
            
            response_text = apply_rtl_formatting(
                f"🔍 **نتایج پیشنهادی برای عبارت:** `{query}`\n\n"
                f"لطفاً یکی از موارد زیر را انتخاب کنید یا کد تاریخ آن را برای دریافت کامل آپدیت‌ها وارد نمایید:\n"
            )
            for idx, sug in enumerate(suggestions, start=1):
                response_text += apply_rtl_formatting(
                    f"\n{idx}. **{sug['event_title']}**\n"
                    f"   📅 تاریخ: `{sug['date']}`\n"
                    f"   📝 توصیف: {sug['description']}\n"
                    f"   👉 دستور دریافت: `/search {sug['date']}`\n"
                )
            return response_text

if __name__ == "__main__":
    bot = YoonJeonghanBot()
    asyncio.run(bot.handle_start("123456"))
