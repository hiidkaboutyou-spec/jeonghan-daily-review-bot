from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ai import CaptionWriter
from .config import ConfigError, ROOT, Settings
from .media import MediaManager
from .models import Draft, EventGroup, Update
from .organizer import organize_updates
from .state import StateStore
from .style import StyleMemory, ThemeEngine, ensure_rtl_line
from .telegram import TelegramBot, TelegramError, draft_keyboard, inline_keyboard, main_keyboard
from .x_client import XCollectionError, XCollector, normalize_handle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class Application:
    def __init__(self, settings: Settings):
        self.settings=settings;self.state=StateStore(settings.state_path);self.telegram=TelegramBot(settings.telegram_token,settings.admin_user_id,settings.review_chat_id);self.memory=StyleMemory(ROOT);self.writer=CaptionWriter(settings.gemini_api_key,settings.gemini_model,self.memory);self.themes=ThemeEngine(settings.themes,settings.timezone);self.collector=XCollector(settings.x_cookies,settings.sources,settings.keyword_groups);self.media=MediaManager(settings.x_cookies)

    async def run(self)->None:
        try:
            await self.process_telegram_updates();await self.run_scheduled_scan();await self.deliver_pending()
        finally:self.state.save()

    async def process_telegram_updates(self)->None:
        updates=self.telegram.get_updates(self.state.telegram_offset)
        for item in updates:
            update_id=int(item.get("update_id",0));self.state.telegram_offset=max(self.state.telegram_offset,update_id+1)
            try:
                if "message" in item:await self.handle_message(item["message"])
                elif "callback_query" in item:await self.handle_callback(item["callback_query"])
            except (XCollectionError,TelegramError,ConfigError) as exc:
                logger.warning("Command failed: %s",exc);self._safe_send(f"❌ {str(exc)[:900]}")
            except Exception as exc:
                logger.exception("Unexpected command error");self._safe_send(f"❌ خطای پیش‌بینی‌نشده: {type(exc).__name__}")

    async def handle_message(self,message:dict[str,Any])->None:
        if not self.telegram.is_admin_message(message):return
        text=str(message.get("text","") or message.get("caption","")).strip()
        if not text:return
        # Persistent keyboard actions. They intentionally look like normal text
        # messages because Telegram ReplyKeyboardMarkup works that way.
        if text=="🕑 ۲ ساعت اخیر":await self.run_recent2h();return
        if text=="🗂 ۲۴ ساعت منبع":self.show_sources();return
        if text=="🔎 سرچ آرشیو":self.ask_for_search();return
        if text=="📚 فن‌فیک":
            self.telegram.send_message("📚 لیست‌های فن‌فیک شبانه جداگانه از X و AO3 هر شب خودکار می‌آیند. برای اجرای دستی فعلاً از GitHub Actions → Nightly Jeonghan Fic Digest → Run workflow استفاده کن.",reply_markup=main_keyboard());return
        if text=="📋 وضعیت":self.send_status();return
        if text=="❔ راهنما":self.send_help();return
        awaiting=self.state.pop_awaiting(self.settings.admin_user_id)
        if awaiting=="search" and not text.startswith("/"):await self.run_search(text);return
        if awaiting=="source" and not text.startswith("/"):await self.run_source24(text);return
        command,_,argument=text.partition(" ");command=command.split("@",1)[0].lower();argument=argument.strip()
        if command in {"/start","/menu"}:self.send_start()
        elif command in {"/recent2h","/fetch2h"}:await self.run_recent2h()
        elif command=="/search":await self.run_search(argument) if argument else self._async_noop(self.ask_for_search())
        elif command in {"/source24","/fetch24h"}:await self.run_source24(argument) if argument else self._async_noop(self.show_sources())
        elif command=="/sources":self.show_sources()
        elif command=="/status":self.send_status()
        elif command=="/help":self.send_help()
        else:self.telegram.send_message("دستور را نشناختم. از دکمه‌های پایین چت استفاده کن:",reply_markup=main_keyboard())

    async def _async_noop(self,value=None):return value

    async def handle_callback(self,callback:dict[str,Any])->None:
        if not self.telegram.is_admin_callback(callback):return
        callback_id=str(callback.get("id",""));data=str(callback.get("data",""));self.telegram.answer_callback(callback_id,"در حال انجام…");parts=data.split(":")
        if data=="cmd:recent2h":await self.run_recent2h()
        elif data=="cmd:search":self.ask_for_search()
        elif data=="cmd:sources":self.show_sources()
        elif data=="cmd:status":self.send_status()
        elif data=="cmd:help":self.send_help()
        elif len(parts)>=3 and parts[0]=="source":await self.run_source24(parts[2])
        elif len(parts)>=3 and parts[0]=="pick":await self.run_selected_event(parts[1],int(parts[2]))
        elif len(parts)>=3 and parts[0]=="draft":await self.handle_draft_action(parts[1],parts[2],int(callback.get("message",{}).get("message_id",0) or 0))

    def send_start(self)->None:
        text="بات خصوصی دیلی جونگهان آماده است.\n\nدکمه‌های ضروری همیشه پایین چت می‌مانند؛ لازم نیست دستورها را حفظ کنی.\n• ۲ ساعت اخیر: همهٔ آپدیت‌ها حتی تکراری\n• ۲۴ ساعت منبع: انتخاب منبع و دریافت کامل\n• سرچ آرشیو: تاریخ یا توضیح رویداد\n• فن‌فیک: وضعیت لیست شبانه\n• وضعیت و راهنما"
        self.telegram.send_message(ensure_rtl_line(text),reply_markup=main_keyboard())

    def ask_for_search(self)->None:
        self.state.set_awaiting(self.settings.admin_user_id,"search");self.telegram.send_message(ensure_rtl_line("تاریخ یا توضیحت را بفرست؛ مثلاً:\n2026-07-14\n260714\nلایوی که داشت بازی می‌کرد و با خودش حرف می‌زد"),reply_markup=main_keyboard())

    def show_sources(self)->None:
        enabled=[s for s in self.settings.sources if s.get("enabled",True)];rows=[[(f"@{s['handle']}",f"source:24:{s['handle']}")] for s in enabled];rows.append([("➕ وارد کردن منبع دیگر","source:24:custom")]);self.telegram.send_message("یک منبع را برای دریافت کامل ۲۴ ساعت انتخاب کن:",reply_markup=inline_keyboard(rows))

    def send_status(self)->None:
        data=self.state.data;text=f"وضعیت بات\n\nمنابع فعال: {sum(bool(i.get('enabled',True)) for i in self.settings.sources)}\nآیتم‌های آرشیو داخلی: {len(data.get('archive',{}))}\nصف باقی‌مانده: {len(data.get('pending_delivery',[]))}\nآخرین اسکن خودکار: {data.get('last_auto_run') or 'هنوز اجرا نشده'}\nمدل کپشن: {self.settings.gemini_model}";self.telegram.send_message(ensure_rtl_line(text),reply_markup=main_keyboard())

    def send_help(self)->None:
        text="دکمه‌های پایین چت:\n🕑 ۲ ساعت اخیر — همهٔ محتوای دو ساعت اخیر حتی تکراری\n🗂 ۲۴ ساعت منبع — انتخاب منبع و دریافت کامل\n🔎 سرچ آرشیو — تاریخ یا توضیح رویداد\n📚 فن‌فیک — لیست شبانهٔ X و AO3\n📋 وضعیت — وضعیت بات\n❔ راهنما — همین راهنما";self.telegram.send_message(ensure_rtl_line(text),reply_markup=main_keyboard())

    async def run_recent2h(self)->None:
        self.telegram.send_message("🕑 دارم تمام آپدیت‌های دو ساعت اخیر را دوباره جمع می‌کنم…",reply_markup=main_keyboard());end=datetime.now(timezone.utc);start=end-timedelta(hours=2);updates=await self.collector.collect_window(start,end,max_per_query=80);updates=[i for i in updates if start<=i.created_at<end]
        if not updates:self.telegram.send_message("در دو ساعت اخیر چیزی پیدا نشد.",reply_markup=main_keyboard());return
        await self.deliver_updates(updates,force=True)

    async def run_source24(self,value:str)->None:
        if value=="custom":self.state.set_awaiting(self.settings.admin_user_id,"source");self.telegram.send_message("لینک X یا یوزرنیم منبع را بفرست.",reply_markup=main_keyboard());return
        handle=normalize_handle(value)
        if not handle:self.telegram.send_message("یوزرنیم منبع درست نیست.",reply_markup=main_keyboard());return
        self.telegram.send_message(f"🗂 دارم ۲۴ ساعت کامل @{handle} را از قدیمی به جدید می‌گیرم…",reply_markup=main_keyboard());end=datetime.now(timezone.utc);start=end-timedelta(hours=24);updates=await self.collector.collect_source(handle,start,end);updates=[i for i in updates if start<=i.created_at<end]
        if not updates:self.telegram.send_message(f"برای @{handle} در ۲۴ ساعت گذشته چیزی پیدا نشد.",reply_markup=main_keyboard());return
        await self.deliver_updates(updates,force=True)

    async def run_search(self,query:str)->None:
        self.telegram.send_message(f"🔎 دارم برای «{query[:200]}» گزینه‌های مرتبط را پیدا می‌کنم…",reply_markup=main_keyboard());date_range=parse_date_query(query,self.settings.timezone);expanded=self.writer.expand_search(query)
        if date_range:
            start,end=date_range;base_queries=[]
            for group in self.settings.keyword_groups:
                terms=[str(t) for t in group.get("terms",[]) if str(t).strip()]
                if terms:base_queries.append(" OR ".join(f'\"{t}\"' if " " in t else t for t in terms))
            expanded=base_queries+expanded
        else:start=end=None
        updates=await self.collector.search_archive(expanded,start=start,end=end,max_per_query=100)
        if not updates:self.telegram.send_message("هیچ نتیجهٔ واقعی و قابل‌استفاده‌ای پیدا نشد.",reply_markup=main_keyboard());return
        groups=rank_groups(query,organize_updates(updates))[:8];titles=self.writer.candidate_titles(query,groups);session_id=short_id(query+datetime.now(timezone.utc).isoformat());payload={"query":query,"candidates":[{"key":g.key,"title":titles.get(g.key) or g.title,"started_at":g.started_at.isoformat(),"selected":g.updates[0].to_dict(),"preview_ids":[i.id for i in g.updates]} for g in groups]};self.state.create_session(session_id,payload);lines=[f"نتیجه‌های پیشنهادی برای «{query}»: "];rows=[]
        for index,g in enumerate(groups):
            local_date=g.started_at.astimezone(self.settings.timezone).strftime("%Y-%m-%d %H:%M");title=titles.get(g.key) or g.title;lines.append(f"{index+1}. {title} — {local_date} — {len(g.updates)} مورد");rows.append([(f"{index+1}. {title[:40]}",f"pick:{session_id}:{index}")])
        self.telegram.send_message(ensure_rtl_line("\n".join(lines)),reply_markup=inline_keyboard(rows))

    async def run_selected_event(self,session_id:str,index:int)->None:
        session=self.state.get_session(session_id)
        if not session:self.telegram.send_message("این سرچ منقضی شده؛ دوباره سرچ کن.",reply_markup=main_keyboard());return
        candidates=list(session.get("candidates",[]))
        if index<0 or index>=len(candidates):self.telegram.send_message("گزینهٔ انتخاب‌شده معتبر نیست.",reply_markup=main_keyboard());return
        selected=Update.from_dict(candidates[index]["selected"]);self.telegram.send_message("انتخاب شد؛ دارم تمام رشته و آپدیت‌های مرتبط همان رویداد را جمع می‌کنم…",reply_markup=main_keyboard());updates=await self.collector.collect_event(selected)
        if not updates:updates=[selected]
        await self.deliver_updates(updates,force=True)

    async def run_scheduled_scan(self)->None:
        now=datetime.now(timezone.utc);last_raw=str(self.state.data.get("last_auto_run","") or "")
        try:last=datetime.fromisoformat(last_raw.replace("Z","+00:00")) if last_raw else now-timedelta(hours=2)
        except ValueError:last=now-timedelta(hours=2)
        if last.tzinfo is None:last=last.replace(tzinfo=timezone.utc)
        if now-last<timedelta(minutes=10):return
        start=max(last-timedelta(minutes=30),now-timedelta(hours=self.settings.scheduled_lookback_hours))
        try:updates=await self.collector.collect_window(start,now,max_per_query=100)
        except XCollectionError as exc:logger.warning("Scheduled X scan failed: %s",exc);return
        for update in updates:self.state.archive_update(update);self.state.queue_update(update)
        self.state.data["last_auto_run"]=now.isoformat()

    async def deliver_pending(self)->None:
        ids=self.state.pop_pending(self.settings.max_auto_items_per_run);updates=[self.state.get_update(i) for i in ids];await self.deliver_updates([i for i in updates if i is not None],force=False)

    async def deliver_updates(self,updates:list[Update],*,force:bool)->None:
        if not updates:return
        for update in updates:self.state.archive_update(update)
        groups=organize_updates(updates)
        for group in groups:
            ordered=sorted(group.updates,key=lambda i:(i.created_at,i.id))
            for index,update in enumerate(ordered,start=1):
                if not force and self.state.was_delivered(update.id):continue
                caption=self.writer.write(update,group,index,len(ordered));caption=self.themes.decorate(caption,group,index,len(ordered));prepared=[]
                try:prepared=self.media.prepare(update.media)
                except Exception as exc:logger.warning("Media preparation failed for %s: %s",update.id,exc)
                try:
                    if prepared:self.telegram.send_media(prepared)
                    draft_id=short_id(update.id+caption);sent=self.telegram.send_message(caption,reply_markup=draft_keyboard(draft_id),disable_preview=True);message_id=int(sent.get("message_id",0) or 0);draft=Draft(id=draft_id,update_id=update.id,event_key=group.key,caption=caption,telegram_message_id=message_id,created_at=datetime.now(timezone.utc).isoformat());self.state.save_draft(draft);self.state.mark_delivered(update.id)
                finally:self.media.cleanup(prepared)

    async def handle_draft_action(self,action:str,draft_id:str,message_id:int)->None:
        draft=self.state.get_draft(draft_id)
        if not draft:self.telegram.send_message("این پیش‌نویس دیگر در حافظه نیست.",reply_markup=main_keyboard());return
        if action=="reject":self.state.reject_draft(draft_id);self.telegram.edit_message_text(message_id,draft.caption+"\n\n🗑 رد شد",reply_markup=None);return
        if action=="copy":self.telegram.send_message(draft.caption,reply_markup=main_keyboard());return
        update=self.state.get_update(draft.update_id)
        if not update:self.telegram.send_message("متن اصلی این آپدیت پیدا نشد.",reply_markup=main_keyboard());return
        mode={"fun":"funny","soft":"soft","precise":"precise"}.get(action,"default");group=EventGroup(key=draft.event_key or update.id,category=update.category,title=update.event_title or "آپدیت جونگهان",updates=[update]);caption=self.writer.write(update,group,1,1,mode=mode);caption=self.themes.decorate(caption,group,1,1);draft.caption=caption;draft.mode=mode;self.state.save_draft(draft);self.telegram.edit_message_text(message_id,caption,reply_markup=draft_keyboard(draft.id))

    def _safe_send(self,text:str)->None:
        try:self.telegram.send_message(text,reply_markup=main_keyboard())
        except Exception:logger.exception("Could not send Telegram error message")


def short_id(value:str)->str:return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]

def parse_date_query(query:str,tz)->tuple[datetime,datetime]|None:
    value=query.strip();patterns=[r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b",r"\b(\d{2})(\d{2})(\d{2})\b"]
    match=re.search(patterns[0],value)
    if match:year,month,day=map(int,match.groups())
    else:
        match=re.search(patterns[1],value)
        if not match:return None
        yy,month,day=map(int,match.groups());year=2000+yy
    try:local=datetime(year,month,day,tzinfo=tz)
    except ValueError:return None
    return local.astimezone(timezone.utc),(local+timedelta(days=1)).astimezone(timezone.utc)

def rank_groups(query:str,groups:list[EventGroup])->list[EventGroup]:
    tokens={t.casefold() for t in re.findall(r"[A-Za-z0-9_\u0600-\u06ff\uac00-\ud7af\u3040-\u30ff]+",query) if len(t)>1}
    def score(group):
        haystack=" ".join([group.title]+[i.text for i in group.updates[:5]]).casefold();overlap=sum(1 for token in tokens if token in haystack);media=sum(len(i.media) for i in group.updates);return (overlap,len(group.updates),media,-group.started_at.timestamp())
    return sorted(groups,key=score,reverse=True)


def build_parser():
    parser=argparse.ArgumentParser(description="Jeonghan private review bot");parser.add_argument("--check",action="store_true",help="Validate configuration and exit without sending Telegram messages.");return parser

async def async_main(args):
    try:settings=Settings.load()
    except ConfigError as exc:print(f"CONFIG ERROR: {exc}",file=sys.stderr);return 2
    app=Application(settings)
    if args.check:
        print(f"CHECK OK | sources={len(settings.sources)} | themes={len(settings.themes)} | voice_examples={len(app.memory.examples)} | model={settings.gemini_model}");return 0
    await app.run();return 0

def main():return asyncio.run(async_main(build_parser().parse_args()))
if __name__=="__main__":raise SystemExit(main())
