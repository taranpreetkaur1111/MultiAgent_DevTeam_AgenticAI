#!/usr/bin/env python3
"""
Demo: Memory Loop — Agent encounters a bug, solves it, then
encounters the SAME bug later and recalls the solution.

This simulates the real workflow:
  Run 1: Agent hits a bug → figures it out → stores solution in FV
  Run 2: Agent gets a similar task → FV recalls the past solution →
          agent skips the debugging and applies the known fix

No LLM needed — this just shows the memory system working.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.interface import (
    get_context,
    record_lesson,
    remember_outcome,
    recall_history,
)

# Clean slate for demo
import os
for f in ["memory/oraclelens_memory.db", "memory/memory_store.json", "memory/audit_log.json"]:
    p = Path(__file__).parent / f
    if p.exists():
        p.unlink()
import shutil
faiss_dir = Path(__file__).parent / "memory" / "oraclelens_faiss"
if faiss_dir.exists():
    shutil.rmtree(faiss_dir)


def divider(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


# =====================================================================
#  RUN 1: First time seeing this bug — agent has to figure it out
# =====================================================================

divider("RUN 1: Developer agent gets a task (FIRST TIME)")

task1 = "Fix the 500 error on POST /api/users — users can't register"

print(f"Task: {task1}\n")

# Agent checks memory before starting
print("[1] Agent checks memory for prior knowledge...")
ctx1 = get_context(
    role="developer",
    task_prompt=task1,
    repo_path=".",
    save_pack=False,
)

fv = ctx1["fv_recall"]
print(f"    Past solutions found: {len(fv['past_solutions'])}")
print(f"    Known mistakes found: {len(fv['known_mistakes'])}")
print(f"    Prior research found: {len(fv['prior_research'])}")

if not fv["past_solutions"] and not fv["known_mistakes"]:
    print("    → No prior knowledge. Agent must debug from scratch.\n")
else:
    print("    → Found prior knowledge! (unexpected for run 1)\n")

# Agent debugs and finds the issue
print("[2] Agent investigates the bug...")
print("    - Reads the error logs: 'IntegrityError: NOT NULL constraint failed: users.email_verified'")
print("    - Reads the migration: email_verified column added as NOT NULL without default")
print("    - Reads the model: User.create() doesn't set email_verified")
print()

print("[3] Agent fixes the bug...")
print("    - Added default=False to email_verified column in migration")
print("    - Added email_verified=False in User.create()")
print("    - Tests pass ✓")
print()

# Agent stores what it learned
print("[4] Agent remembers the solution for next time...")
fact_id = remember_outcome(
    task_prompt=task1,
    outcome="success",
    memory_type="solution",
    details="500 on POST /api/users was caused by email_verified column "
            "added as NOT NULL without a default value. Fix: add default=False "
            "to the migration and set email_verified=False in User.create().",
    repo_path="/home/om/projects/user-api",
    agent_role="developer",
)
print(f"    Stored solution: {fact_id}")

# Also store the mistake pattern
remember_outcome(
    task_prompt=task1,
    outcome="mistake pattern identified",
    memory_type="mistake",
    details="Adding NOT NULL columns without defaults breaks existing INSERT "
            "statements. Always add a default value when adding NOT NULL columns "
            "to tables that already have INSERT paths.",
    repo_path="/home/om/projects/user-api",
    agent_role="developer",
)
print("    Stored mistake pattern for future avoidance")

# Supervisor records a lesson
record_lesson(
    role="supervisor",
    category="database",
    lesson="Always add default values when adding NOT NULL columns to existing tables.",
    context="Run 1 — POST /api/users 500 error",
)
print("    Supervisor recorded lesson in gated memory")

print("\n    ✓ Run 1 complete — bug fixed from scratch, knowledge stored")

# Brief pause to simulate time passing
time.sleep(1)


# =====================================================================
#  RUN 2: Same bug pattern — agent should recall the solution
# =====================================================================

divider("RUN 2: Developer agent gets a SIMILAR task (WEEKS LATER)")

task2 = "Fix 500 error on POST /api/orders — customers can't place orders"

print(f"Task: {task2}\n")

# Agent checks memory before starting
print("[1] Agent checks memory for prior knowledge...")
ctx2 = get_context(
    role="developer",
    task_prompt=task2,
    repo_path=".",
    save_pack=False,
)

fv2 = ctx2["fv_recall"]
print(f"    Past solutions found: {len(fv2['past_solutions'])}")
print(f"    Known mistakes found: {len(fv2['known_mistakes'])}")
print(f"    Gated lessons found:  {len(ctx2['memory_lessons'])}")

if fv2["past_solutions"] or fv2["known_mistakes"]:
    print("\n    ★ MEMORY HIT! Agent found relevant prior knowledge:\n")

    if fv2["past_solutions"]:
        print("    PAST SOLUTIONS:")
        for s in fv2["past_solutions"]:
            # Clean up the tag prefix for display
            text = s["text"]
            for tag in ["[oraclelens:solution] ", "[oraclelens:mistake] "]:
                text = text.replace(tag, "")
            print(f"      (score={s['score']:.3f}) {text[:120]}...")

    if fv2["known_mistakes"]:
        print("\n    KNOWN MISTAKES:")
        for m in fv2["known_mistakes"]:
            text = m["text"]
            for tag in ["[oraclelens:solution] ", "[oraclelens:mistake] "]:
                text = text.replace(tag, "")
            print(f"      (score={m['score']:.3f}) {text[:120]}...")

    if ctx2["memory_lessons"]:
        print("\n    GATED LESSONS:")
        for l in ctx2["memory_lessons"]:
            print(f"      [{l['category']}] {l['lesson']}")

print()
print("[2] Agent uses recalled knowledge to diagnose faster...")
print("    - Checks migration history: found new 'shipping_confirmed' column added as NOT NULL")
print("    - MATCHES the pattern from memory: NOT NULL without default!")
print("    - Skips lengthy debugging — goes straight to the fix")
print()

print("[3] Agent applies the known fix pattern...")
print("    - Added default=False to shipping_confirmed column")
print("    - Added shipping_confirmed=False in Order.create()")
print("    - Tests pass ✓")
print()

print("    ✓ Run 2 complete — bug fixed FAST using recalled knowledge")
print("      (no debugging needed, pattern was recognized from memory)")


# =====================================================================
#  Show the prompt block the agent would actually see
# =====================================================================

divider("WHAT THE AGENT ACTUALLY SEES (injected prompt block)")

print(ctx2["prompt_block"][:1500])
if len(ctx2["prompt_block"]) > 1500:
    print("...")

print()
divider("DEMO COMPLETE")
print("  Run 1: Agent debugged from scratch, stored solution + mistake pattern")
print("  Run 2: Agent recalled solution, applied fix immediately")
print("  This is the memory loop — learn once, apply forever.")
print()
