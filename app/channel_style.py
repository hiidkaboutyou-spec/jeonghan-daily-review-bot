from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

CHANNEL_STYLE_VERSION = 1
CORPUS_FORMAT_VERSION = 1
PROMPT_TEMPLATE_VERSION = 1

PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
KOREAN_RE = re.compile(r"[\uac00-\ud7af]")
JAPANESE_RE = re.compile(r"[\u3040-\u30ff]")
LATIN_RE = re.compile(r"[A-Za-z]")
TOKEN_RE = re.compile(r"[0-9A-Za-z_\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+")
URL_RE = re.compile(r"https?://\S+")
HASHTAG_RE = re.compile(r"#[\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+", re.UNICODE)
NUMBER_RE = re.compile(r"(?<!\w)[+\-]?(?:\d[\d,.:/\-]*\d|\d)(?!\w)")
LAUGHTER_RE = re.compile(r"(?:ㅋ{2,}|ㅎ{2,}|(?:lol|lmao|lmfao)\b|خ{2,}|ه{3,})", re.I)
SPEAKER_LINE_RE = re.compile(r"^\s*([^\s:：]{1,20})\s*[:：]\s*(.+)$", re.M)

CONTENT_TYPES = (
    "LIVE_DIALOGUE", "WEVERSE_POST", "WEVERSE_LIVE", "FANSIGN", "INTERVIEW",
    "MAGAZINE", "OFFICIAL_NEWS", "BRAND_AD", "FASHION_EVENT", "AIRPORT",
    "INSTAGRAM_UPDATE", "X_FANBASE_UPDATE", "FAN_ACCOUNT_OR_OP_STORY",
    "PHOTO_REACTION", "VIDEO_REACTION", "MEMBER_QUOTE", "MEMBER_INTERACTION",
    "KOREAN_LANGUAGE_NUANCE", "JAPANESE_LANGUAGE_NUANCE", "WORDPLAY",
    "THREAD_OR_LONG_EXPLANATION", "SHORT_REACTION", "FACTUAL_INFORMATION",
    "FANFIC_UPDATE", "OTHER",
)

@dataclass(slots=True)
class SourceAnalysis:
    source_language: str
    content_type: str
    numbers: list[str]
    urls: list[str]
    hashtags: list[str]
    laughter: list[str]
    speakers: list[str]
    names_and_terms: list[str]
    uncertain_items: list[str]
    line_count: int
    char_count: int
    has_dialogue: bool
    platform: str

    def fact_ledger(self) -> dict[str, Any]:
        return {
            "source_language": self.source_language,
            "content_type": self.content_type,
            "numbers": self.numbers,
            "urls": self.urls,
            "hashtags": self.hashtags,
            "laughter": self.laughter,
            "speakers": self.speakers,
            "names_and_terms": self.names_and_terms,
            "uncertain_items": self.uncertain_items,
        }

@dataclass(slots=True)
class RetrievedStyleExample:
    example_id: str
    text: str
    content_type: str
    source_language: str
    date: str
    score: float
    reasons: list[str]

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "example_id": self.example_id,
            "text": self.text,
            "content_type": self.content_type,
            "source_language": self.source_language,
            "style_score": round(self.score, 4),
            "why_retrieved": self.reasons,
        }

class _CountProxy:
    def __init__(self, count: int): self.count = max(0, int(count))
    def __len__(self) -> int: return self.count
    def __bool__(self) -> bool: return self.count > 0

class ChannelStyleMemory:
    """Versioned private channel-style memory backed by SQLite FTS5.

    Historical date is metadata only. Every stable-ID unique historical textual
    message has equal base style authority (1.0); retrieval uses content/format/FTS,
    never chronology or recency.
    """
    def __init__(self, root: Path, db_path: Path | None = None):
        self.root = Path(root)
        self.profile_path = self.root / "config" / "channel_style_profile.json"
        self.glossary_path = self.root / "config" / "channel_glossary.json"
        self.corpus_path = self.root / "data" / "channel_style_examples.jsonl.gz"
        self.corpus_dir = self.root / "data" / "channel_style_examples"
        self.profile = self._load_json(self.profile_path)
        self.glossary = self._load_json(self.glossary_path)
        self.db_path = Path(db_path) if db_path is not None else self.root / ".state" / "private-review.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        self._ensure_corpus_index()
        self.sample_count = self._count_examples()
        self.samples = _CountProxy(self.sample_count)

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try: value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError): return {}
        return value if isinstance(value, dict) else {}

    def _init_schema(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS channel_style_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS channel_style_examples(
            example_id TEXT PRIMARY KEY,channel_id TEXT NOT NULL,message_id TEXT NOT NULL,text TEXT NOT NULL,
            content_type TEXT NOT NULL,source_language TEXT NOT NULL,date TEXT NOT NULL DEFAULT '',platform TEXT NOT NULL DEFAULT '',
            line_count INTEGER NOT NULL DEFAULT 1,char_count INTEGER NOT NULL DEFAULT 0,has_dialogue INTEGER NOT NULL DEFAULT 0,
            has_laughter INTEGER NOT NULL DEFAULT 0,has_media INTEGER NOT NULL DEFAULT 0,format_prefix TEXT NOT NULL DEFAULT '',
            base_style_weight REAL NOT NULL DEFAULT 1.0,raw_json TEXT NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS channel_style_fts USING fts5(
            example_id UNINDEXED,text,content_type,source_language,platform,tokenize='unicode61 remove_diacritics 2');
        CREATE TABLE IF NOT EXISTS translation_feedback(
            feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,source_text TEXT NOT NULL,source_language TEXT NOT NULL,
            content_type TEXT NOT NULL,generated_text TEXT NOT NULL,final_user_text TEXT NOT NULL,created_at TEXT NOT NULL,
            context_json TEXT NOT NULL DEFAULT '{}',confirmed INTEGER NOT NULL DEFAULT 1);
        """)
        self.conn.commit()

    def _corpus_files(self) -> list[Path]:
        parts = sorted(self.corpus_dir.glob("part-*.jsonl.gz")) if self.corpus_dir.exists() else []
        if parts: return parts
        return [self.corpus_path] if self.corpus_path.exists() else []

    def _corpus_digest(self) -> str:
        files = self._corpus_files()
        if not files: return "missing"
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.name.encode("utf-8")); digest.update(path.read_bytes())
        return digest.hexdigest()

    def _meta(self, key: str) -> str:
        row = self.conn.execute("SELECT value FROM channel_style_meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else ""

    def _set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO channel_style_meta(key,value) VALUES(?,?)", (key, str(value)))

    def _count_examples(self) -> int:
        try: return int(self.conn.execute("SELECT count(*) FROM channel_style_examples").fetchone()[0])
        except sqlite3.DatabaseError: return 0

    def _ensure_corpus_index(self) -> None:
        digest = self._corpus_digest()
        try:
            count = self._count_examples()
            valid = digest != "missing" and self._meta("style_version") == str(CHANNEL_STYLE_VERSION) and self._meta("corpus_sha256") == digest and count > 0 and self.conn.execute("SELECT count(*) FROM channel_style_fts").fetchone()[0] == count
        except sqlite3.DatabaseError:
            valid = False
        if not valid: self.rebuild_from_derived_corpus()

    def rebuild_from_derived_corpus(self) -> int:
        try: self._init_schema()
        except sqlite3.DatabaseError: pass
        files = self._corpus_files()
        if not files:
            logger.warning("Channel style corpus is missing")
            return 0
        rows: list[dict[str, Any]] = []
        try:
            for path in files:
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    for line in fh:
                        try: item = json.loads(line)
                        except json.JSONDecodeError: continue
                        if not isinstance(item, dict): continue
                        if str(item.get("text", "")).strip() and str(item.get("example_id", "")).strip(): rows.append(item)
        except (OSError, EOFError):
            return 0
        try:
            with self.conn:
                self.conn.execute("DROP TABLE IF EXISTS channel_style_fts")
                self.conn.execute("DELETE FROM channel_style_examples")
                self.conn.execute("CREATE VIRTUAL TABLE channel_style_fts USING fts5(example_id UNINDEXED,text,content_type,source_language,platform,tokenize='unicode61 remove_diacritics 2')")
                for item in rows:
                    text = str(item.get("text", "")); content_type = str(item.get("content_type", "OTHER")); platform = _platform_from_text(text, content_type)
                    values=(str(item.get("example_id","")),str(item.get("channel_id","")),str(item.get("message_id","")),text,content_type,str(item.get("source_language","other")),str(item.get("date","")),platform,int(item.get("line_count",1) or 1),int(item.get("char_count",0) or 0),int(bool(item.get("has_dialogue"))),int(bool(item.get("has_laughter"))),int(bool(item.get("has_media"))),str(item.get("format_prefix","")),1.0,json.dumps(item,ensure_ascii=False,separators=(",",":")))
                    self.conn.execute("INSERT INTO channel_style_examples(example_id,channel_id,message_id,text,content_type,source_language,date,platform,line_count,char_count,has_dialogue,has_laughter,has_media,format_prefix,base_style_weight,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
                    self.conn.execute("INSERT INTO channel_style_fts(example_id,text,content_type,source_language,platform) VALUES(?,?,?,?,?)", (values[0],values[3],values[4],values[5],values[7]))
                self._set_meta("style_version", CHANNEL_STYLE_VERSION); self._set_meta("corpus_format_version", CORPUS_FORMAT_VERSION); self._set_meta("prompt_template_version", PROMPT_TEMPLATE_VERSION); self._set_meta("corpus_sha256", self._corpus_digest()); self._set_meta("example_count", len(rows))
            return len(rows)
        except sqlite3.DatabaseError as exc:
            logger.warning("Channel style FTS rebuild failed: %s", type(exc).__name__)
            return 0

    def retrieve(self, query: str, category: str, limit: int = 8) -> list[str]:
        analysis = analyze_source(query, hinted_content_type=legacy_category_to_content_type(category, query))
        return [item.text for item in self.retrieve_examples(query, analysis, limit=limit)]

    def retrieve_examples(self, neutral_persian: str, analysis: SourceAnalysis, *, limit: int = 8, exclude_example_ids: set[str] | None = None) -> list[RetrievedStyleExample]:
        limit=max(1,min(int(limit),12)); excluded={str(x) for x in (exclude_example_ids or set())}; candidates=self._candidate_rows(neutral_persian,analysis,max_candidates=max(80,limit*10)); scored=[]; target_len=max(1,analysis.char_count)
        for pos,row in enumerate(candidates):
            if str(row["example_id"]) in excluded: continue
            score=1.0; reasons=["equal historical base weight=1.0"]
            if row["content_type"]==analysis.content_type: score+=2.4; reasons.append("same content type")
            elif _content_family(str(row["content_type"]))==_content_family(analysis.content_type): score+=1.0; reasons.append("related content family")
            if PERSIAN_RE.search(str(row["text"])): score+=0.75; reasons.append("Persian target-style example")
            if bool(row["has_dialogue"])==analysis.has_dialogue: score+=0.55; reasons.append("matching dialogue structure")
            if analysis.platform and row["platform"]==analysis.platform: score+=0.45; reasons.append("same platform")
            ratio=min(int(row["char_count"] or 0),target_len)/max(int(row["char_count"] or 1),target_len); score+=0.45*ratio
            if ratio>=0.65: reasons.append("similar length")
            lexical=max(0.0,1.4-pos*0.025); score+=lexical
            if lexical>0.5: reasons.append("neutral-Persian FTS similarity")
            scored.append(RetrievedStyleExample(str(row["example_id"]),str(row["text"])[:1800],str(row["content_type"]),str(row["source_language"]),str(row["date"]),score,reasons))
        scored.sort(key=lambda x:(-x.score,x.example_id))
        return _diverse_examples(scored,limit)

    def _candidate_rows(self, query: str, analysis: SourceAnalysis, *, max_candidates: int) -> list[sqlite3.Row]:
        max_candidates=max(20,min(int(max_candidates),250)); seen=set(); rows=[]
        try:
            match=_fts_query(query)
            if match:
                for row in self.conn.execute("SELECT e.* FROM channel_style_fts f JOIN channel_style_examples e ON e.example_id=f.example_id WHERE channel_style_fts MATCH ? ORDER BY bm25(channel_style_fts) LIMIT ?",(match,max_candidates)).fetchall(): rows.append(row); seen.add(str(row["example_id"]))
            for row in self.conn.execute("SELECT * FROM channel_style_examples WHERE content_type=? ORDER BY example_id ASC LIMIT ?",(analysis.content_type,max_candidates)).fetchall():
                if str(row["example_id"]) not in seen: rows.append(row); seen.add(str(row["example_id"]))
        except sqlite3.DatabaseError:
            if self.rebuild_from_derived_corpus(): return self._candidate_rows(query,analysis,max_candidates=max_candidates)
            return []
        return rows[:max_candidates]

    def relevant_glossary(self, source: str, neutral_persian: str, *, max_entries: int = 18) -> list[dict[str, Any]]:
        haystack=f"{source}\n{neutral_persian}".casefold(); result=[]; categories=self.glossary.get("categories",{}) if isinstance(self.glossary,dict) else {}
        for category,entries in categories.items() if isinstance(categories,dict) else []:
            if not isinstance(entries,list): continue
            for entry in entries:
                if not isinstance(entry,dict): continue
                forms=[str(entry.get("canonical_form","")),*[str(x) for x in entry.get("alternatives",[]) if str(x)]]
                if any(form and form.casefold() in haystack for form in forms):
                    clean=dict(entry); clean["category"]=category; result.append(clean)
                    if len(result)>=max_entries: return result
        return result

    def add_confirmed_feedback(self, *, source_text: str, source_language: str, content_type: str, generated_text: str, final_user_text: str, timestamp: str, context: dict[str, Any] | None = None, confirmed: bool = False) -> bool:
        if not confirmed or not all(str(v).strip() for v in (source_text,generated_text,final_user_text)): return False
        safe=_sanitize_feedback_context(context or {})
        with self.conn:
            self.conn.execute("INSERT INTO translation_feedback(source_text,source_language,content_type,generated_text,final_user_text,created_at,context_json,confirmed) VALUES(?,?,?,?,?,?,?,1)",(source_text,source_language,content_type,generated_text,final_user_text,timestamp,json.dumps(safe,ensure_ascii=False,separators=(",",":"))))
        return True

    def close(self) -> None: self.conn.close()

def historical_base_style_weight(date_metadata: str | None = None) -> float: return 1.0
def chronological_style_bonus(date_metadata: str | None = None) -> float: return 0.0

def analyze_source(text: str, *, hinted_content_type: str | None = None) -> SourceAnalysis:
    text=str(text or ""); language=detect_language(text); content_type=hinted_content_type if hinted_content_type in CONTENT_TYPES else classify_content_type(text); speakers=[m.group(1) for m in SPEAKER_LINE_RE.finditer(text)]
    uncertain=["source contains a question/possible ambiguity; preserve rather than resolve by guess"] if any(x in text for x in ("?","؟")) and len(text)>140 else []
    return SourceAnalysis(language,content_type,_unique(NUMBER_RE.findall(text)),_unique(URL_RE.findall(text)),_unique(HASHTAG_RE.findall(text)),_unique(LAUGHTER_RE.findall(text)),_unique(speakers),_unique(_extract_names_and_terms(text)),uncertain,max(1,text.count("\n")+1),len(text),len(speakers)>=2 or content_type=="LIVE_DIALOGUE",_platform_from_text(text,content_type))

def detect_language(text: str) -> str:
    if KOREAN_RE.search(text): return "ko" if not JAPANESE_RE.search(text) else "mixed"
    if JAPANESE_RE.search(text): return "ja"
    if PERSIAN_RE.search(text) and LATIN_RE.search(text): return "fa_mixed"
    if PERSIAN_RE.search(text): return "fa"
    if LATIN_RE.search(text): return "en"
    return "other"

def classify_content_type(text: str) -> str:
    lower=text.casefold(); lines=[x for x in text.splitlines() if x.strip()]
    if "fanfic" in lower or "ao3" in lower or "فیک" in lower: return "FANFIC_UPDATE"
    if ("weverse" in lower or "ویورس" in lower or "위버스" in lower) and "live" in lower: return "WEVERSE_LIVE"
    if "weverse" in lower or "ویورس" in lower or "위버스" in lower: return "WEVERSE_POST"
    if any(k in lower for k in ("fansign","fan sign","فن ساین","فن‌ساین","팬싸","fancall","fan call","فن‌کال")): return "FANSIGN"
    if any(k in lower for k in ("interview","مصاحبه","インタビュー","인터뷰")): return "INTERVIEW"
    if any(k in lower for k in ("magazine","مجله","vogue","allure","elle","gq ")): return "MAGAZINE"
    if any(k in lower for k in ("airport","فرودگاه","공항","空港")): return "AIRPORT"
    if any(k in lower for k in ("instagram"," ig ","اینستاگرام","insta")): return "INSTAGRAM_UPDATE"
    if any(k in lower for k in ("brand","کمپین","campaign","ambassador","سفیر","banila","بانیلا")): return "BRAND_AD"
    if any(k in lower for k in ("fashion week","فشن","fashion event","showroom")): return "FASHION_EVENT"
    if any(k in lower for k in ("official","공지","notice","اعلام","اطلاعیه","pledis","hybe")): return "OFFICIAL_NEWS"
    if any(k in lower for k in ("wordplay","pun","بازی با کلمه","말장난","言葉遊び")): return "WORDPLAY"
    if KOREAN_RE.search(text) and any(k in lower for k in ("معنی","یعنی","گرامر","پسوند","لحن","means","nuance")): return "KOREAN_LANGUAGE_NUANCE"
    if JAPANESE_RE.search(text) and any(k in lower for k in ("معنی","یعنی","پسوند","لحن","means","nuance")): return "JAPANESE_LANGUAGE_NUANCE"
    if len(SPEAKER_LINE_RE.findall(text))>=2: return "LIVE_DIALOGUE"
    if len(text)>500 or len(lines)>=7: return "THREAD_OR_LONG_EXPLANATION"
    if any(k in lower for k in ("op ","op:","اوپ","fan account","fanaccount","فنی که","تعریف کرد")): return "FAN_ACCOUNT_OR_OP_STORY"
    if any(q in text for q in ('“','”','«','»')) and len(text)<400: return "MEMBER_QUOTE"
    if any(k in lower for k in ("interaction","جونگچول","جیهان","couphan","gyuhan","باهم","همدیگه")): return "MEMBER_INTERACTION"
    reaction=bool(re.search(r"(?:😭|🥺|💗|🩷|💘|گریه|کیوت|ناز|عسلی|تاینی|دارم میمیرم|می‌میرم)",text))
    if len(text)<=100 and reaction: return "SHORT_REACTION"
    if re.search(r"\b(?:20\d{2}|\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\b",text) and not reaction: return "FACTUAL_INFORMATION"
    if LATIN_RE.search(text) and not PERSIAN_RE.search(text): return "X_FANBASE_UPDATE"
    return "OTHER"

def legacy_category_to_content_type(category: str, text: str = "") -> str:
    return {"live":"LIVE_DIALOGUE","jeonghan_instagram":"INSTAGRAM_UPDATE","member_instagram":"INSTAGRAM_UPDATE","brand":"BRAND_AD","fansign":"FANSIGN","airport":"AIRPORT","general":classify_content_type(text)}.get(str(category),classify_content_type(text))

def normalize_numbers(value: str) -> list[str]:
    return NUMBER_RE.findall(str(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩","01234567890123456789")))

def verify_hard_facts(source: str, output: str, analysis: SourceAnalysis | None = None) -> list[str]:
    analysis=analysis or analyze_source(source); failures=[]; src=normalize_numbers(source); out=normalize_numbers(output)
    missing=[x for x in src if x not in out]; extra=[x for x in out if x not in src]
    if missing: failures.append("missing numbers: "+", ".join(_unique(missing)))
    if extra: failures.append("invented numbers: "+", ".join(_unique(extra)))
    for url in analysis.urls:
        if url not in output: failures.append(f"missing URL: {url}")
    for url in URL_RE.findall(output):
        if url not in analysis.urls: failures.append(f"invented URL: {url}")
    for tag in analysis.hashtags:
        if tag not in output: failures.append(f"missing hashtag: {tag}")
    for tag in HASHTAG_RE.findall(output):
        if tag not in analysis.hashtags: failures.append(f"invented hashtag: {tag}")
    for laugh in analysis.laughter:
        if (laugh.startswith("ㅋ") or laugh.startswith("ㅎ")) and laugh not in output: failures.append(f"missing source laughter: {laugh}")
    return failures

def is_trivial_source(text: str, analysis: SourceAnalysis | None = None) -> bool:
    a=analysis or analyze_source(text)
    return a.char_count<=90 and a.line_count<=2 and not a.has_dialogue and a.content_type not in {"WORDPLAY","KOREAN_LANGUAGE_NUANCE","JAPANESE_LANGUAGE_NUANCE","THREAD_OR_LONG_EXPLANATION"}

def _extract_names_and_terms(text: str) -> list[str]:
    values=[m.group(0) for m in re.finditer(r"\b(?:[A-Z][A-Za-z0-9._'-]{1,}|[A-Z]{2,})\b",text)]
    for name in ("JEONGHAN","Jeonghan","Yoon Jeonghan","SEVENTEEN","Weverse","Instagram","جونگهان","هانی","سونگچول","جاشوآ","ونوو","مینگیو","هوشی","دینو","سونگکوان","윤정한","정한","ジョンハン"):
        if name.casefold() in text.casefold(): values.append(name)
    return values

def _platform_from_text(text: str, content_type: str) -> str:
    lower=text.casefold()
    if "weverse" in lower or "ویورس" in lower or "위버스" in lower or content_type in {"WEVERSE_POST","WEVERSE_LIVE"}: return "weverse"
    if "instagram" in lower or "اینستاگرام" in lower or content_type=="INSTAGRAM_UPDATE": return "instagram"
    if "youtube" in lower or "youtu.be" in lower: return "youtube"
    if content_type=="X_FANBASE_UPDATE": return "x"
    return ""

def _content_family(t: str) -> str:
    if t in {"LIVE_DIALOGUE","WEVERSE_LIVE","FANSIGN","INTERVIEW","MEMBER_QUOTE"}: return "dialogue"
    if t in {"PHOTO_REACTION","VIDEO_REACTION","SHORT_REACTION","MEMBER_INTERACTION"}: return "reaction"
    if t in {"OFFICIAL_NEWS","FACTUAL_INFORMATION","BRAND_AD","FASHION_EVENT","AIRPORT","MAGAZINE"}: return "information"
    if t in {"KOREAN_LANGUAGE_NUANCE","JAPANESE_LANGUAGE_NUANCE","WORDPLAY","THREAD_OR_LONG_EXPLANATION","FAN_ACCOUNT_OR_OP_STORY"}: return "explanation"
    return "general"

def _fts_query(value: str) -> str:
    tokens=[x for x in TOKEN_RE.findall(value or "") if len(x)>1][:24]
    return " OR ".join(f'"{x.replace(chr(34), "")}"*' for x in tokens) if tokens else ""

def _diverse_examples(items: list[RetrievedStyleExample], limit: int) -> list[RetrievedStyleExample]:
    chosen=[]; fingerprints=set()
    for item in items:
        fp=re.sub(r"[0-9A-Za-z\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+","W",item.text[:100]); fp=re.sub(r"W(?:\s+W)+","W",fp)
        if fp in fingerprints and len(chosen)>=max(3,limit//2): continue
        fingerprints.add(fp); chosen.append(item)
        if len(chosen)>=limit: break
    if len(chosen)<limit:
        selected={x.example_id for x in chosen}
        for item in items:
            if item.example_id not in selected: chosen.append(item)
            if len(chosen)>=limit: break
    return chosen

def _sanitize_feedback_context(value: dict[str, Any]) -> dict[str, Any]:
    forbidden=re.compile(r"(?i)(token|cookie|secret|api[_-]?key|authorization|dsn|x_accounts)"); result={}
    for key,item in value.items():
        if forbidden.search(str(key)): continue
        if isinstance(item,(str,int,float,bool)) or item is None:
            if isinstance(item,str) and forbidden.search(item): continue
            result[str(key)]=item
    return result

def _unique(values: Iterable[str]) -> list[str]:
    out=[]; seen=set()
    for value in values:
        value=str(value); key=value.casefold()
        if value and key not in seen: seen.add(key); out.append(value)
    return out
