import os
import json
from fastapi import FastAPI, Request, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import aiosqlite
import aiohttp

load_dotenv()
app = FastAPI()
DB_PATH = os.getenv("DB_PATH", "data.db")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Allow all origins for dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize DB if not present
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                sender TEXT,
                text TEXT,
                raw TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        await db.commit()

@app.on_event("startup")
async def startup():
    await init_db()

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Handles Telegram webhook requests.
    Supports both:
    - Query parameter ?secret=YOUR_SECRET
    - Telegram header X-Telegram-Bot-Api-Secret-Token
    """

    # --- Secret validation ---
    req_secret = request.query_params.get("secret")
    header_secret = request.headers.get("x-telegram-bot-api-secret-token")

    # Debug log
    print("Incoming Telegram Webhook:")
    print("Query secret:", req_secret)
    print("Header secret:", header_secret)
    print("Expected secret:", WEBHOOK_SECRET)

    if WEBHOOK_SECRET:
        if not ((req_secret and req_secret == WEBHOOK_SECRET) or (header_secret and header_secret == WEBHOOK_SECRET)):
            print("❌ Secret mismatch — returning 403 Forbidden")
            return Response(status_code=403, content="forbidden")

    # Parse body
    body = await request.json()
    msg = body.get("message") or body.get("edited_message") or {}
    chat = msg.get("chat", {})
    text = msg.get("text", "") or json.dumps(msg)
    sender = (msg.get("from") or {}).get("username") or chat.get("id") or "unknown"
    raw = json.dumps(body)

    # Save message
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages(platform,sender,text,raw) VALUES(?,?,?,?)",
            ("telegram", str(sender), str(text), raw),
        )
        await db.commit()

    # Acknowledge user
    background_tasks.add_task(
        send_telegram_message,
        chat.get("id"),
        "✅ Received: " + (str(text)[:200] if text else "~")
    )
    return {"ok": True}

async def send_telegram_message(chat_id, text):
    """Sends a reply message to the user via Telegram Bot API."""
    if not TELEGRAM_TOKEN or not chat_id:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, json=payload)
    except Exception as e:
        print("Error sending Telegram message:", e)

@app.get("/messages")
async def get_messages(limit: int = 200):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id,platform,sender,text,created_at FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
    return {
        "messages": [
            {
                "id": r[0],
                "platform": r[1],
                "sender": r[2],
                "text": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]
    }

@app.get("/health")
def health():
    return {"status": "ok"}
