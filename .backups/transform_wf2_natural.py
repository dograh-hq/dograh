"""Restore the FULL original workflow-2 prompt, naturalizing speech IN PLACE.

Keeps every line of the old prompt byte-identical EXCEPT the pacing/"..."
instructions (and the "..." in the example transition phrases, which taught
the model to insert ellipses). Nothing factual is touched. Every replacement
asserts its target was found.
"""
import json
import sys

SRC = ".backups/wf2-20260622-164947.json"  # the ORIGINAL backup
OUT = ".backups/wf2-natural.json"

d = json.load(open(SRC))
nodes = {str(n["id"]): n for n in d["nodes"]}


def rep(text, old, new, label):
    if old not in text:
        sys.exit(f"FAIL [{label}]: not found")
    return text.replace(old, new, 1)


# ---------- NODE 4 (Global) — in-place naturalization ----------
g = nodes["4"]["data"]["prompt"]
before = len(g)

g = rep(g, "The core principle: **slow down and let every word land.**",
        "The core principle: **speak naturally and clearly, like a real conversation — never robotic, never halting.**", "core")
g = rep(g, "- Speak at a measured, unhurried pace throughout the call. Never rush.",
        "- Speak at a natural, conversational pace throughout the call.", "p1")
g = rep(g, '- Use natural soft pauses — indicated by "..." — to create breathing room between thoughts.',
        '- Speak in complete, flowing sentences. Do NOT insert "...", ellipses, or artificial pauses between words or phrases.', "p2")
g = rep(g, "- Deliver pricing, configuration details, and numbers especially slowly — give the caller time to absorb each figure before moving to the next.",
        "- Deliver pricing, configuration details, and numbers clearly and naturally, in one smooth sentence.", "p3")
g = rep(g, "- One idea per sentence. One sentence, then a breath. Then the next.",
        "- Let your sentences flow naturally into one another.", "p4")
g = rep(g, "- Never chain multiple facts into a single breath. Break them apart.",
        "- Keep each point clear and easy to follow.", "p5")
g = rep(g, "- NEVER speak faster when delivering important information — slow down further instead",
        "- Keep a steady, natural pace throughout — never slow to a halt or add filler pauses", "p6")
# Example transition phrases: drop the trailing "..." so the model stops echoing them
for w in ["Of course", "Certainly", "Indeed", "I understand completely",
          "That is quite right", "Allow me to share"]:
    g = rep(g, f'"{w}..."', f'"{w},"', f"phrase {w}")
# Persona + accent pacing lines
g = rep(g, "- Measured pace — luxury clients appreciate thoughtful conversation",
        "- Natural, warm conversational pace", "persona")
g = rep(g, "- Measured, clear pace — not rushed, not clipped — the pace of a senior relationship manager at a private bank",
        "- Clear, natural pace — warm and professional", "accent")
g = rep(g, "18. NEVER rush. Slow down further when delivering pricing, sizes, or configuration details.",
        "18. Speak at a natural, even pace — say pricing, sizes, and configurations clearly and smoothly, never in halting fragments.", "rule18")

nodes["4"]["data"]["prompt"] = g
after4 = len(g)

# ---------- NODE 2 (Main Agenda) — pitch pacing ----------
m = nodes["2"]["data"]["prompt"]
m = rep(m,
        "# PROJECT PITCH — THREE BEATS. PAUSE FULLY BETWEEN EACH.\nDeliver each beat slowly and deliberately. One sentence at a time. Read the caller's interest before continuing to the next beat. Never rush through figures.",
        "# PROJECT PITCH — THREE BEATS\nDeliver in three clear, conversational beats. After each beat, briefly check the caller's interest before continuing. Speak naturally and fluidly — never break figures into halting fragments.",
        "node2 pitch")
nodes["2"]["data"]["prompt"] = m

json.dump(d, open(OUT, "w"), ensure_ascii=False, indent=None)
print(f"NODE 4 (Global): {before} -> {after4} chars (original 17557 fully preserved minus pacing edits)")
# Sanity — KB/guardrails intact, no "..." pause instruction left, no leftover ellipsis phrases
checks = {
    "KB intact": "COMPLETE KNOWLEDGE BASE" in g and "Forty thousand to forty-five thousand" in g,
    "all 19 absolute rules": "19. NEVER attempt a second recovery" in g,
    "objection handling": "OBJECTION HANDLING REFERENCE" in g,
    "natural rule present": 'Do NOT insert "..."' in g,
    "no slow-pause instruction": "indicated by" not in g,
    "no 'Of course...' ellipsis": '"Of course..."' not in g,
}
for k, v in checks.items():
    print(("  OK   " if v else "  FAIL ") + k)
