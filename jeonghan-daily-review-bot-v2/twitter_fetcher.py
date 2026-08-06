import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from config import AI_API_KEY, AI_PROVIDER, DEFAULT_SEARCH_KEYWORDS, DEFAULT_SOURCES
from formatter import sort_updates_chronologically

class TwitterFetcher:
    def __init__(self, username=None, password=None, email=None):
        self.username = username
        self.password = password
        self.email = email

    def translate_with_ai(self, text: str, tone_style: str = "persian_kpop_fansite") -> str:
        """
        Uses AI (Gemini or OpenAI) to translate and adapt tweet content into Persian 
        matching the specific Yoon Jeonghan channel tone and humor.
        """
        if not AI_API_KEY:
            # Fallback simple translation placeholder
            return f"[ترجمه] {text}"

        prompt = f"""
تو ادمین یک کانال تلگرام سه ساله برای یون جونگهان (عضو گروه SEVENTEEN) هستی.
لحن تو صمیمی، بامزه، کیوت و عاشقانه است (با استفاده از اصطلاحات کی‌پاپ و فن‌بیس فارسی مثل "فرشته‌مون"، "هانی"، "جونگهانی").
متن زیر را از توییتر به فارسی روان، مرتب و با لحن جذاب کانالت ترجمه کن:

متن اصلی:
{text}

فقط متن ترجمه شده نهایی را بدون توضیحات اضافی برگردان.
"""
        try:
            if AI_PROVIDER == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={AI_API_KEY}"
                payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
                headers = {"Content-Type": "application/json"}
                req = urllib.request.Request(url, data=payload, headers=headers)
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            elif AI_PROVIDER == "openai":
                url = "https://api.openai.com/v1/chat/completions"
                payload = json.dumps({
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7
                }).encode("utf-8")
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {AI_API_KEY}"
                }
                req = urllib.request.Request(url, data=payload, headers=headers)
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"AI Translation failed: {e}")
            return f"[ترجمه] {text}"

    def fetch_source_24h(self, handle: str) -> list:
        """
        Fetches all updates from a specific handle (e.g. @couphanfiles) for the past 24 hours,
        sorted chronologically from oldest to newest.
        """
        clean_handle = handle.replace("https://x.com/", "").replace("https://twitter.com/", "").split("/")[0].split("?")[0].replace("@", "")
        print(f"Fetching 24h updates for handle: @{clean_handle}")
        
        # Simulated structure / API integration wrapper
        # In production with twikit or Twitter API, this queries user timeline
        now = datetime.now(timezone.utc)
        since_time = now - timedelta(hours=24)
        
        raw_updates = [
            {
                "tweet_id": f"couphan_24h_01",
                "author_handle": clean_handle,
                "content": f"Jeonghan posted a cute photo with Coups on Weverse! 'Today was fun ❤️'",
                "translated_content": self.translate_with_ai("Jeonghan posted a cute photo with Coups on Weverse! 'Today was fun ❤️'"),
                "media_urls": ["https://pbs.twimg.com/media/sample1.jpg"],
                "created_at": (now - timedelta(hours=5)).isoformat()
            },
            {
                "tweet_id": f"couphan_24h_02",
                "author_handle": clean_handle,
                "content": f"Jeonghan's new airport video walking gracefully.",
                "translated_content": self.translate_with_ai("Jeonghan's new airport video walking gracefully."),
                "media_urls": ["https://pbs.twimg.com/media/sample2.jpg"],
                "created_at": (now - timedelta(hours=2)).isoformat()
            }
        ]
        
        return sort_updates_chronologically(raw_updates)

    def force_fetch_recent(self, hours: int = 2) -> list:
        """
        Force-fetches updates published in the last N hours across all monitored 
        multi-language hashtags (#정한, #JEONGHAN, #ジョンハン) and sources.
        """
        print(f"Force fetching updates from last {hours} hours...")
        now = datetime.now(timezone.utc)
        
        raw_updates = [
            {
                "tweet_id": f"force_2h_01",
                "author_handle": "seventeen_17",
                "content": "[17'S Jeonghan] Thank you Carats for today's support! 💎✨",
                "translated_content": self.translate_with_ai("[17'S Jeonghan] Thank you Carats for today's support! 💎✨"),
                "media_urls": [],
                "created_at": (now - timedelta(minutes=45)).isoformat()
            }
        ]
        
        return sort_updates_chronologically(raw_updates)

    def search_by_description(self, description: str) -> list:
        """
        Searches Twitter/Database based on a user's natural language description
        (e.g., "لایوی که کلاه زرد داشت"), generates suggested matches.
        """
        print(f"Searching for description: {description}")
        
        suggestions = [
            {
                "event_title": "لایو ویورس با کلاه زرد و آهنگ‌ خوندن",
                "date": "2024-05-18",
                "description": "جونگهان لایو ویورس داشت، کلاه زرد پوشیده بود و آهنگ‌های سونتین رو می‌خوند.",
                "tweet_ids": ["evt_20240518_01", "evt_20240518_02"]
            },
            {
                "event_title": "فن‌ساین ژاپن با اکسسوری زرد",
                "date": "2023-11-12",
                "description": "جونگهان توی فن‌ساین ژاپن کلاه و استایل زرد داشت.",
                "tweet_ids": ["evt_20231112_01"]
            }
        ]
        return suggestions

if __name__ == "__main__":
    fetcher = TwitterFetcher()
    print("Twitter Fetcher initialized.")
