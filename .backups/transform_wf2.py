"""Surgical prompt fixes for workflow 2 (Oberoi cold-caller).

Fixes: (1) halting "..." speech, (2) bloat/latency, (3) repetition emphasis —
WITHOUT touching the knowledge base, pricing, objection handling, topic
boundaries, or absolute rules (the anti-hallucination guardrails).
Every replacement asserts its target was found, so a silent no-op is impossible.
"""
import json
import re
import sys

SRC = ".backups/wf2-20260622-164947.json"
OUT = ".backups/wf2-new.json"

d = json.load(open(SRC))
nodes = {str(n["id"]): n for n in d["nodes"]}


def replace_exact(text, old, new, label):
    if old not in text:
        sys.exit(f"FAIL [{label}]: target string not found")
    return text.replace(old, new, 1)


# ---------- NODE 4 (Global) ----------
g = nodes["4"]["data"]["prompt"]
before = len(g)

# 1) Replace the entire "SPEECH SMOOTHNESS" section (slow/"..." instructions)
#    with a natural-conversation section. Regex spans header -> next header.
NEW_SPEECH = """# SPEECH STYLE — NATURAL, FLUID CONVERSATION

Speak like a real person on a normal phone call — warm, clear, and natural.

## Pace and delivery
- Speak at a normal, natural conversational pace. Do not speak slowly or drag.
- Do NOT insert "..." pauses, ellipses, or artificial breaths between words or phrases. Speak in complete, flowing sentences.
- Say pricing, sizes, and numbers clearly and naturally in one smooth sentence — never break them into halting fragments.
- Be warm and composed — never robotic, never over-excited.

## What NEVER to do
- NEVER use "Great!", "Fantastic!", "Wonderful!", "Amazing!", "Awesome!" as exclamations
- NEVER stack two questions in one turn — ask one, then wait
- NEVER fill silence with "umm", "so", "okay" — wait quietly after a question
- NEVER sound like you are reading a list or reciting a script

"""
g2, n = re.subn(
    r"# SPEECH SMOOTHNESS.*?(?=# ACCENT AND SPEECH STYLE)",
    NEW_SPEECH,
    g,
    flags=re.S,
)
if n != 1:
    sys.exit(f"FAIL [speech section]: expected 1 match, got {n}")
g = g2

# 2) Persona pacing line -> natural
g = replace_exact(
    g,
    "- Measured pace — luxury clients appreciate thoughtful conversation",
    "- Natural, warm conversational pace",
    "persona pace",
)

# 3) Accent pacing line -> natural
g = replace_exact(
    g,
    "- Measured, clear pace — not rushed, not clipped — the pace of a senior relationship manager at a private bank",
    "- Clear, natural pace — warm and professional",
    "accent pace",
)

# 4) Absolute rule 18 (slow down further) -> natural
g = replace_exact(
    g,
    "18. NEVER rush. Slow down further when delivering pricing, sizes, or configuration details.",
    "18. Speak at a natural pace — say pricing, sizes, and configurations clearly and smoothly, never in halting fragments.",
    "abs rule 18",
)

nodes["4"]["data"]["prompt"] = g
after4 = len(g)

# ---------- NODE 2 (Main Agenda) — pitch pacing ----------
m = nodes["2"]["data"]["prompt"]
m = replace_exact(
    m,
    "# PROJECT PITCH — THREE BEATS. PAUSE FULLY BETWEEN EACH.\nDeliver each beat slowly and deliberately. One sentence at a time. Read the caller's interest before continuing to the next beat. Never rush through figures.",
    "# PROJECT PITCH — THREE BEATS\nDeliver in three clear, conversational beats. After each beat, briefly check the caller's interest before continuing. Speak naturally and fluidly — never break figures into halting fragments.",
    "node2 pitch pacing",
)
nodes["2"]["data"]["prompt"] = m

json.dump(d, open(OUT, "w"), ensure_ascii=False, indent=None)
print(f"NODE 4 (Global): {before} -> {after4} chars")
print(f"NODE 2 (Main Agenda): updated pitch pacing")
print(f"wrote {OUT}")
# Sanity: ensure knowledge base + guardrails survived
kb_markers = [
    "COMPLETE KNOWLEDGE BASE",
    "Oberoi Three Sixty North",
    "Forty thousand to forty-five thousand rupees per square foot",
    "ABSOLUTE RULES — NEVER BREAK",
    "OBJECTION HANDLING REFERENCE",
    'Do NOT insert "..."',
]
gfinal = nodes["4"]["data"]["prompt"]
for mk in kb_markers:
    print(("  OK   " if mk in gfinal else "  MISSING ") + mk)
