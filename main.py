import os, tempfile, glob
from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI
import yt_dlp

# ── Переменные окружения (заполним в Cloud Run)
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "secret")
TRANSCRIBE_MODEL = os.environ.get("TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
ALLOWED_CHATS = os.environ.get("ALLOWED_CHATS")  # "12345,67890"

client = OpenAI(api_key=OPENAI_API_KEY)
application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
app = FastAPI()

def _allowed(chat_id:int)->bool:
    if not ALLOWED_CHATS: return True
    ids=[int(x) for x in ALLOWED_CHATS.split(",") if x.strip()]
    return chat_id in ids

def _split(t, limit=4000):
    out=[]
    while t:
        chunk=t[:limit]; cut=chunk.rfind("\n")
        if cut==-1 or cut<limit*0.6: cut=len(chunk)
        out.append(t[:cut]); t=t[cut:]
    return out

def _download_ig(url:str, td:str)->str:
    ydl_opts={"outtmpl":f"{td}/%(id)s.%(ext)s","format":"mp4/best","noplaylist":True,"quiet":True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info=ydl.extract_info(url, download=True)
        path=ydl.prepare_filename(info)
    files=glob.glob(f"{td}/*"); files.sort(key=os.path.getmtime, reverse=True)
    return files[0]

def _transcribe(path:str)->str:
    with open(path,"rb") as f:
        r=client.audio.transcriptions.create(model=TRANSCRIBE_MODEL, file=f)
    return getattr(r,"text",None) or getattr(r,"output_text","") or "Текст не распознан."

async def start(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not _allowed(update.effective_chat.id): return
    await update.message.reply_text("Пришли ссылку на публичный Instagram Reels — расшифрую. Или отправь видео/аудио файлом.")

async def on_text(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not _allowed(update.effective_chat.id): return
    url=(update.message.text or "").strip()
    if "instagram.com" not in url:
        await update.message.reply_text("Нужна ссылка на Instagram. Либо пришли файл видео/аудио.")
        return
    await update.message.reply_text("Скачиваю ролик…")
    try:
        with tempfile.TemporaryDirectory() as td:
            path=_download_ig(url, td)
            await update.message.reply_text("Транскрибирую…")
            text=_transcribe(path)
    except Exception:
        await update.message.reply_text("Не удалось скачать (возможно приватно). Пришли файл видео/аудио сообщением.")
        return
    for p in _split("Готово:\n\n"+text): await update.message.reply_text(p)

async def on_media(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not _allowed(update.effective_chat.id): return
    tgfile=None
    if update.message.video: tgfile=await update.message.video.get_file()
    elif update.message.document: tgfile=await update.message.document.get_file()
    elif update.message.audio: tgfile=await update.message.audio.get_file()
    elif update.message.voice: tgfile=await update.message.voice.get_file()
    if not tgfile: return
    await update.message.reply_text("Скачиваю файл…")
    with tempfile.TemporaryDirectory() as td:
        local=os.path.join(td,"input.bin")
        await tgfile.download_to_drive(local)
        await update.message.reply_text("Транскрибирую…")
        try:
            text=_transcribe(local)
            for p in _split("Готово:\n\n"+text): await update.message.reply_text(p)
        except Exception:
            await update.message.reply_text("Не удалось распознать аудио, попробуй другой файл.")

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
application.add_handler(MessageHandler(filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE, on_media))

@app.get("/")
def health(): return {"status":"ok"}

@app.post("/webhook/{secret}")
async def webhook(secret:str, request:Request):
    if secret!=WEBHOOK_SECRET: raise HTTPException(403, "forbidden")
    update=Update.de_json(await request.json(), application.bot)
    await application.process_update(update)
    return {"ok":True}
