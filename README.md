# Dungeon AI Bot

Carl-inspired chaos, theatrical narration, and surprisingly practical advice — all wrapped in a Telegram bot that remembers your party’s misadventures and ramps up the drama the more you poke it.

## Why you might want this
- Daily **chaos ramp** that cranks temperature & tone as interactions pile up, then resets at local dawn.
- **Quest continuity**: `/quest` seeds a hook; `/advice` remembers and builds on it.
- **Achievement spam** with sarcastic omniscient narration driven by `persona_dm.json`.
- **Inline feedback** (👍/👎) logged to SQLite for later review.
- Works in **direct messages or groups**; in groups the bot only replies to commands or mentions.
- Automated **daily admin DM** summarizing usage, votes, and top adventurers.

## How it fits together
- **aiogram 3** handles Telegram updates with async handlers in `main.py`.
- **OpenAI Chat Completions** supply the personality and procedurally-generate quest hooks; chaos ramps tweak temperature on the fly.
- **SQLite (aiosqlite)** tracks users, memories, feedback, and daily counters (`dm_oracle.db` by default).
- **Persona & prompts** live in `persona_dm.json` and `prompts.py`, making it easy to reskin the voice.

## Prerequisites
- Python ≥ 3.10
- [`uv`](https://github.com/astral-sh/uv) (or manage dependencies however you prefer)
- Telegram Bot token from [@BotFather](https://t.me/BotFather)
- OpenAI API key with access to the configured model

## Quick start
```bash
# Install dependencies
uv sync

# Configure secrets (copy example and fill in real values)
cp .env.example .env
# edit .env with your tokens/IDs and any optional overrides

# Run the bot
uv run python main.py
```

The process prints `Dungeon AI Bot is listening...` once polling starts. Stop with `Ctrl+C`.

## Docker
```bash
# Build and run (requires Docker + docker compose)
cp .env.example .env             # fill in secrets if not done already
docker compose up --build        # foreground
# or run in background
docker compose up --build -d
```

The compose stack mounts a named volume (`dm_oracle_data`) at `/data` so the SQLite database survives restarts. To stop and remove containers without deleting the volume, run `docker compose down`. Add `-v` if you want to wipe the stored data.

## Command reference
- `/advice <dilemma>` – primary interaction; bot replies with achievement box, strategy, and snark.
- `/quest` – generates a fresh quest hook via the LLM and stores it as active context for later advice.
- `/stop` – clears your current quest context if you want to start over.
- `/roll [NdM]` – dice roller with narrative flavor (supports up to 20 dice and 1000 faces).
- `/stats` – personal interaction + token counts plus today’s chaos level.
- `/leaderboard` – top users ranked by total interactions.
- `@BotName <text>` in groups – treated as `/advice`.
- **Admin**: `/report_now` forces the daily report DM; `/set_chaos <base> <slope> <max>` live-tunes the ramp.

## Chaos, memory, and reports
- **Chaos ramp**: `CHAOS_BASE + CHAOS_SLOPE * interactions_today` (clamped to `CHAOS_MAX`) raises OpenAI temperature and pushes the persona louder.
- **Memory**: the bot stores short snippets of recent advice per user (`MAX_HISTORY_PER_USER`) and replays them for continuity.
- **Daily report loop**: at `DAILY_REPORT_HOUR` (local time) the bot DM’s the admin with counts, votes, and the top five users.

## Configuration reference
| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | ✅ | — | BotFather token to connect to Telegram. |
| `OPENAI_API_KEY` | ✅ | — | OpenAI key used by the Chat Completions client. |
| `OPENAI_MODEL` | ⛔ | `gpt-4o-mini` | Model identifier passed to OpenAI. |
| `LLM_PROVIDER` | ⛔ | `openai` | Choose `openai` (cloud) or `ollama` (local). |
| `OLLAMA_MODEL` | ⛔ | `dolphin-venice:latest` | Model name to use when `LLM_PROVIDER=ollama`. |
| `OLLAMA_HOST` | ⛔ | `http://localhost:11434` | Ollama server URL if not using the default. |
| `DB_PATH` | ⛔ | `./dm_oracle.db` | SQLite database location. |
| `BOT_NAME` | ⛔ | `Dungeon AI Bot` | Printed on startup. |
| `TIMEZONE` | ⛔ | `America/Los_Angeles` | Used for chaos reset and report scheduling. |
| `ADMIN_USER_ID` | ⛔ | `0` (disabled) | Telegram user ID that should receive daily summaries. |
| `DAILY_REPORT_HOUR` | ⛔ | `23` | Local hour (0–23) for the scheduled report. |
| `CHAOS_BASE` | ⛔ | `0.5` | Starting chaos multiplier. |
| `CHAOS_SLOPE` | ⛔ | `0.015` | Chaos growth per interaction. |
| `CHAOS_MAX` | ⛔ | `1.3` | Maximum chaos cap. |
| `SYSTEM_TEMPERATURE` | ⛔ | `0.7` | Base OpenAI temperature before chaos adjustments. |
| `MAX_HISTORY_PER_USER` | ⛔ | `5` | Number of recent memories retrieved per user. |

✅ = required, ⛔ = optional.

## Customization ideas
- **Persona**: tweak `persona_dm.json` to change tone, structure, or safety constraints.
- **Quest hooks & flavor**: adjust `generate_quest_hook` in `main.py` or expand `QUEST_FALLBACKS` for offline resilience.
- **Storage**: point `DB_PATH` elsewhere or swap `aiosqlite` for a different backend.
- **Prompts**: adjust `prompts.py` if you want a different user/system framing.

## Development tips
- The project uses async-heavy aiogram; prefer running with `uv run python main.py` to reuse the managed virtualenv.
- Data accumulates in `dm_oracle.db` (SQLite). Delete the file to reset state during local testing.
- There is no dedicated test suite yet; manual runs are the norm. Consider adding integration tests around command handlers as you expand functionality.

---

Need help? File an issue or reach out to the admin user configured in your deployment. Happy adventuring!
