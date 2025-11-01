# app.py
import os
import json
import re
import datetime
from typing import Any, Dict, Optional, List

from fastapi import FastAPI, Request, BackgroundTasks, Response, Header
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import aiosqlite
import aiohttp

load_dotenv()
app = FastAPI()

DB_PATH = os.getenv("DB_PATH", "data.db")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")  # e.g. https://xxxxx.supabase.co
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE", "")  # service role (server-side only)
APP_URL = os.getenv("APP_URL", "")  # frontend dashboard base url for /link

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

# ---------- Helpers: Supabase server-side REST helpers ----------
async def supabase_insert(table: str, row: Dict[str, Any]) -> Optional[Dict]:
    """Insert a row into Supabase via REST (server-side using service role)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Supabase env not configured; skipping supabase_insert")
        return None
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=row, headers=headers, timeout=10) as resp:
                text = await resp.text()
                if resp.status in (200, 201):
                    try:
                        return json.loads(text)
                    except Exception:
                        return {"status": "ok"}
                else:
                    print(f"Supabase insert error {resp.status}: {text}")
                    return None
        except Exception as e:
            print("Supabase insert exception:", e)
            return None

async def supabase_select(table: str, params: str = "", select: str = "*", limit: int = 100) -> List[Dict]:
    """Simple select helper using REST. params should be querystring like 'telegram_id=eq.123'"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Supabase env not configured; skipping supabase_select")
        return []
    url = f"{SUPABASE_URL}/rest/v1/{table}?select={select}"
    if params:
        url += f"&{params}"
    if limit:
        url += f"&limit={limit}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    txt = await resp.text()
                    print(f"Supabase select error {resp.status}: {txt}")
                    return []
        except Exception as e:
            print("Supabase select exception:", e)
            return []

# ---------- Helpers: Telegram ----------
async def send_telegram_message(chat_id: int, text: str):
    """Sends a reply message to the user via Telegram Bot API."""
    if not TELEGRAM_TOKEN or not chat_id:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status not in (200, 201):
                    txt = await resp.text()
                    print("Telegram send failed:", resp.status, txt)
        except Exception as e:
            print("Error sending Telegram message:", e)

def parse_amount(raw: str) -> Optional[float]:
    """Extract amount from string like '50000' or '50,000' or '₹50,000'."""
    if not raw:
        return None
    s = raw.strip()
    s = s.replace("₹", "").replace(",", "")
    m = re.match(r"^(\d+(\.\d+)?)$", s)
    if m:
        return float(m.group(1))
    # fallback: find first number inside
    m2 = re.search(r"(\d+(\.\d+)?)", s)
    if m2:
        return float(m2.group(1))
    return None

def format_currency(x: float) -> str:
    try:
        return f"₹{int(x):,}"
    except Exception:
        return f"₹{x}"

# ---------- Main webhook handler with command parsing ----------
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks, x_telegram_bot_api_secret_token: Optional[str] = Header(None)):
    """
    Handles Telegram webhook requests.
    Supports both:
    - Query parameter ?secret=YOUR_SECRET
    - Telegram header X-Telegram-Bot-Api-Secret-Token
    """
    # --- Secret validation ---
    req_secret = request.query_params.get("secret")
    header_secret = x_telegram_bot_api_secret_token  # fastapi auto-maps header (case-insensitive)

    # Debug log
    try:
        body = await request.json()
    except Exception:
        body = {}
    print("Incoming Telegram Webhook - body keys:", list(body.keys()))
    print("Query secret:", req_secret)
    print("Header secret:", header_secret)
    print("Expected secret:", WEBHOOK_SECRET)

    if WEBHOOK_SECRET:
        if not ((req_secret and req_secret == WEBHOOK_SECRET) or (header_secret and header_secret == WEBHOOK_SECRET)):
            print("❌ Secret mismatch — returning 403 Forbidden")
            return Response(status_code=403, content="forbidden")

    # Parse body
    msg = body.get("message") or body.get("edited_message") or {}
    chat = msg.get("chat", {}) or {}
    text = msg.get("text", "") or json.dumps(msg)
    # Prefer username, fallback to id
    sender_obj = msg.get("from", {}) or {}
    sender_username = sender_obj.get("username") or sender_obj.get("first_name") or str(chat.get("id"))
    sender_id = str(sender_obj.get("id") or chat.get("id") or "unknown")
    raw = json.dumps(body)

    # Save message locally (sqlite)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO messages(platform,sender,text,raw) VALUES(?,?,?,?)",
                ("telegram", str(sender_id), str(text), raw),
            )
            await db.commit()
    except Exception as e:
        print("SQLite insert error:", e)

    # Save message to Supabase messages table (server-side)
    sb_row = {
        "platform": "telegram",
        "sender": sender_id,
        "username": sender_username,
        "text": text,
        "raw": body,
        "created_at": datetime.datetime.utcnow().isoformat()
    }
    background_tasks.add_task(supabase_insert, "messages", sb_row)

    # If no text, ack and return
    if not text or not isinstance(text, str):
        return {"ok": True}

    # Simple command parser
    parts = text.strip().split()
    if len(parts) == 0:
        return {"ok": True}
    cmd = parts[0].lower()
    args = parts[1:]

    chat_id = chat.get("id")

    async def reply(msg: str):
        # reply in background
        background_tasks.add_task(send_telegram_message, chat_id, msg)

    # Command handlers
    try:
        if cmd == "/start":
            start_text = (
                "Welcome to FinSight! I can store your income/expenses and show quick stats.\n"
                "Use /help to see commands.\n"
                f"For detailed visual advice, open your dashboard: {APP_URL or '<dashboard link>'}"
            )
            await reply(start_text)
            return {"ok": True}

        if cmd == "/help":
            help_text = (
                "/income — show your incomes\n"
                "/addincome <amount> [frequency] [desc]\n"
                "/expense — show recent expenses\n"
                "/addexpense <amount> <category> [note]\n"
                "/budget — show budgets\n"
                "/setbudget <category> <limit>\n"
                "/portfolio — show portfolios\n"
                "/stats — quick savings/budget heuristics\n"
                "/link — get dashboard link\n"
                "/consent — opt-in/out for AI consultant\n"
                "/export — get CSV of recent transactions\n"
            )
            await reply(help_text)
            return {"ok": True}

        # /addincome 50000 monthly salary
        if cmd == "/addincome":
            if len(args) < 1:
                await reply("Usage: /addincome <amount> [frequency] [description]")
                return {"ok": True}
            amount = parse_amount(args[0])
            if amount is None:
                await reply("Could not parse amount. Usage: /addincome 50000 monthly salary")
                return {"ok": True}
            frequency = args[1] if len(args) >= 2 else "monthly"
            desc = " ".join(args[2:]) if len(args) >= 3 else ""
            sb_income = {
                "telegram_id": sender_id,
                "amount": amount,
                "frequency": frequency,
                "description": desc,
                "created_at": datetime.datetime.utcnow().isoformat()
            }
            background_tasks.add_task(supabase_insert, "incomes", sb_income)
            await reply(f"Added income {format_currency(amount)} ({frequency})")
            return {"ok": True}

        # /income -> list incomes
        if cmd == "/income":
            rows = await supabase_select("incomes", params=f"telegram_id=eq.{sender_id}", select="amount,frequency,description", limit=50)
            if not rows:
                await reply("No incomes found. Add one with /addincome <amount> monthly salary")
                return {"ok": True}
            lines = []
            total = 0.0
            for r in rows:
                amt = float(r.get("amount", 0) or 0)
                total += amt if (r.get("frequency", "monthly") == "monthly") else amt  # simple assumption
                desc = r.get("description") or ""
                freq = r.get("frequency") or ""
                lines.append(f"{format_currency(amt)} — {freq} — {desc}")
            msg = "Your incomes:\n" + "\n".join(lines) + f"\n\nTotal monthly (approx): {format_currency(total)}"
            await reply(msg)
            return {"ok": True}

        # /addexpense 250 food lunch
        if cmd == "/addexpense":
            if len(args) < 2:
                await reply("Usage: /addexpense <amount> <category> [note]")
                return {"ok": True}
            amount = parse_amount(args[0])
            if amount is None:
                await reply("Could not parse amount. Usage: /addexpense 250 food lunch")
                return {"ok": True}
            category = args[1]
            note = " ".join(args[2:]) if len(args) >= 3 else ""
            sb_exp = {
                "telegram_id": sender_id,
                "amount": amount,
                "category": category,
                "note": note,
                "created_at": datetime.datetime.utcnow().isoformat()
            }
            background_tasks.add_task(supabase_insert, "expenses", sb_exp)
            await reply(f"Added expense {format_currency(amount)} ({category})")
            return {"ok": True}

        # /expense -> recent expenses
        if cmd == "/expense":
            rows = await supabase_select("expenses", params=f"telegram_id=eq.{sender_id}", select="amount,category,note,created_at", limit=5)
            if not rows:
                await reply("No expenses recorded. Add one with /addexpense 250 food lunch")
                return {"ok": True}
            lines = []
            total = 0.0
            for r in rows:
                amt = float(r.get("amount", 0) or 0)
                total += amt
                lines.append(f"{format_currency(amt)} — {r.get('category','')} — {r.get('note','')}")
            msg = "Recent expenses:\n" + "\n".join(lines) + f"\n\nTotal (last {len(rows)}): {format_currency(total)}"
            await reply(msg)
            return {"ok": True}

        # /setbudget <category> <limit>
        if cmd == "/setbudget":
            if len(args) < 2:
                await reply("Usage: /setbudget <category> <monthly_limit>")
                return {"ok": True}
            category = args[0]
            limit_amount = parse_amount(args[1])
            if limit_amount is None:
                await reply("Could not parse limit amount. Example: /setbudget food 5000")
                return {"ok": True}
            sb_budget = {
                "telegram_id": sender_id,
                "category": category,
                "monthly_limit": limit_amount,
                "created_at": datetime.datetime.utcnow().isoformat()
            }
            background_tasks.add_task(supabase_insert, "budgets", sb_budget)
            await reply(f"Set budget for {category} = {format_currency(limit_amount)} / month")
            return {"ok": True}

        # /budget -> show budgets & percent used (compute from expenses)
        if cmd == "/budget":
            budgets = await supabase_select("budgets", params=f"telegram_id=eq.{sender_id}", select="category,monthly_limit", limit=100)
            if not budgets:
                await reply("No budgets set. Use /setbudget <category> <limit>")
                return {"ok": True}
            # compute usage per category for current month (simple approach)
            now = datetime.datetime.utcnow()
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
            expenses = await supabase_select("expenses", params=f"telegram_id=eq.{sender_id}&created_at=gte.{month_start}", select="amount,category", limit=1000)
            usage = {}
            for e in expenses:
                cat = e.get("category") or "other"
                usage[cat] = usage.get(cat, 0) + float(e.get("amount") or 0)
            lines = []
            for b in budgets:
                cat = b.get("category")
                limit_amt = float(b.get("monthly_limit") or 0)
                used = usage.get(cat, 0)
                pct = int((used / limit_amt) * 100) if limit_amt > 0 else 0
                lines.append(f"{cat}: {format_currency(used)} used of {format_currency(limit_amt)} — {pct}%")
            await reply("Budgets:\n" + "\n".join(lines))
            return {"ok": True}

        # /portfolio -> quick summary
        if cmd == "/portfolio":
            rows = await supabase_select("portfolios", params=f"telegram_id=eq.{sender_id}", select="id,name,data", limit=50)
            if not rows:
                await reply("No portfolios stored. Add via the app dashboard.")
                return {"ok": True}
            lines = []
            for r in rows:
                name = r.get("name") or "unnamed"
                data = r.get("data") or {}
                # try to compute approximate value if holdings have 'value' field
                total_val = 0.0
                if isinstance(data, dict):
                    holdings = data.get("holdings") or []
                    for h in holdings:
                        total_val += float(h.get("value") or 0)
                lines.append(f"{name} — approx value {format_currency(total_val)}")
            await reply("Portfolios:\n" + "\n".join(lines))
            return {"ok": True}

        # /stats -> quick heuristics: savings percent
        if cmd == "/stats":
            incomes = await supabase_select("incomes", params=f"telegram_id=eq.{sender_id}", select="amount,frequency", limit=50)
            expenses = await supabase_select("expenses", params=f"telegram_id=eq.{sender_id}", select="amount", limit=1000)
            total_income = sum([float(i.get("amount") or 0) for i in incomes])
            total_expenses = sum([float(e.get("amount") or 0) for e in expenses])
            if total_income == 0:
                await reply("No income recorded. Add one with /addincome <amount> monthly salary")
                return {"ok": True}
            savings = max(total_income - total_expenses, 0)
            pct = int((savings / total_income) * 100) if total_income > 0 else 0
            msg = f"Estimated savings: {format_currency(savings)} ({pct}% of income). Suggested target: 20% (50/30/20 heuristic)."
            await reply(msg)
            return {"ok": True}

        # /link -> send dashboard link (if APP_URL configured)
        if cmd == "/link":
            url = APP_URL or "<dashboard-url>"
            await reply(f"Open your dashboard: {url}")
            return {"ok": True}

        # /consent -> toggle consent for LLM/detailed advice
        if cmd == "/consent":
            # if an argument provided: /consent yes/no
            opt = args[0].lower() if args else ""
            val = True if opt in ("yes", "y", "1", "true") else False if opt in ("no", "n", "0", "false") else None
            if val is None:
                await reply("Usage: /consent yes|no\nExample: /consent yes (to allow detailed AI advice)")
                return {"ok": True}
            sb_consent = {
                "telegram_id": sender_id,
                "consented": val,
                "scope": "ai_advice",
                "created_at": datetime.datetime.utcnow().isoformat()
            }
            background_tasks.add_task(supabase_insert, "consents", sb_consent)
            await reply(f"Consent set to: {val}")
            return {"ok": True}

        # /export -> create CSV of recent transactions (simplified)
        if cmd == "/export":
            # generate CSV from recent expenses & incomes (server-side)
            exps = await supabase_select("expenses", params=f"telegram_id=eq.{sender_id}", select="amount,category,note,created_at", limit=500)
            incs = await supabase_select("incomes", params=f"telegram_id=eq.{sender_id}", select="amount,frequency,description,created_at", limit=500)
            # Build CSV text
            lines = ["type,amount,category_or_freq,note_or_desc,created_at"]
            for e in exps:
                lines.append(f"expense,{e.get('amount')},{e.get('category','')},{e.get('note','')},{e.get('created_at','')}")
            for i in incs:
                lines.append(f"income,{i.get('amount')},{i.get('frequency','')},{i.get('description','')},{i.get('created_at','')}")
            csv_text = "\n".join(lines)
            # For MVP: send as a message (may be too large). Ideally: upload to storage and return link.
            # We'll send short confirmation and (optionally) a small preview.
            preview = "\n".join(lines[:10])
            await reply("Generated CSV (preview):\n" + preview + "\n\nFull export available on dashboard.")
            # TODO: upload CSV to storage and return link.
            return {"ok": True}

        # Unrecognized command: small help
        await reply("Command not recognized. Use /help to see available commands.")
        return {"ok": True}

    except Exception as e:
        print("command handling error:", e)
        await reply("An error occurred while processing your command.")
        return {"ok": True}

# ---------- existing endpoints ----------
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
