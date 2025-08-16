# main.py
import os
import asyncio
from typing import List

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ==== Конфигурация из переменных окружения ====
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET     = os.getenv("WEBHOOK_SECRET", "").strip()
ALLOWED_CHATS_RAW  = os.getenv("ALLOWED_CHATS", "").strip()

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан как переменная окружения")
if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET не задан как переменная окружения")

ALLOWED_CHATS: List[int] = []
if ALLOWED_CHATS_RAW:
    try:
        ALLOWED_CHATS = [int(x) for x in ALLOWED_CHATS_RAW.split(",") if x.strip()]
    except Exception:
        # Если формат неверный — игнорируем (лучше поправить в Cloud Run)
        ALLOWED_CHATS = []

# ==== FastAPI ====
app = FastAPI(title="Insta Transcriber Bot")

@app.get("/")
async def root():
    # Наличие корня с 200 OK помогает Cloud Run health-check
    return {"ok": True, "service": "insta-transcriber-bot"}

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

# ==== Telegram Application ====
application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

def _allowed(chat_id: int) -> bool:
    return True if not ALLOWED_CHATS else (chat_id in ALLOWED_CHATS)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not _allowed(update.effective_chat.id):
        return
    await update.message.reply_text(
        "Привет! Отправь ссылку на Instagram Reels — верну расшифровку.\n"
        "Пока что проверяем, что бот жив 😉"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not _allowed(update.effective_chat.id):
        return
    await update.message.reply_text("Команды: /start, /help")

# Заглушка на любые сообщения (позже сюда добавим транс-крипт)
async def any_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not _allowed(update.effective_chat.id):
        return
    text = (update.message.text or "").strip()
    if "instagram.com/reel" in text or "instagram.com/p/" in text:
        await update.message.reply_text("Принял ссылку. Логика транскрибации будет добавлена после проверки запуска 👍")
    else:
        await update.message.reply_text("Пришли ссылку на Reels, пожалуйста.")

# Регистрируем хендлеры
application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), any_text))

# ==== Жизненный цикл в связке с FastAPI ====
@app.on_event("startup")
async def on_startup():
    # Важно: initialize/start, чтобы application был готов обрабатывать update
    await application.initialize()
    await application.start()

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()

# ==== Модель для вебхука (принимаем «как есть») ====
class TelegramUpdate(BaseModel):
    root: dict

@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    data = await request.json()
    update = Update.de_json(data, application.bot)
    # Передаём событие в PTB
    await application.process_update(update)
    return {"ok": True}
