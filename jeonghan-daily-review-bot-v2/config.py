import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@your_channel_username")
ADMIN_USER_IDS = [int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "0").split(",") if x.strip().isdigit()]

# AI Configuration (OpenAI or Gemini for tone adaptation & semantic search)
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini") # "gemini" or "openai"

# Twitter / X Configuration
TWITTER_USERNAME = os.getenv("TWITTER_USERNAME", "")
TWITTER_PASSWORD = os.getenv("TWITTER_PASSWORD", "")
TWITTER_EMAIL = os.getenv("TWITTER_EMAIL", "")

# Search Keywords and Multi-Language Hashtags for Yoon Jeonghan
DEFAULT_SEARCH_KEYWORDS = [
    # Korean
    "#정한", "#윤정한", "SEVENTEEN 정한", "정한",
    # English
    "#JEONGHAN", "#YoonJeonghan", "Jeonghan",
    # Japanese
    "#ジョンハン", "#ハニ", "ジョンハン"
]

DEFAULT_SOURCES = [
    "couphanfiles",
    "seventeen_17",
    "pledis_17"
]

# Formatting & Symbols Constants
RLM = "\u200F" # Right-To-Left Mark for Persian text alignment

# Default Templates
TEMPLATES = {
    "live": {
        "header": f"{RLM}،، 🐹⌕໋  ִ˒˒ 🎙️ **ترجمه لایو ویورس - {{date}}**",
        "body": f"{RLM}𖥨᩠ׄ݁⠀{{content}}\n\n{RLM}📍 **بخش {{part_num}} از {{total_parts}}**",
        "footer": f"{RLM}───────\n#Jeonghan #WeverseLive #ترجمه"
    },
    "jeonghan_ig": {
        "header": f"{RLM}،، 👼🏻⌕໋  ִ˒˒ 📸 **آپدیت اینستاگرام خود هانی**",
        "body": f"{RLM}𖥨᩠ׄ݁⠀{{caption_fa}}\n\n{RLM}💬 کپشن اصلی: `{{caption_orig}}`",
        "footer": f"{RLM}───────\n#Jeonghan #Instagram #JeonghanIG"
    },
    "member_ig": {
        "header": f"{RLM}،، 💎⌕໋  ִ˒˒ 📸 **آپدیت اینستاگرام اعضا با جونگهان**",
        "body": f"{RLM}𖥨᩠ׄ݁⠀{{caption_fa}}\n\n{RLM}👤 پست شده توسط: {{member_name}}",
        "footer": f"{RLM}───────\n#Jeonghan #Seventeen #Instagram"
    },
    "general": {
        "header": f"{RLM}،، 🐹⌕໋  ִ˒˒ 💫 **آپدیت جدید جونگهان**",
        "body": f"{RLM}𖥨᩠ׄ݁⠀{{content}}",
        "footer": f"{RLM}───────\n#Jeonghan #SVT #Update"
    }
}
