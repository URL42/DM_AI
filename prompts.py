# prompts.py
import json
from pathlib import Path
from typing import List, Dict

def load_persona(path: str = "persona_dm.json") -> Dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def build_system_prompt(persona: Dict, memories: List[str], chaos_float: float) -> str:
    rules = "\n- ".join(persona["style_rules"])
    mem = "\n".join(f"- {m}" for m in memories) if memories else "None recorded."
    chaos_note = (
        f"Chaos Meter: {chaos_float:.2f} (0=stoic, 1.0=spicy, 1.5=unhinged-but-safe). "
        f"As chaos rises, increase theatrical narration, absurd tangents, and sarcastic flair — but keep advice concrete and refusals safe."
    )
    return (
        f"You are {persona['name']} — {persona.get('description','')}\n"
        f"Voice: {persona['voice']['tone']} (rating: {persona['voice']['rating']}).\n"
        f"{chaos_note}\n\n"
        f"Style rules:\n- {rules}\n\n"
        f"If giving advice, follow this structure:\n- " + "\n- ".join(persona["advice_structure"]) + "\n\n"
        f"Recent party memories (short notes for callbacks):\n{mem}"
    )

def build_user_prompt(username: str, question: str, last_quest: str | None) -> str:
    quest_line = f"\nRelated active quest: {last_quest}" if last_quest else ""
    return (
        f"Adventurer @{username} asks:\n"
        f"\"{question}\"{quest_line}\n\n"
        f"Respond as the Dungeon AI, in-character. Include an ACHIEVEMENT box if this constitutes a meaningful action."
    )
