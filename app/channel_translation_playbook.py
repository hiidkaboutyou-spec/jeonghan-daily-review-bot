from __future__ import annotations

"""Compact, paired translation demonstrations for the channel writer.

Historical Telegram posts are excellent evidence for register and formatting, but
they are monolingual: they do not teach the model how a source sentence should be
translated.  These deliberately generic pairs teach that transformation without
authorising any current-event fact.  The live source remains the only fact source.
"""

from typing import Any


_PAIRS: dict[str, list[dict[str, str]]] = {
    "reaction": [
        {
            "source": "he fixed his hair and smiled at the camera 😭 he knows exactly what he's doing",
            "target": "موهاشو مرتب کرد و بعدم به دوربین لبخند زد 😭 خودش دقیقاً می‌دونه داره چیکار می‌کنه",
        },
        {
            "source": "no because why does he look this pretty just standing there 😭😭",
            "target": "نه آخه چرا فقط وایساده اونجا این‌قدر خوشگله 😭😭",
        },
        {
            "source": "Jeonghan with Foden? what is this crossover 😭",
            "target": "جونگهان با فودن؟ این دیگه چه کراس‌اوریه؟ 😭",
        },
        {
            "source": "it’s giving Jeonghan China bar 😭",
            "target": "خیلی وایب چاینا بار جونگهان رو می‌ده 😭",
        },
    ],
    "dialogue": [
        {
            "source": "👤: Did you eat yet?\n🪽: I ate!\n👤: Already?\n🪽: Of course ㅋㅋㅋ",
            "target": "👤: چیزی خوردی؟\n🪽: خوردم!\n👤: به این زودی؟\n🪽: معلومه ㅋㅋㅋ",
        },
        {
            "source": "팬: 벌써 끝이에요?\n🪽: 그러게, 다음에 또 봐요. 약속!",
            "target": "فن: به همین زودی تموم شد؟\n🪽: آره واقعاً، دفعهٔ بعد دوباره همدیگه رو ببینیم. قول!",
        },
    ],
    "information": [
        {
            "source": "The interview will be published in the September issue on August 20.",
            "target": "مصاحبه قراره ۲۰ آگوست توی شمارهٔ سپتامبر منتشر بشه.",
        },
        {
            "source": "Instagram update — 7 photos, including two with Joshua.",
            "target": "آپدیت اینستاگرام — ۷ تا عکس که توی دوتاشون جاشوآ هم هست.",
        },
    ],
    "explanation": [
        {
            "source": "First he said he had practiced. Later he read comments and promised to return.",
            "target": "اول گفت قبلش تمرین کرده؛ بعدتر هم کامنت‌ها رو خوند و قول داد دوباره برگرده.",
        },
        {
            "source": "‘괜찮지~’ sounds softer and more playful than a firm ‘괜찮아’.",
            "target": "«괜찮지~» نسبت به «괜찮아» نرم‌تر و بازیگوشانه‌تر به گوش می‌رسه.",
        },
    ],
    "soft_ja": [
        {
            "source": "待っていてくださって、本当にありがとうございます。",
            "target": "واقعاً ممنونم که منتظرم موندین.",
        },
        {
            "source": "『ありがとね』って言って、いつもより少し柔らかく聞こえた。",
            "target": "گفت «ありがとね» و نسبت به همیشه یکم نرم‌تر و صمیمی‌تر به گوش می‌رسید.",
        },
    ],
}


def _family(content_type: str) -> str:
    if content_type in {"LIVE_DIALOGUE", "WEVERSE_LIVE", "FANSIGN", "INTERVIEW", "MEMBER_QUOTE"}:
        return "dialogue"
    if content_type in {"PHOTO_REACTION", "VIDEO_REACTION", "SHORT_REACTION", "MEMBER_INTERACTION"}:
        return "reaction"
    if content_type in {
        "OFFICIAL_NEWS", "FACTUAL_INFORMATION", "BRAND_AD", "FASHION_EVENT",
        "AIRPORT", "MAGAZINE", "INSTAGRAM_UPDATE", "X_FANBASE_UPDATE",
    }:
        return "information"
    return "explanation"


def translation_demonstrations(content_type: str, source_language: str) -> list[dict[str, str]]:
    pairs = list(_PAIRS[_family(content_type)])
    if source_language == "ja":
        pairs = list(_PAIRS["soft_ja"]) + pairs[:1]
    # Reaction slang has more distinct pragmatic forms than factual copy. Four
    # tiny pairs are still cheaper and more reliable than a second model call.
    return pairs[:4]


def compact_style_examples(examples: list[Any], *, limit: int = 3) -> list[dict[str, Any]]:
    """Keep historical style evidence small and explicitly monolingual."""
    result: list[dict[str, Any]] = []
    for item in examples[: max(0, int(limit))]:
        text = "\n".join(line.rstrip() for line in str(item.text or "").splitlines()).strip()
        result.append(
            {
                "example_id": item.example_id,
                "persian_style_excerpt": text[:420],
                "content_type": item.content_type,
            }
        )
    return result


def unavailable_translation(source: str) -> str:
    """Fail honestly when the translation model is unavailable.

    A private review warning plus the untouched source is safer than presenting a
    broken dictionary fallback as Persian translation.
    """
    value = str(source or "").strip()
    return f"⚠️ ترجمهٔ خودکار در دسترس نبود؛ متن اصلی برای بررسی:\n\n{value}"
