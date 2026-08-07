from __future__ import annotations
import argparse, gzip, json, re
from json import JSONDecoder
from pathlib import Path
PERSIAN=re.compile(r"[\u0600-\u06ff]"); KOREAN=re.compile(r"[\uac00-\ud7af]"); JAPANESE=re.compile(r"[\u3040-\u30ff]"); LATIN=re.compile(r"[A-Za-z]")
SPEAKER=re.compile(r"^\s*([^\s:：]{1,20})\s*[:：]\s*(.+)$",re.M); LAUGHTER=re.compile(r"(?:ㅋ{2,}|ㅎ{2,}|(?:lol|lmao|lmfao)\b|خ{2,}|ه{3,})",re.I)
def visible_text(value):
    if isinstance(value,str): return value
    if isinstance(value,list): return "".join(x if isinstance(x,str) else str(x.get("text","")) if isinstance(x,dict) else "" for x in value)
    return ""
def load_export(path: Path):
    raw=path.read_text(encoding="utf-8")
    try:
        data=json.loads(raw); return data,list(data.get("messages",[])),False
    except json.JSONDecodeError:
        prefix=raw[:raw.find('"messages"')]; channel_match=re.search(r'"id"\s*:\s*(-?\d+)',prefix); name_match=re.search(r'"name"\s*:\s*"([^"]*)"',prefix)
        head={"id":int(channel_match.group(1)) if channel_match else "","name":name_match.group(1) if name_match else ""}
        start=raw.find("[",raw.find('"messages"'))+1; dec=JSONDecoder(); messages=[]
        while start>0 and start<len(raw):
            while start<len(raw) and raw[start] in " \r\n\t,": start+=1
            if start>=len(raw) or raw[start]=="]": break
            try: obj,end=dec.raw_decode(raw,start)
            except json.JSONDecodeError: break
            if isinstance(obj,dict): messages.append(obj)
            start=end
        return head,messages,True
def language(text):
    ko=bool(KOREAN.search(text)); ja=bool(JAPANESE.search(text)); fa=bool(PERSIAN.search(text)); en=bool(LATIN.search(text))
    if ko and ja:return "mixed"
    if ko:return "ko"
    if ja:return "ja"
    if fa and en:return "fa_mixed"
    if fa:return "fa"
    if en:return "en"
    return "other"
def content_type(text):
    l=text.casefold(); lines=[x for x in text.splitlines() if x.strip()]
    if ("weverse" in l or "ویورس" in l or "위버스" in l) and "live" in l:return "WEVERSE_LIVE"
    if "weverse" in l or "ویورس" in l or "위버스" in l:return "WEVERSE_POST"
    checks=[("FANFIC_UPDATE",("fanfic","ao3","فیک")),("FANSIGN",("fansign","fan sign","فن ساین","فن‌ساین","팬싸","fancall","fan call","فن‌کال")),("INTERVIEW",("interview","مصاحبه","インタビュー","인터뷰")),("MAGAZINE",("magazine","مجله","vogue","allure","elle","gq ")),("AIRPORT",("airport","فرودگاه","공항","空港")),("INSTAGRAM_UPDATE",("instagram","اینستاگرام","insta")),("BRAND_AD",("brand","کمپین","campaign","ambassador","سفیر","banila","بانیلا")),("FASHION_EVENT",("fashion week","فشن","fashion event","showroom")),("OFFICIAL_NEWS",("official","공지","notice","اعلام","اطلاعیه","pledis","hybe")),("WORDPLAY",("wordplay","pun","بازی با کلمه","말장난","言葉遊び"))]
    for name,keys in checks:
        if any(k in l for k in keys):return name
    if len(SPEAKER.findall(text))>=2:return "LIVE_DIALOGUE"
    if len(text)>500 or len(lines)>=7:return "THREAD_OR_LONG_EXPLANATION"
    if any(k in l for k in ("op ","op:","اوپ","fan account","fanaccount","فنی که","تعریف کرد")):return "FAN_ACCOUNT_OR_OP_STORY"
    if any(q in text for q in ("“","”","«","»")) and len(text)<400:return "MEMBER_QUOTE"
    if any(k in l for k in ("interaction","جونگچول","جیهان","couphan","gyuhan","باهم","همدیگه")):return "MEMBER_INTERACTION"
    reaction=bool(re.search(r"(?:😭|🥺|💗|🩷|💘|گریه|کیوت|ناز|عسلی|تاینی|دارم میمیرم|می‌میرم)",text))
    if len(text)<=100 and reaction:return "SHORT_REACTION"
    if re.search(r"\b(?:20\d{2}|\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\b",text) and not reaction:return "FACTUAL_INFORMATION"
    if LATIN.search(text) and not PERSIAN.search(text):return "X_FANBASE_UPDATE"
    return "OTHER"
def clean_entities(message):
    allowed={"bold","blockquote","text_link","hashtag","custom_emoji","italic","code","mention","link"}
    return [{k:e.get(k) for k in ("type","text","href") if e.get(k) is not None} for e in message.get("text_entities",[]) if isinstance(e,dict) and e.get("type") in allowed]
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("exports",nargs="+",type=Path); ap.add_argument("--out",type=Path,required=True); ap.add_argument("--report",type=Path); args=ap.parse_args(); unique={}; report=[]
    for path in args.exports:
        meta,messages,truncated=load_export(path); channel=str(meta.get("id","")); textual=0
        for m in messages:
            text=visible_text(m.get("text","")).strip()
            if m.get("type")!="message" or not text: continue
            textual+=1; key=(channel,str(m.get("id","")))
            if key in unique: continue
            ct=content_type(text); unique[key]={"version":1,"example_id":f"{channel}:{m.get('id')}","channel_id":channel,"message_id":str(m.get("id")),"text":text,"date":str(m.get("date","")),"reply_to_message_id":m.get("reply_to_message_id"),"entities":clean_entities(m),"media_type":m.get("media_type") or ("photo" if m.get("photo") else ""),"has_media":bool(m.get("photo") or m.get("file") or m.get("media_type")),"source_language":language(text),"content_type":ct,"line_count":max(1,text.count("\n")+1),"char_count":len(text),"has_dialogue":len(SPEAKER.findall(text))>=2 or ct=="LIVE_DIALOGUE","has_laughter":bool(LAUGHTER.search(text)),"format_prefix":text.splitlines()[0][:40]}
        report.append({"file":path.name,"complete_messages_recovered":len(messages),"textual_messages":textual,"truncated_tail_ignored":truncated})
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(args.out,"wt",encoding="utf-8",compresslevel=9) as fh:
        for row in sorted(unique.values(),key=lambda x:(x["channel_id"],int(x["message_id"]) if x["message_id"].isdigit() else x["message_id"])): fh.write(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n")
    result={"corpus_format_version":1,"unique_textual_messages":len(unique),"deduplication":"stable channel_id + message_id only","text_similarity_deduplication":False,"chronological_base_weight":1.0,"recency_weighting":"NONE","exports":report}
    if args.report: args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
if __name__=="__main__": main()
