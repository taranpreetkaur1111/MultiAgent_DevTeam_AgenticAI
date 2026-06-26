"""
FV v2.2 Memory Integration
============================
Connects the virtual employee workflow to Abdullah's FV v2.2
memory system (FAISS + SQLite + SentenceTransformers).

Provides:
  - Semantic recall of past solutions, mistakes, and research
  - Auto-remembering of task outcomes for future reference
  - Prevents agents from repeating solved problems
  - Prevents agents from redoing completed research/changes

Uses FV v2.2's VectorStore + upsert_fact + retrieve_rerank
directly, no external API server needed.
"""

import os
import sys
import time
import json
import sqlite3
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── FV v2.2 location ─────────────────────────────────────────────────────
# Default: mounted Windows drive. Override with FV_ROOT env var.
FV_ROOT = os.environ.get("FV_ROOT", "/mnt/sata/d")

# Memory DB for OracleLens (separate from FV's own memory.db)
MEMORY_DB = os.environ.get(
    "ORACLELENS_MEMORY_DB",
    os.path.join(
        os.path.dirname(__file__),
        "oraclelens_memory.db",
    ),
)
FAISS_DIR = os.environ.get(
    "ORACLELENS_FAISS_DIR",
    os.path.join(os.path.dirname(__file__), "oraclelens_faiss"),
)

os.makedirs(FAISS_DIR, exist_ok=True)

# ── Import FV v2.2 modules ───────────────────────────────────────────────
# Add FV root and agent_pipeline to path so we can import its modules
if FV_ROOT not in sys.path:
    sys.path.insert(0, FV_ROOT)
if os.path.join(FV_ROOT, "agent_pipeline") not in sys.path:
    sys.path.insert(0, os.path.join(FV_ROOT, "agent_pipeline"))

_store = None
_conn = None

# Memory type tags
MEMORY_TYPES = {
    "solution":  "oraclelens:solution",
    "mistake":   "oraclelens:mistake",
    "research":  "oraclelens:research",
    "change":    "oraclelens:change",
    "decision":  "oraclelens:decision",
}


def _get_store():
    """Lazy-init the FV v2.2 VectorStore with our own DB."""
    global _store, _conn

    if _store is not None:
        return _store, _conn

    try:
        # Override FV's globals before importing
        os.environ["AGENT_DB"] = MEMORY_DB
        os.environ["FAISS_DIR"] = FAISS_DIR
        os.environ["EMBED_DEVICE"] = "cpu"  # safe default

        from agent_pipeline.agent_short_memory import (
            VectorStore,
            init_db,
        )

        # Patch the module-level DB_PATH before init
        import agent_pipeline.agent_short_memory as asm
        asm.DB_PATH = MEMORY_DB
        asm.FAISS_DIR = FAISS_DIR

        _conn = init_db()
        _store = VectorStore(_conn)
        logger.info(f"FV v2.2 memory initialized (db={MEMORY_DB})")
        return _store, _conn

    except Exception as e:
        logger.error(f"Failed to initialize FV v2.2: {e}")
        return None, None


def remember(
    text: str,
    memory_type: str = "solution",
    importance: float = 0.8,
) -> Optional[str]:
    """
    Store a fact in FV v2.2 for future recall.

    Parameters
    ----------
    text         : the fact to remember (concise, self-contained)
    memory_type  : one of: solution, mistake, research, change, decision
    importance   : 0.0–1.0, higher = more important

    Returns
    -------
    The fact_id if successful, None if failed.
    """
    store, conn = _get_store()
    if store is None:
        return None

    try:
        from agent_pipeline.agent_short_memory import upsert_fact

        type_tag = MEMORY_TYPES.get(memory_type, f"oraclelens:{memory_type}")
        tagged_text = f"[{type_tag}] {text}"

        fact_id = upsert_fact(
            store,
            tagged_text,
            tags={"type": memory_type, "source": "oraclelens"},
            importance=importance,
        )
        logger.info(f"Remembered ({memory_type}): {text[:80]}...")
        return fact_id

    except Exception as e:
        logger.error(f"FV remember failed: {e}")
        return None


def recall(
    query: str,
    top_k: int = 20,
    final_k: int = 10,
    memory_type: Optional[str] = None,
) -> list[dict]:
    """
    Semantically search FV v2.2 for relevant past knowledge.

    Parameters
    ----------
    query       : what to search for (natural language)
    top_k       : candidates to retrieve from FAISS
    final_k     : final results after filtering
    memory_type : optional filter (solution/mistake/research/change/decision)

    Returns
    -------
    List of {text, score, fact_id, meta} dicts.
    """
    store, conn = _get_store()
    if store is None:
        return []

    try:
        from agent_pipeline.agent_short_memory import retrieve_rerank
        # Override session filter — we want ALL oraclelens facts
        import agent_pipeline.agent_short_memory as asm
        old_session = asm.SESSION_ID
        asm.SESSION_ID = asm.SESSION_ID  # keep as-is for now

        raw = store.query_topk(query, top_k)

        # Filter to oraclelens facts only
        results = []
        for fid, txt, meta, score in raw:
            tags = meta.get("tags", {}) if isinstance(meta, dict) else {}
            source = tags.get("source", "") if isinstance(tags, dict) else ""
            if source == "oraclelens" or "oraclelens:" in txt:
                result = {
                    "text": txt,
                    "score": score,
                    "fact_id": fid,
                    "meta": meta,
                }
                # Filter by type if requested
                if memory_type:
                    type_tag = MEMORY_TYPES.get(memory_type, "")
                    if type_tag not in txt:
                        continue
                results.append(result)

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:final_k]

    except Exception as e:
        logger.error(f"FV recall failed: {e}")
        return []


def recall_for_task(task_prompt: str, repo_path: str = "", top_k: int = 20) -> dict:
    """
    High-level recall for the virtual employee workflow.
    Searches for past solutions, known mistakes, and prior research.
    """
    results = {
        "past_solutions": [],
        "known_mistakes": [],
        "prior_research": [],
        "prior_changes": [],
        "raw_results": [],
    }

    query = f"task: {task_prompt}"
    if repo_path:
        query += f" | repo: {repo_path}"

    all_results = recall(query=query, top_k=top_k, final_k=15)
    results["raw_results"] = all_results

    for r in all_results:
        text = r.get("text", "")
        if MEMORY_TYPES["solution"] in text:
            results["past_solutions"].append(r)
        elif MEMORY_TYPES["mistake"] in text:
            results["known_mistakes"].append(r)
        elif MEMORY_TYPES["research"] in text:
            results["prior_research"].append(r)
        elif MEMORY_TYPES["change"] in text:
            results["prior_changes"].append(r)

    return results


def remember_task_outcome(
    task_prompt: str,
    outcome: str,
    memory_type: str = "solution",
    details: str = "",
    repo_path: str = "",
    agent_role: str = "",
) -> Optional[str]:
    """
    Store a task outcome for future recall.
    """
    parts = [f"Task: {task_prompt}"]
    if repo_path:
        parts.append(f"Repo: {repo_path}")
    if agent_role:
        parts.append(f"Agent: {agent_role}")
    parts.append(f"Outcome: {outcome}")
    if details:
        parts.append(f"Details: {details}")
    parts.append(f"When: {time.strftime('%Y-%m-%d %H:%M')}")

    text = " | ".join(parts)

    # Mistakes get higher importance so they surface more
    importance = 0.9 if memory_type == "mistake" else 0.8
    return remember(text=text, memory_type=memory_type, importance=importance)


def format_recall_for_prompt(recall_results: dict) -> str:
    """
    Format recall results into a prompt-injectable block.
    """
    sections = []

    if recall_results["past_solutions"]:
        sections.append("=== PAST SOLUTIONS (don't re-solve these) ===")
        for r in recall_results["past_solutions"][:5]:
            text = r.get("text", "")
            # Strip the type tag prefix
            for tag in MEMORY_TYPES.values():
                text = text.replace(f"[{tag}]", "").strip()
            sections.append(f"  - {text}")

    if recall_results["known_mistakes"]:
        sections.append("\n=== KNOWN MISTAKES (avoid these) ===")
        for r in recall_results["known_mistakes"][:5]:
            text = r.get("text", "")
            for tag in MEMORY_TYPES.values():
                text = text.replace(f"[{tag}]", "").strip()
            sections.append(f"  - {text}")

    if recall_results["prior_research"]:
        sections.append("\n=== PRIOR RESEARCH (already investigated) ===")
        for r in recall_results["prior_research"][:5]:
            text = r.get("text", "")
            for tag in MEMORY_TYPES.values():
                text = text.replace(f"[{tag}]", "").strip()
            sections.append(f"  - {text}")

    if recall_results["prior_changes"]:
        sections.append("\n=== PRIOR CHANGES (already done, don't repeat) ===")
        for r in recall_results["prior_changes"][:3]:
            text = r.get("text", "")
            for tag in MEMORY_TYPES.values():
                text = text.replace(f"[{tag}]", "").strip()
            sections.append(f"  - {text}")

    if not sections:
        return ""

    return "\n".join(sections) + "\n=== END MEMORY RECALL ===\n"
