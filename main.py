import os
import re
import json
import tempfile
import logging
from typing import Optional, List

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse

# ---------- ЛОГИ ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("reels-transcriber")

# ---------- ENV ----------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY", "").strip()
WEBHOOK_SECRET     = os.environ.get("WEBHOOK_SECRET", "").strip()
ALLOWED_CHATS      = os.environ.get("ALLOWED_CHATS", "").strip()

if not TELEGRAM_BOT_TOKEN:
    log.error("ENV TELEGRAM_BOT_TOKEN is not set")
if not OPENAI_API_KEY:
    log.error("ENV OPENAI_API_KEY is not set")
if not WEBHOOK_SECRET:
    log.warning("ENV WEBHOOK_SECRET is not set (webhook will not be protected)")

ALLOWED_CHAT_IDS: Optional[List[int]] = None
if ALLOWED_CHATS:
    try:
        ALLOWED_CHAT_IDS = [int(x) for x in re.split(r"[,\s]+", ALLOWED_CHATS) if x]
    except Exception:
        log.warning("Could not parse ALLOWED_CHATS=%s", ALLOWED_CHATS)

# ---------- OPENAI ----------
try:
    from openai import OpenAI
    openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception as e:
    openai_client = None
    log.error("Failed to init OpenAI client: %s", e)

# ---------- FASTAPI ----------
app = FastAPI()

# ---------- HELPERS ----------

TG_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

URL_REGEX = re.compile(
    r"(https?://[^\s]+)",
    flags=re.IGNORECASE
)

def split_by_chunks(text: str, limit: int = 3900) -> List[str]:
    """Режем ответ на части, чтобы не превышать лимит Телеграма (4096)."""
    res, cur = [], []
    cur_len = 0
    for line in text.splitlines(True):
        if cur_len + len(line) > limit:
            res.append("".join(cur))
            cur = [line]
            cur_len = len(line)
        else:
            cur.append(line)
            cur_len += len(line)
    if cur:
        res.append("".join(cur))
    return res or ["(пусто)"]

def tg_send_message(chat_id: int, text: str, reply_to: Optional[int] = None):
    data = {"chat_id": chat_id, "text": text}
    if reply_to:
        data["reply_to_message_id"] = reply_to
        data["allow_sending_without_reply"] = True
    r = requests.post(f"{TG_API}/sendMessage", json=data, timeout=30)
    if r.status_code != 200:
        log.error("sendMessage error: %s", r.text)

def extract_first_url(text: str) -> Optional[str]:
    m = URL_REGEX.search(text or "")
    return m.group(1) if m else None

def is_instagram_url(url: str) -> bool:
    return "instagram.com/reel" in url or "instagram.com/p/" in url or "instagram.com/tv/" in url

def download_audio_with_ytdlp(url: str) -> Optional[str]:
    """
    Качаем аудио дорожку через yt-dlp.
    Возвращаем путь к временной .mp3/.m4a, либо None.
    """
    try:
        import yt_dlp
    except Exception as e:
        log.error("yt-dlp not installed: %s", e)
        return None

    tmpdir = tempfile.mkdtemp(prefix="reels_")
    out_tmpl = os.path.join(tmpdir, "%(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_tmpl,
        "quiet": True,
        "noprogress": True,
        "nocheckcertificate": True,
        # если видео публичное, этого достаточно.
        # для приватных потребуются cookies — это отдельная тема.
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # yt-dlp после pp даёт путь к .mp3 в info["requested_downloads"][0]["filepath"]
            # но на разных версиях по‑разному, поэтому ищем файл в tmpdir
            for root, _, files in os.walk(tmpdir):
                for fn in files:
                    if fn.lower().endswith((".mp3", ".m4a", ".wav")):
                        return os.path.join(root, fn)
    except Exception as e:
        log.error("yt-dlp download error: %s", e)
        return None
    return None

def transcribe_file(path: str) -> str:
    if not openai_client:
        return "Ошибка: OpenAI клиент не инициализирован."
    try:
        with open(path, "rb") as f:
            tr = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="text",
                temperature=0
            )
        # client v1 отдаёт .text при response_format="text"
        return tr if isinstance(tr, str) else getattr(tr, "text", str(tr))
    except Exception as e:
        log.error("OpenAI transcription error: %s", e)
        return f"Ошибка транскрибации: {e}"

def check_allowed(chat_id: Optional[int]) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    try:
        return int(chat_id) in ALLOWED_CHAT_IDS
    except Exception:
        return False

# ---------- ROUTES ----------

@app.get("/", response_class=PlainTextResponse)
def root():
    return "OK"

@app.post("/webhook", response_class=JSONResponse)
async def webhook(request: Request):
    # Проверяем секрет от Telegram (опционально, но желательно)
    if WEBHOOK_SECRET:
        header_secret = request.headers.get("x-telegram-bot-api-secret-token")
        if header_secret != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Bad secret")

    update = await request.json()
    log.info("Update: %s", json.dumps(update)[:2000])

    msg = None
    chat_id = None
    msg_id = None

    # update.message.text / update.channel_post / edited_message — обрабатываем базовый случай
    if "message" in update:
        msg = update["message"]
    elif "channel_post" in update:
        msg = update["channel_post"]

    if msg:
        chat_id = msg.get("chat", {}).get("id")
        msg_id = msg.get("message_id")
        text = msg.get("text") or msg.get("caption") or ""

        if not check_allowed(chat_id):
            tg_send_message(chat_id, "⛔️ Доступ к боту ограничен.", msg_id)
            return {"ok": True}

        # стартовая команда
        if text.startswith("/start"):
            tg_send_message(
                chat_id,
                "Привет! Отправь ссылку на Instagram Reels (публичную), я сделаю транскрипт.",
                msg_id
            )
            return {"ok": True}

        url = extract_first_url(text)
        if not url:
            tg_send_message(chat_id, "Пришлите ссылку на видео (Instagram Reels).", msg_id)
            return {"ok": True}

        if not is_instagram_url(url):
            tg_send_message(chat_id, "Похоже, это не ссылка на Instagram. Нужна ссылка вида instagram.com/reel/…", msg_id)
            return {"ok": True}

        tg_send_message(chat_id, "⏳ Скачиваю аудио…", msg_id)
        audio_path = download_audio_with_ytdlp(url)
        if not audio_path:
            tg_send_message(chat_id, "Не удалось скачать аудио. Убедитесь, что ролик публичный.", msg_id)
            return {"ok": True}

        tg_send_message(chat_id, "🎙 Транскрибирую…", msg_id)
        text = transcribe_file(audio_path)

        for chunk in split_by_chunks("📝 Расшифровка:\n\n" + text):
            tg_send_message(chat_id, chunk)

        return {"ok": True}

    # игнорируем несообщения (callback_query и т.п.)
    return {"ok": True}


# Локальный запуск (в Cloud Run не используется, но не мешает)
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
