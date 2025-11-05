# Dungeon AI Bot

A Telegram “Dungeon AI” (Carl-inspired) with:
- Daily **unhinged chaos ramp** (resets at dawn by timezone).
- **Achievement-flavored** persona + sarcastic theatrical narration.
- **Quest → Advice** continuity (remembers the last quest).
- **Rate-my-advice** (👍/👎) logging.
- **Group-aware** (commands or @mention).
- **Daily report** DM to admin with totals.

## Commands
- `/advice <your dilemma>` — snarky, useful guidance  
- `/quest` — a one-shot hook to spark chaos (then use `/advice` to plan; bot remembers the quest)  
- `/roll d20` — the bones never lie (supports `NdM` and richer flavor)  
- `/stats` — your usage; `/leaderboard` — top fools  
- Admin: `/report_now`, `/set_chaos <base> <slope> <max>`

## Setup
```bash
uv sync
cp .env.example .env   # fill TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, ADMIN_USER_ID
uv run python main.py
