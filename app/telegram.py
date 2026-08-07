from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

import requests

from .media import PreparedMedia

logger = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    pass


class TelegramBot:
    def __init__(self, token: str, admin_user_id: int, review_chat_id: int):
        self.token = token
        self.admin_user_id = int(admin_user_id)
        self.review_chat_id = int(review_chat_id)
        self.base = f"https://api.telegram.org/bot{token}"
        self.session = requests.Session()

    def api(self, method: str, *, data: dict[str, Any] | None = None, files=None, timeout: int = 60) -> Any:
        response = self.session.post(f"{self.base}/{method}", data=data or {}, files=files, timeout=timeout)
        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramError(f"Telegram returned HTTP {response.status_code} without JSON.") from exc
        if not response.ok or not payload.get("ok"):
            description = str(payload.get("description") or response.text)[:600]
            raise TelegramError(f"Telegram {method} failed: {description}")
        return payload.get("result")

    def get_updates(self, offset: int) -> list[dict[str, Any]]:
        result = self.api("getUpdates", data={"offset": offset, "timeout": 0, "limit": 100, "allowed_updates": json.dumps(["message", "callback_query"])}, timeout=30)
        return list(result or [])

    def send_message(self, text: str, *, chat_id: int | None = None, reply_markup: dict[str, Any] | None = None, disable_preview: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {"chat_id": chat_id or self.review_chat_id, "text": text[:4096], "disable_web_page_preview": "true" if disable_preview else "false"}
        if reply_markup: data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self.api("sendMessage", data=data)

    def edit_message_text(self, message_id: int, text: str, *, reply_markup: dict[str, Any] | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {"chat_id": self.review_chat_id, "message_id": message_id, "text": text[:4096], "disable_web_page_preview": "true"}
        if reply_markup is not None: data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self.api("editMessageText", data=data)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.api("answerCallbackQuery", data={"callback_query_id": callback_id, "text": text[:180]})

    def send_media(self, media: list[PreparedMedia]) -> list[dict[str, Any]]:
        sent=[]
        for offset in range(0,len(media),10):
            chunk=media[offset:offset+10]
            if len(chunk)==1: sent.append(self._send_single_media(chunk[0]))
            else: sent.extend(self._send_media_group(chunk))
        return sent

    def _send_single_media(self,item:PreparedMedia)->dict[str,Any]:
        method="sendPhoto" if item.kind=="photo" else "sendVideo"; field="photo" if item.kind=="photo" else "video"
        with item.path.open("rb") as handle:
            files={field:(item.path.name,handle,item.content_type)};data={"chat_id":self.review_chat_id}
            if item.kind=="video":data["supports_streaming"]="true"
            return self.api(method,data=data,files=files,timeout=180)

    def _send_media_group(self,items:list[PreparedMedia])->list[dict[str,Any]]:
        descriptors=[];handles=[];files={}
        try:
            for index,item in enumerate(items):
                key=f"file{index}";handle=item.path.open("rb");handles.append(handle);files[key]=(item.path.name,handle,item.content_type);descriptor={"type":"photo" if item.kind=="photo" else "video","media":f"attach://{key}"}
                if item.kind=="video":descriptor["supports_streaming"]=True
                descriptors.append(descriptor)
            return list(self.api("sendMediaGroup",data={"chat_id":self.review_chat_id,"media":json.dumps(descriptors)},files=files,timeout=240) or [])
        finally:
            for handle in handles:handle.close()

    def is_admin_message(self,message:dict[str,Any])->bool:
        sender=message.get("from",{});chat=message.get("chat",{});return int(sender.get("id",0))==self.admin_user_id and int(chat.get("id",0))==self.review_chat_id
    def is_admin_callback(self,callback:dict[str,Any])->bool:
        sender=callback.get("from",{});message=callback.get("message",{});chat=message.get("chat",{});return int(sender.get("id",0))==self.admin_user_id and int(chat.get("id",0))==self.review_chat_id


def inline_keyboard(rows:list[list[tuple[str,str]]])->dict[str,Any]:
    return {"inline_keyboard":[[{"text":label,"callback_data":data[:64]} for label,data in row] for row in rows]}


def main_keyboard()->dict[str,Any]:
    """Persistent keyboard above Telegram's message field.

    These are ordinary text buttons rather than callback buttons, so the keyboard
    remains available at the bottom of the chat instead of being attached to one
    old bot message.  Keep the first screen intentionally small and essential.
    """
    return {
        "keyboard": [
            [{"text":"🕑 ۲ ساعت اخیر"},{"text":"🗂 ۲۴ ساعت منبع"}],
            [{"text":"🔎 سرچ آرشیو"},{"text":"📚 فن‌فیک"}],
            [{"text":"📋 وضعیت"},{"text":"❔ راهنما"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
        "input_field_placeholder": "یک قابلیت را انتخاب کن…",
    }


def draft_keyboard(draft_id:str)->dict[str,Any]:
    return inline_keyboard([
        [("😂 بامزه‌تر",f"draft:fun:{draft_id}"),("🪽 نرم‌تر",f"draft:soft:{draft_id}")],
        [("📰 دقیق‌تر",f"draft:precise:{draft_id}"),("📋 متن تمیز",f"draft:copy:{draft_id}")],
        [("🗑 رد",f"draft:reject:{draft_id}")],
    ])
