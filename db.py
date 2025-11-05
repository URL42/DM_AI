# db.py
import aiosqlite
from typing import Optional, List, Tuple, Dict

CREATE_SQL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  username TEXT,
  first_seen_ts INTEGER,
  last_seen_ts INTEGER,
  alignment TEXT DEFAULT 'NEU'
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  ts INTEGER,
  kind TEXT,            -- 'advice','quest','roll','other'
  prompt_tokens INTEGER DEFAULT 0,
  completion_tokens INTEGER DEFAULT 0,
  chat_id INTEGER DEFAULT NULL,
  FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  ts INTEGER,
  snippet TEXT,
  importance INTEGER DEFAULT 1,
  FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER,
  user_id INTEGER,
  ts INTEGER,
  rating TEXT CHECK(rating IN ('up','down'))
);
CREATE TABLE IF NOT EXISTS daily_counters (
  day TEXT PRIMARY KEY,       -- 'YYYY-MM-DD' local TZ
  interactions INTEGER DEFAULT 0,
  advice_count INTEGER DEFAULT 0,
  quest_count INTEGER DEFAULT 0,
  roll_count INTEGER DEFAULT 0,
  unique_users INTEGER DEFAULT 0,
  upvotes INTEGER DEFAULT 0,
  downvotes INTEGER DEFAULT 0
);
-- Minimal key-value state per user (e.g., last_quest)
CREATE TABLE IF NOT EXISTS user_state (
  user_id INTEGER,
  key TEXT,
  value TEXT,
  ts INTEGER,
  PRIMARY KEY(user_id, key)
);
"""

class DB:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(CREATE_SQL)
            await db.commit()

    async def upsert_user(self, user_id: int, username: Optional[str], now_ts: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
              INSERT INTO users(user_id, username, first_seen_ts, last_seen_ts)
              VALUES (?, ?, ?, ?)
              ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, last_seen_ts=excluded.last_seen_ts
            """, (user_id, username, now_ts, now_ts))
            await db.commit()

    async def add_message(self, user_id: int, now_ts: int, kind: str, ptoks: int, ctoks: int, chat_id: Optional[int] = None) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("""
              INSERT INTO messages(user_id, ts, kind, prompt_tokens, completion_tokens, chat_id)
              VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, now_ts, kind, ptoks, ctoks, chat_id))
            await db.commit()
            return cur.lastrowid

    async def add_memory(self, user_id: int, now_ts: int, snippet: str, importance: int = 1):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
              INSERT INTO memories(user_id, ts, snippet, importance) VALUES (?, ?, ?, ?)
            """, (user_id, now_ts, snippet, importance))
            await db.commit()

    async def delete_memories_with_prefix(self, user_id: int, prefix: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
              DELETE FROM memories
              WHERE user_id = ? AND snippet LIKE ?
            """, (user_id, f"{prefix}%"))
            await db.commit()

    async def get_top_memories(self, user_id: int, limit: int = 5) -> List[str]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("""
              SELECT snippet FROM memories
              WHERE user_id = ?
              ORDER BY importance DESC, ts DESC
              LIMIT ?
            """, (user_id, limit))
            rows = await cur.fetchall()
            return [r[0] for r in rows]

    async def get_stats(self, user_id: int) -> Tuple[int, int]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("""
              SELECT COUNT(*), COALESCE(SUM(prompt_tokens + completion_tokens),0)
              FROM messages WHERE user_id = ?
            """, (user_id,))
            row = await cur.fetchone()
            return int(row[0]), int(row[1] or 0)

    async def set_alignment(self, user_id: int, alignment: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE users SET alignment = ? WHERE user_id = ?", (alignment, user_id))
            await db.commit()

    async def leaderboard(self, limit: int = 10):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("""
              SELECT u.username, COUNT(m.id) as cnt
              FROM users u
              LEFT JOIN messages m ON m.user_id = u.user_id
              GROUP BY u.user_id
              ORDER BY cnt DESC
              LIMIT ?
            """, (limit,))
            return await cur.fetchall()

    async def inc_counter(self, day: str, kind: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
              INSERT INTO daily_counters(day, interactions) VALUES(?,1)
              ON CONFLICT(day) DO UPDATE SET interactions = interactions + 1
            """, (day,))
            if kind in ("advice","quest","roll"):
                col = {"advice":"advice_count","quest":"quest_count","roll":"roll_count"}[kind]
                await db.execute(f"UPDATE daily_counters SET {col} = {col}+1 WHERE day = ?", (day,))
            await db.commit()

    async def inc_votes(self, day: str, up: int, down: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
              INSERT INTO daily_counters(day, upvotes, downvotes)
              VALUES (?, ?, ?)
              ON CONFLICT(day) DO UPDATE SET
                upvotes = upvotes + excluded.upvotes,
                downvotes = downvotes + excluded.downvotes
            """, (day, up, down))
            await db.commit()

    async def add_feedback(self, message_id: int, user_id: int, ts: int, rating: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT INTO feedback(message_id,user_id,ts,rating) VALUES(?,?,?,?)",
                             (message_id, user_id, ts, rating))
            await db.commit()

    async def compute_unique_users_today(self, day_start_ts: int, day_end_ts: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("""
                SELECT COUNT(DISTINCT user_id)
                FROM messages
                WHERE ts >= ? AND ts < ?
            """, (day_start_ts, day_end_ts))
            row = await cur.fetchone()
            return int(row[0] or 0)

    async def day_snapshot(self, day: str) -> Dict:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT interactions, advice_count, quest_count, roll_count, unique_users, upvotes, downvotes FROM daily_counters WHERE day = ?", (day,))
            row = await cur.fetchone()
            if not row: return {}
            keys = ["interactions","advice_count","quest_count","roll_count","unique_users","upvotes","downvotes"]
            return dict(zip(keys, row))

    async def set_unique_users(self, day: str, n: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE daily_counters SET unique_users = ? WHERE day = ?", (n, day))
            await db.commit()

    # -------- simple user_state K/V for last quest --------
    async def set_last_quest(self, user_id: int, ts: int, text: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                INSERT INTO user_state(user_id, key, value, ts)
                VALUES (?, 'last_quest', ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, ts=excluded.ts
            """, (user_id, text, ts))
            await db.commit()

    async def get_last_quest(self, user_id: int) -> Optional[str]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT value FROM user_state WHERE user_id=? AND key='last_quest'", (user_id,))
            row = await cur.fetchone()
            return row[0] if row else None
