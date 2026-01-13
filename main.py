# main.py
import os, time, random, re, asyncio, json
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional, Dict
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.enums import ChatType

from openai import OpenAI

from db import DB
from prompts import load_persona, build_system_prompt, build_user_prompt

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
DB_PATH = os.getenv("DB_PATH", "./dm_oracle.db")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "dolphin-venice:latest")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

TIMEZONE = os.getenv("TIMEZONE", "America/Los_Angeles")
BOT_NAME = os.getenv("BOT_NAME", "Dungeon AI Bot")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DAILY_REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", "23"))

CHAOS_BASE = float(os.getenv("CHAOS_BASE", "0.5"))
CHAOS_SLOPE = float(os.getenv("CHAOS_SLOPE", "0.015"))
CHAOS_MAX = float(os.getenv("CHAOS_MAX", "1.3"))

MAX_HISTORY = int(os.getenv("MAX_HISTORY_PER_USER", "5"))
SYSTEM_TEMPERATURE = float(os.getenv("SYSTEM_TEMPERATURE", "0.7"))
MAX_MEMORIES_TOTAL = int(os.getenv("MAX_MEMORIES_PER_USER", "100"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "30"))
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "5"))
ACHIEVEMENT_SOUND_COOLDOWN = int(os.getenv("ACHIEVEMENT_SOUND_COOLDOWN_SEC", "20"))
ALLOWED_USER_IDS = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip().isdigit()}
def parse_user_labels(raw: str) -> Dict[int, str]:
    labels = {}
    for part in raw.split(","):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip()
        v = v.strip()
        if k.isdigit() and v:
            labels[int(k)] = v
    return labels

USER_LABELS = parse_user_labels(os.getenv("USER_LABELS", ""))
def log_debug(*parts):
    print("[bot]", *parts, flush=True)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "dolphin-venice:latest")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MAX_MEMORIES_TOTAL = int(os.getenv("MAX_MEMORIES_PER_USER", "100"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "30"))
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "5"))
ACHIEVEMENT_SOUND_COOLDOWN = int(os.getenv("ACHIEVEMENT_SOUND_COOLDOWN_SEC", "20"))
ALLOWED_USER_IDS = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip().isdigit()}
if LLM_PROVIDER not in ("openai", "ollama"):
    raise ValueError("LLM_PROVIDER must be either 'openai' or 'ollama'")

openai_client = None
ollama_client = None
if LLM_PROVIDER == "openai":
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
else:
    import ollama  # Only needed when using the local provider
    ollama_client = ollama.Client(host=OLLAMA_HOST)

log_debug(f"Startup allowlist={ALLOWED_USER_IDS} admin={ADMIN_USER_ID} provider={LLM_PROVIDER}")
if USER_LABELS:
    log_debug(f"User labels loaded: {USER_LABELS}")

db = DB(DB_PATH)
persona = load_persona("persona_dm.json")
TZ = ZoneInfo(TIMEZONE)
SOUND_DIR = Path(__file__).parent / "sounds"
SOUNDS = {
    "achievement": SOUND_DIR / "new_achievement.mp3",
    "quest": SOUND_DIR / "new_quest.mp3",
}

QUEST_FALLBACKS = [
    "A suspicious merchant offers a map to a sunken archive guarded by silent bells.",
    "Each dawn, footprints circle every door—yet no watcher sees the walker.",
    "A cursed ledger predicts debts that come due in blood by the next full moon."
]

ACHIEVEMENT_BOX_RE = re.compile(
    r"🏆\s*ACHIEVEMENT UNLOCKED:[\s\S]*?(?:Reward:[^\n]*)(?:\n|$)", re.IGNORECASE
)

RECENT_QUEST_HOOKS: list[str] = []
RATE_BUCKETS: dict[int, list[float]] = {}
LAST_ACHIEVEMENT_SOUND: dict[int, float] = {}
THREAD_MAX = 3

async def send_sound(kind: str, chat_id: int, caption: Optional[str] = None):
    path = SOUNDS.get(kind)
    if not path or not path.is_file():
        return
    try:
        await bot.send_audio(chat_id, FSInputFile(path), caption=caption)
    except Exception:
        # Sound should not block the primary message; ignore playback issues.
        pass

def user_allowed(user_id: int) -> bool:
    if user_id == ADMIN_USER_ID:
        return True
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS

def display_user(user_id: int, username: Optional[str]) -> str:
    if user_id in USER_LABELS:
        return USER_LABELS[user_id]
    if username:
        return f"@{username}"
    return "unknown"

def check_rate_limit(user_id: int, now_ts: float) -> bool:
    bucket = RATE_BUCKETS.setdefault(user_id, [])
    cutoff = now_ts - RATE_LIMIT_WINDOW
    bucket[:] = [t for t in bucket if t >= cutoff]
    if len(bucket) >= RATE_LIMIT_MAX:
        return False
    bucket.append(now_ts)
    return True

async def guard_access(message: Message) -> bool:
    uid = message.from_user.id
    if not user_allowed(uid):
        log_debug(f"access denied uid={uid}")
        await message.reply("Access restricted. Ask the admin to be added to the allowlist.")
        return False
    if not check_rate_limit(uid, time.time()):
        log_debug(f"rate limited uid={uid}")
        await message.reply("⏳ Cooldown — too many requests too quickly. Try again in a moment.")
        return False
    log_debug(f"access ok uid={uid}")
    return True

def local_day_key(ts: Optional[int] = None) -> str:
    dt = datetime.fromtimestamp(ts or time.time(), TZ)
    return dt.strftime("%Y-%m-%d")

def local_day_bounds(day: Optional[str] = None):
    if not day:
        day = local_day_key()
    d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ)
    d1 = d0 + timedelta(days=1)
    return int(d0.timestamp()), int(d1.timestamp())

def clamp(x, a, b): return max(a, min(b, x))
def compute_chaos(today_interactions: int) -> float:
    return clamp(CHAOS_BASE + CHAOS_SLOPE * today_interactions, 0.2, CHAOS_MAX)

def quest_recently_used(hook: str) -> bool:
    key = hook.lower().strip()
    return key in RECENT_QUEST_HOOKS

def remember_quest(hook: str):
    key = hook.lower().strip()
    if not key:
        return
    RECENT_QUEST_HOOKS.append(key)
    if len(RECENT_QUEST_HOOKS) > 20:
        RECENT_QUEST_HOOKS.pop(0)

def load_thread(raw: Optional[str]) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and "user" in d and "bot" in d]
    except Exception:
        return []
    return []

def trim_thread(exchanges: list[dict]) -> list[dict]:
    return exchanges[-THREAD_MAX:] if len(exchanges) > THREAD_MAX else exchanges

def build_thread_prompt(exchanges: list[dict], new_text: str, last_quest: Optional[str]) -> str:
    blocks = []
    for ex in exchanges:
        blocks.append(f"User: {ex['user']}\nDungeon AI: {ex['bot']}")
    thread_txt = "\n\n".join(blocks)
    quest_note = f"\nConsider current quest: {last_quest}" if last_quest else ""
    user_line = new_text or "Continue the guidance on this same topic."
    return f"Continue this conversation using the prior exchanges.\n{thread_txt}\n\nNew request: {user_line}{quest_note}"

def is_group(msg: Message) -> bool:
    return msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

def bot_is_mentioned(msg: Message, me: types.User) -> bool:
    if not msg.entities: return False
    for e in msg.entities:
        if e.type == "mention" and msg.text[e.offset:e.offset+e.length].lstrip("@").lower() == me.username.lower():
            return True
    return False

def infer_alignment(text: str) -> str:
    t = text.lower()
    score = 0
    if any(w in t for w in ["policy", "plan", "process", "deadline", "sprint"]): score += 1
    if any(w in t for w in ["idk", "wing", "gamble", "chaos", "yolo", "improvise"]): score -= 1
    if score > 0: return "LAW"
    if score < 0: return "CHA"
    return "NEU"

async def get_today_interactions() -> int:
    snap = await db.day_snapshot(local_day_key())
    return int(snap.get("interactions", 0)) if snap else 0

# NOTE: Regular function so asyncio.to_thread works
def generate_reply(username: str, question: str, memories: list[str], chaos: float, last_quest: Optional[str]):
    system = build_system_prompt(persona, memories, chaos)
    user = build_user_prompt(username, question, last_quest)
    temp = clamp(SYSTEM_TEMPERATURE + (chaos - 0.5), 0.2, 1.5)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user}
    ]

    if LLM_PROVIDER == "ollama":
        resp = ollama_client.chat(model=OLLAMA_MODEL, messages=messages, options={"temperature": temp})
        content = resp["message"]["content"].strip()
        ptoks = ctoks = 0
    else:
        resp = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=temp,
            messages=messages
        )
        content = resp.choices[0].message.content.strip()
        ptoks = getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0
        ctoks = getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0
    return content, ptoks, ctoks, temp

def clean_quest_hook(raw: str) -> str:
    """Strip achievement boxes or trailing whitespace from quest output."""
    cleaned = ACHIEVEMENT_BOX_RE.sub(" ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

def generate_quest_hook(chaos: float):
    chaos_note = (
        f"Quest hook request. Chaos meter {chaos:.2f} (0=restrained, 1=spicy, 1.5=absurd). "
        "Higher chaos should lean into weirder stakes, uncanny details, or surreal NPC motives."
    )
    system = (
        f"You are {persona['name']} — {persona.get('description','')}\n"
        f"Voice: {persona['voice']['tone']}.\n"
        f"{chaos_note}\n\n"
        "Task: Craft one NEW quest hook for an adventuring party.\n"
        "- 1–2 sentences, vivid, immediately playable.\n"
        "- No ACHIEVEMENT boxes, no bullet points, no meta commentary.\n"
        "- Avoid repeating recent hook themes; steer clear of egg/breakfast motifs unless explicitly requested.\n"
        "- Output ONLY the quest hook text."
    )
    user = (
        "Deliver a brand-new quest hook that hints at a conflict, an unusual locale or relic, and a quirky constraint or twist. "
        "Make it feel like the start of an epic side quest. Stay under 50 words."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user}
    ]
    if LLM_PROVIDER == "ollama":
        resp = ollama_client.chat(
            model=OLLAMA_MODEL,
            messages=messages,
            options={"temperature": clamp(SYSTEM_TEMPERATURE + (chaos - 0.5), 0.2, 1.5)}
        )
        content = resp["message"]["content"].strip().replace("\n", " ")
        ptoks = ctoks = 0
    else:
        resp = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=clamp(SYSTEM_TEMPERATURE + (chaos - 0.5), 0.2, 1.5),
            messages=messages
        )
        content = resp.choices[0].message.content.strip().replace("\n", " ")
        ptoks = getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0
        ctoks = getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0
    return clean_quest_hook(content), ptoks, ctoks

def advice_buttons(message_row_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Wise counsel", callback_data=f"rate:{message_row_id}:up"),
        InlineKeyboardButton(text="👎 Fool’s errand", callback_data=f"rate:{message_row_id}:down")
    ]])

WELCOME_TEXT = (
    "⚔️ *Dungeon AI Bot*\n"
    "I am the Dungeon AI.\n\n"
    "• /advice <your dilemma> — snarky, useful guidance\n"
    "• /quest — a one-shot hook to spark chaos\n"
    "• /stop — clear your current quest context\n"
    "• /roll d20 — the bones never lie\n"
    "• /stats — your usage; /leaderboard — top fools\n"
    "The more you invoke me today, the wilder I get. Dawn resets my temper."
)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not await guard_access(message):
        return
    now = int(time.time())
    await db.upsert_user(message.from_user.id, message.from_user.username, now)
    await message.reply(WELCOME_TEXT, parse_mode="Markdown")

@dp.message(Command("advice"))
async def cmd_advice(message: Message):
    await handle_advice(message)

@dp.message(Command("stop"))
async def cmd_stop(message: Message):
    if not await guard_access(message):
        return
    now = int(time.time())
    uid = message.from_user.id
    uname = message.from_user.username or "adventurer"
    await db.upsert_user(uid, uname, now)
    await db.delete_memories_with_prefix(uid, "Active quest:")
    await db.clear_last_quest(uid)
    await db.clear_thread(uid)
    await message.reply("🧹 Quest log cleared. Use /quest to start fresh chaos.")

@dp.message(Command("quest"))
async def cmd_quest(message: Message):
    if not await guard_access(message):
        return
    now = int(time.time())
    uid = message.from_user.id
    uname = message.from_user.username or "adventurer"
    await db.upsert_user(uid, uname, now)
    day = local_day_key(now)
    await db.inc_counter(day, "quest")

    today_interactions = await get_today_interactions()
    chaos = compute_chaos(today_interactions)

    try:
        hook, ptoks, ctoks = await asyncio.to_thread(generate_quest_hook, chaos)
    except Exception:
        hook = ""
        ptoks = ctoks = 0

    if not hook:
        hook = random.choice(QUEST_FALLBACKS)
    hook = clean_quest_hook(hook)
    attempts = 0
    while hook and quest_recently_used(hook) and attempts < 2:
        attempts += 1
        try:
            hook, ptoks, ctoks = await asyncio.to_thread(generate_quest_hook, chaos)
            hook = clean_quest_hook(hook)
        except Exception:
            hook = ""
            break
    if not hook:
        # Ensure fallback isn't a duplicate either
        fallback_options = [q for q in QUEST_FALLBACKS if not quest_recently_used(q)]
        hook = random.choice(fallback_options or QUEST_FALLBACKS)
    remember_quest(hook)

    # Store as active quest for user, and as memory for callbacks
    await db.delete_memories_with_prefix(uid, "Active quest:")
    await db.set_last_quest(uid, now, hook)
    await db.add_memory(uid, now, f"Active quest: {hook}", importance=3)
    await db.prune_memories(uid, keep=MAX_MEMORIES_TOTAL)
    await db.clear_thread(uid)

    await db.add_message(uid, now, "quest", ptoks, ctoks, chat_id=message.chat.id)
    await message.reply(
        f"📜 *Quest Hook*\n{hook}\n\n_Use /advice to plot your approach — I’ll remember this quest._",
        parse_mode="Markdown"
    )
    await send_sound("quest", message.chat.id, caption="New quest posted")

def roll_flavor(rolls: list[int], faces: int) -> str:
    n = len(rolls)
    total = sum(rolls)
    crit = (faces in rolls)
    fail = (1 in rolls)
    if n == 1:
        r = rolls[0]
        if r == faces: return "CRITICAL SUCCESS — the bards will *not* shut up about this."
        if r == 1:     return "CRITICAL FAILURE — even the rats look embarrassed."
        pct = r / faces
        if pct >= 0.85: return "A shining triumph, if a bit sweaty."
        if pct >= 0.6:  return "A competent display. Barely."
        if pct >= 0.35: return "A wobbling maybe; fate shrugs."
        return "A tragic whiff — the floor bites back."
    else:
        if crit and not fail: return "At least one die struck gold — fortune grins with too many teeth."
        if fail and not crit: return "Someone rolled a one; the gods required slapstick."
        if crit and fail:     return "Both glory and disaster — a statistically accurate life."
        avg = total / (n * faces)
        if avg >= 0.75: return "The chorus sings. Today you are tall."
        if avg >= 0.5:  return "Adequate chaos. Proceed with smug caution."
        if avg >= 0.3:  return "The bones mutter: 'eh'."
        return "Entropy slaps your hand and steals your lunch."

@dp.message(Command("roll"))
async def cmd_roll(message: Message):
    if not await guard_access(message):
        return
    now = int(time.time())
    uid = message.from_user.id
    uname = message.from_user.username or "adventurer"
    await db.upsert_user(uid, uname, now)
    day = local_day_key(now)
    await db.inc_counter(day, "roll")

    m = re.search(r"(\d*)d(\d+)", message.text or "", re.I)
    if not m:
        await message.reply("Try /roll d20, /roll 2d6, or /roll d24 if you're feeling cursed.")
        return
    n = min(max(int(m.group(1) or "1"), 1), 20)
    faces = min(max(int(m.group(2)), 2), 1000)

    rolls = [random.randint(1, faces) for _ in range(n)]
    total = sum(rolls)
    flavor = roll_flavor(rolls, faces)
    await db.add_message(uid, now, "roll", 0, 0, chat_id=message.chat.id)
    await message.reply(f"🎲 Rolls: {rolls} → *{total}*\n{flavor}", parse_mode="Markdown")
    await db.clear_thread(uid)

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if not await guard_access(message):
        return
    uid = message.from_user.id
    cnt, tokens = await db.get_stats(uid)
    today = await db.day_snapshot(local_day_key())
    chaos = compute_chaos(int(today.get("interactions",0))) if today else CHAOS_BASE
    await message.reply(f"📊 Your stats — Interactions: {cnt}, Tokens used: {tokens}\n"
                        f"🔥 Today’s chaos: {chaos:.2f}")

@dp.message(Command("leaderboard"))
async def cmd_leaderboard(message: Message):
    if not await guard_access(message):
        return
    now = int(time.time())
    rows = await db.leaderboard_since(now - 86400, now, limit=10)
    if not rows:
        await message.reply("No heroes have darkened my doorway yet.")
        return
    lines = []
    for i, (uid, username, cnt) in enumerate(rows):
        label = display_user(uid, username)
        lines.append(f"{i+1}. {label} — {cnt} rites")
    await message.reply("🏆 *Leaderboard*\n" + "\n".join(lines), parse_mode="Markdown")

# ---- Admin: on-demand report & chaos tuning ----

@dp.message(Command("report_now"))
async def cmd_report_now(message: Message):
    if message.from_user.id != ADMIN_USER_ID:
        return
    await send_daily_report()

@dp.message(Command("set_chaos"))
async def cmd_set_chaos(message: Message):
    if message.from_user.id != ADMIN_USER_ID:
        return
    parts = message.text.split()
    if len(parts) != 4:
        await message.reply("Usage: /set_chaos <base> <slope> <max>")
        return
    global CHAOS_BASE, CHAOS_SLOPE, CHAOS_MAX
    CHAOS_BASE, CHAOS_SLOPE, CHAOS_MAX = map(float, parts[1:])
    await message.reply(f"Chaos tuned. base={CHAOS_BASE}, slope={CHAOS_SLOPE}, max={CHAOS_MAX}")

@dp.message(Command("health"))
async def cmd_health(message: Message):
    if message.from_user.id != ADMIN_USER_ID:
        return
    counts = await db.table_counts()
    provider = f"{LLM_PROVIDER} ({OLLAMA_MODEL})" if LLM_PROVIDER == "ollama" else f"{LLM_PROVIDER} ({OPENAI_MODEL})"
    await message.reply(
        f"🩺 Health check\nProvider: {provider}\nUsers: {counts['users']} | Messages: {counts['messages']} | Memories: {counts['memories']}",
        parse_mode="Markdown"
    )

# ---- Group smart handling & DM nudges ----

@dp.message(F.text & ~F.text.startswith("/"), flags={"block": False})
async def on_message(message: Message):
    log_debug("on_message passthrough text:", (message.text or "")[:60])
    if is_group(message):
        me = await bot.get_me()
        if not (message.text and (message.text.startswith("/") or bot_is_mentioned(message, me))):
            return
        if not message.text.startswith("/") and message.text:
            # Treat mention + text as advice
            cleaned = re.sub(r"@\w+\s*", "", message.text, flags=re.I).strip()
            if cleaned:
                message.text = "/advice " + cleaned
                await handle_advice(message)
            return
    else:
        if message.text and not message.text.startswith("/"):
            await message.reply("Use /advice <your dilemma> or /quest. Try /stats.")
            return

# ---- Advice core (links to last quest if present) ----

async def handle_advice(message: Message):
    if not await guard_access(message):
        return
    now = int(time.time())
    uid = message.from_user.id
    uname = message.from_user.username or "adventurer"
    await db.upsert_user(uid, uname, now)
    day = local_day_key(now)

    parts = message.text.split(maxsplit=1)
    user_question = parts[1].strip() if len(parts) > 1 else ""
    await db.inc_counter(day, "advice")

    memories_raw = await db.get_top_memories(uid, MAX_HISTORY)
    memories = [cleaned for cleaned in map(clean_quest_hook, memories_raw) if cleaned]
    today_interactions = await get_today_interactions()
    chaos = compute_chaos(today_interactions)
    last_quest = await db.get_last_quest(uid)
    if last_quest:
        last_quest = clean_quest_hook(last_quest)

    if not user_question and last_quest:
        user_question = f"Strategy to tackle my current quest: {last_quest}"
    elif user_question and last_quest:
        user_question = f"{user_question}\n(Consider current quest context: {last_quest})"

    try:
        reply, ptoks, ctoks, temp_used = await asyncio.to_thread(
            generate_reply, uname, user_question, memories, chaos, last_quest
        )
    except Exception as e:
        await message.reply(f"🛑 The Oracle coughed on a dust mote: {e}")
        return

    msg_row_id = await db.add_message(uid, now, "advice", ptoks, ctoks, chat_id=message.chat.id)

    if len(user_question) > 80:
        snippet = re.sub(r"\s+", " ", user_question)[:180]
        await db.add_memory(uid, now, snippet, importance=2)
        await db.prune_memories(uid, keep=MAX_MEMORIES_TOTAL)

    await db.set_alignment(uid, infer_alignment(user_question))

    await message.reply(
        f"{reply}\n\n_Chaos {chaos:.2f} • Temp {temp_used:.2f}_",
        reply_markup=advice_buttons(message_row_id=msg_row_id),
        parse_mode="Markdown"
    )
    if ACHIEVEMENT_BOX_RE.search(reply):
        now_ts = time.time()
        last_played = LAST_ACHIEVEMENT_SOUND.get(message.chat.id, 0)
        if now_ts - last_played >= ACHIEVEMENT_SOUND_COOLDOWN:
            LAST_ACHIEVEMENT_SOUND[message.chat.id] = now_ts
            await send_sound("achievement", message.chat.id, caption="Achievement unlocked")
    thread = trim_thread([{"user": user_question, "bot": reply}])
    await db.set_thread(uid, now, json.dumps(thread))
    log_debug(f"/advice stored thread len={len(thread)} for uid={uid}")

@dp.message(Command("continue"))
async def cmd_continue(message: Message):
    log_debug("/continue invoked")
    if not await guard_access(message):
        log_debug("/continue blocked by guard")
        return
    now = int(time.time())
    uid = message.from_user.id
    uname = message.from_user.username or "adventurer"
    await db.upsert_user(uid, uname, now)
    day = local_day_key(now)
    await db.inc_counter(day, "advice")

    raw_thread = await db.get_thread(uid)
    thread = trim_thread(load_thread(raw_thread))
    log_debug(f"/continue thread len={len(thread)}")
    if not thread:
        await message.reply("No active thread. Start fresh with /advice first.")
        log_debug("/continue no thread stored")
        return

    parts = message.text.split(maxsplit=1)
    user_text = parts[1].strip() if len(parts) > 1 else ""

    memories_raw = await db.get_top_memories(uid, MAX_HISTORY)
    memories = [cleaned for cleaned in map(clean_quest_hook, memories_raw) if cleaned]
    today_interactions = await get_today_interactions()
    chaos = compute_chaos(today_interactions)
    last_quest = await db.get_last_quest(uid)
    if last_quest:
        last_quest = clean_quest_hook(last_quest)

    continue_prompt = build_thread_prompt(thread, user_text, last_quest)

    try:
        reply, ptoks, ctoks, temp_used = await asyncio.to_thread(
            generate_reply, uname, continue_prompt, memories, chaos, last_quest
        )
    except Exception as e:
        await message.reply(f"🛑 The Oracle coughed on a dust mote: {e}")
        log_debug(f"/continue error {e}")
        return

    msg_row_id = await db.add_message(uid, now, "advice", ptoks, ctoks, chat_id=message.chat.id)

    if len(user_text) > 80:
        snippet = re.sub(r"\s+", " ", user_text)[:180]
        await db.add_memory(uid, now, snippet, importance=2)
        await db.prune_memories(uid, keep=MAX_MEMORIES_TOTAL)

    await db.set_alignment(uid, infer_alignment(user_text or continue_prompt))

    await message.reply(
        f"{reply}\n\n_Chaos {chaos:.2f} • Temp {temp_used:.2f}_",
        reply_markup=advice_buttons(message_row_id=msg_row_id),
        parse_mode="Markdown"
    )
    if ACHIEVEMENT_BOX_RE.search(reply):
        now_ts = time.time()
        last_played = LAST_ACHIEVEMENT_SOUND.get(message.chat.id, 0)
        if now_ts - last_played >= ACHIEVEMENT_SOUND_COOLDOWN:
            LAST_ACHIEVEMENT_SOUND[message.chat.id] = now_ts
            await send_sound("achievement", message.chat.id, caption="Achievement unlocked")

    thread.append({"user": user_text or continue_prompt, "bot": reply})
    await db.set_thread(uid, now, json.dumps(trim_thread(thread)))
    log_debug(f"/continue stored thread len={len(thread)} for uid={uid}")

# ---- Rate my advice ----

@dp.callback_query(F.data.startswith("rate:"))
async def on_rate(callback: CallbackQuery):
    try:
        _, msg_id_str, rating = callback.data.split(":")
        rating = "up" if rating == "up" else "down"
        now = int(time.time())
        day = local_day_key(now)
        await db.add_feedback(int(msg_id_str), callback.from_user.id, now, rating)
        await db.inc_votes(day, 1 if rating=="up" else 0, 1 if rating=="down" else 0)
        await callback.answer("Noted, adventurer.")
    except Exception:
        await callback.answer("Couldn’t record that, the quill snapped.")

# ---- Daily report ----

async def send_daily_report():
    day = local_day_key()
    d0, d1 = local_day_bounds(day)
    unique_users = await db.compute_unique_users_today(d0, d1)
    await db.set_unique_users(day, unique_users)
    snap = await db.day_snapshot(day) or {}
    rows = await db.leaderboard_since(d0, d1, limit=5)
    lb_parts = []
    for i, (uid, username, cnt) in enumerate(rows):
        lb_parts.append(f"{i+1}. {display_user(uid, username)} — {cnt}")
    lb = "\n".join(lb_parts) or "—"

    text = (
        f"📮 *Dungeon AI Daily Report* — {day}\n"
        f"Interactions: {snap.get('interactions',0)}\n"
        f"Users: {snap.get('unique_users',0)}\n"
        f"Advice: {snap.get('advice_count',0)} | Quests: {snap.get('quest_count',0)} | Rolls: {snap.get('roll_count',0)}\n"
        f"Votes: 👍 {snap.get('upvotes',0)} / 👎 {snap.get('downvotes',0)}\n\n"
        f"Top heroes:\n{lb}"
    )
    if ADMIN_USER_ID:
        try:
            await bot.send_message(ADMIN_USER_ID, text, parse_mode="Markdown")
        except Exception:
            pass

async def daily_report_loop():
    while True:
        now = datetime.now(TZ)
        target = now.replace(hour=DAILY_REPORT_HOUR, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        # FIXED: removed extra ')'
        await asyncio.sleep((target - now).total_seconds())
        await send_daily_report()

async def main():
    await db.init()
    asyncio.create_task(daily_report_loop())
    print(f"{BOT_NAME} is listening...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
