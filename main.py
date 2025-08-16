# main.py
# Telegram webhook + FastAPI на Cloud Run
# Требует переменные окружения:
#   TELEGRAM_BOT_TOKEN  – токен бота
#   WEBHOOK_SECRET      – секрет, который ты добавляешь в путь вебхука
#   ALLOWED_CHATS       – список chat_id через запятую (можно оставить пустым)
# Cloud Run передаёт переменную PORT (по умолчанию 8080) – мы её слушаем.

import os
import logging
from typing import List

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------- Конфигурация ----------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
ALLOWED_CHATS_RAW = os.getenv("ALLOWED_CHATS", "").strip()

if not BOT_TOKEN:
    # Без токена бот не запустится, поэтому явно падаем понятной ошибкой в логах
    raise RuntimeError("Env TELEGRAM_BOT_TOKEN is not set")

# Разбираем список разрешённых чатов
def parse_allowed(raw: str) -> List[int]:
    out: List[int] = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.append(int(p))
        except ValueError:
            logging.warning("Skip bad chat id: %r", p)
    return out

ALLOWED_CHATS = parse_allowed(ALLOWED_CHATS_RAW)

# Логирование по‑умолчанию
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("insta-transcriber-bot")

# ---------- FastAPI + PTB ----------
app = FastAPI(title="Insta Transcriber Bot")

# PTB Application создаём один раз и переиспользуем
tg_app: Application = Application.builder().token(BOT_TOKEN).build()

# --- Утилиты ---

def is_allowed(chat_id: int) -> bool:
    # Если ALLOWED_CHATS пуст – разрешаем всем
    return (not ALLOWED_CHATS) or (chat_id in ALLOWED_CHATS)

# --- Хендлеры Telegram ---

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else 0
    if not is_allowed(chat_id):
        await context.bot.send_message(chat_id=chat_id, text="⛔️ Доступ ограничён.")
        return

    text = (
        "Привет! Я бот для транскрибации Reels/коротких видео.\n\n"
        "Отправь ссылку на Instagram Reels — я скачаю звук и сделаю текст.\n"
        "Пока для теста я просто отвечаю, что вебхук работает ✅"
    )
    await context.bot.send_message(chat_id=chat_id, text=text)

async def text_echo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else 0
    if not is_allowed(chat_id):
        return
    msg = (update.message.text or "").strip()
    # Здесь позже вставим обработку ссылки/скачивание/транскрибацию.
    await context.bot.send_message(chat_id=chat_id, text=f"Принял сообщение: {msg}\n(вебхук работает)")

# Регистрируем хендлеры
tg_app.add_handler(CommandHandler("start", start_handler))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_echo_handler))

# ---------- Роуты FastAPI ----------

@app.get("/", response_class=PlainTextResponse)
async def root():
    # Простой healthcheck для Cloud Run
    return "ok"

@app.get("/health", response_class=PlainTextResponse)
async def health():
    return "ok"

@app.post(f"/webhook/{{secret}}")
async def webhook(secret: str, request: Request):
    # Проверяем секрет в URL
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Bad webhook secret")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    try:
        update = Update.de_json(data, tg_app.bot)
        # Обрабатываем апдейт через PTB
        await tg_app.process_update(update)
    except Exception as e:
        logger.exception("Failed to process update: %s", e)
        return JSONResponse({"ok": False})

    return JSONResponse({"ok": True})

# Локальный запуск (для отладки). В Cloud Run можно оставить – не мешает.
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
