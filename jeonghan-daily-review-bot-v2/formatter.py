import re
from datetime import datetime
from config import RLM, TEMPLATES

def apply_rtl_formatting(text: str) -> str:
    """
    Applies Right-To-Left Mark (RLM) to lines to ensure Telegram renders
    decorative symbols and Persian text in correct RTL order.
    Example:
      Input:  "،، 🐹⌕໋  ִ˒˒ متن فارسی"
      Output: "\u200F،، 🐹⌕໋  ִ˒˒ متن فارسی"
    """
    if not text:
        return ""
    
    lines = text.split('\n')
    formatted_lines = []
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            formatted_lines.append("")
            continue
            
        if not stripped.startswith(RLM):
            formatted_lines.append(f"{RLM}{stripped}")
        else:
            formatted_lines.append(stripped)
            
    return '\n'.join(formatted_lines)

def detect_category(content: str, author_handle: str = "") -> str:
    """
    Detects update type to apply matching theme/template.
    """
    content_lower = content.lower()
    
    if "live" in content_lower or "weverse" in content_lower or "لایو" in content_lower or "ترجمه" in content_lower:
        return "live"
    elif "instagram" in content_lower or "ig" in content_lower or "پست اینستا" in content_lower:
        if "jeonghan" in author_handle.lower() or "hanni" in author_handle.lower():
            return "jeonghan_ig"
        else:
            return "member_ig"
    else:
        return "general"

def format_post(tweet_data: dict, template_key: str = None, part_num: int = 1, total_parts: int = 1) -> str:
    """
    Formats a single post into aesthetic RTL Markdown with symbols and theme templates.
    """
    content_fa = tweet_data.get("translated_content") or tweet_data.get("content") or ""
    content_orig = tweet_data.get("content", "")
    author = tweet_data.get("author_handle", "")
    created_at = tweet_data.get("created_at", datetime.now().strftime("%Y-%m-%d"))
    
    if isinstance(created_at, str) and "T" in created_at:
        date_str = created_at.split("T")[0]
    else:
        date_str = str(created_at)[:10]

    if not template_key:
        template_key = detect_category(content_orig, author)
        
    tpl = TEMPLATES.get(template_key, TEMPLATES["general"])
    
    header = tpl["header"].replace("{{date}}", date_str).replace("{date}", date_str)
    
    body = tpl["body"]
    replacements = {
        "{{content}}": content_fa, "{content}": content_fa,
        "{{caption_fa}}": content_fa, "{caption_fa}": content_fa,
        "{{caption_orig}}": content_orig, "{caption_orig}": content_orig,
        "{{part_num}}": str(part_num), "{part_num}": str(part_num),
        "{{total_parts}}": str(total_parts), "{total_parts}": str(total_parts),
        "{{member_name}}": f"@{author}" if author else "عضو سونتین", "{member_name}": f"@{author}" if author else "عضو سونتین"
    }
    
    for key, val in replacements.items():
        body = body.replace(key, val)
        
    footer = tpl["footer"]
    
    full_post = f"{header}\n\n{body}\n\n{footer}"
    return apply_rtl_formatting(full_post)

def sort_updates_chronologically(updates: list) -> list:
    """
    Sorts a list of update dicts strictly from oldest to newest (created_at ASC).
    """
    def parse_time(item):
        val = item.get("created_at", "")
        if isinstance(val, datetime):
            return val
        try:
            return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        except Exception:
            return datetime.min

    return sorted(updates, key=parse_time)

if __name__ == "__main__":
    sample_tweet = {
        "content": "Jeonghan went live on Weverse! 'I ate dinner with Mingyu today.'",
        "translated_content": "جونگهان توی ویورس لایو شد! 'امروز با مینگیو شام خوردم.'",
        "author_handle": "couphanfiles",
        "created_at": "2026-08-06T08:30:00Z"
    }
    formatted = format_post(sample_tweet, "live", 1, 3)
    print("Formatted Post Output:\n")
    print(formatted)
