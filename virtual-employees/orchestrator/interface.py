"""
Orchestrator Interface
=======================
Single entry point for the orchestrator (Microsoft Agent Framework).

Exposes clean calls:
  - get_context(task)         -> memory lessons + FV recall + repo context pack
  - record_lesson(...)        -> gated memory write
  - remember_outcome(...)     -> FV v2.2 long-term memory write
  - recall_history(...)       -> FV v2.2 semantic search for past work

This is the ONLY file the orchestrator needs to import.
"""

import sys
from pathlib import Path

# Allow imports from sibling packages
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.gated_memory import (
    write_lesson,
    read_lessons,
    rollback_lesson,
    get_audit_log,
)
from memory.fv_memory import (
    recall_for_task,
    remember_task_outcome,
    format_recall_for_prompt,
    recall,
)
from grounding.context_pack import (
    build_context_pack,
    save_context_pack,
    format_pack_for_prompt,
)
from grounding.repo_agent import RepoAgent


REPO_REVIEWER_GUIDANCE = (
    "If you need more clarity about the repository, its important files, dependencies, tests, "
    "or the limits of what can be known about prior versus current state, "
    "call the `ask_repo_reviewer` tool before making assumptions."
)


def get_context(
    role: str,
    task_prompt: str,
    repo_path: str,
    task_id: str = None,
    memory_category: str = None,
    save_pack: bool = True,
    packs_output_dir: str = "./context_packs",
) -> dict:
    """Main orchestrator call — fetches everything needed before a task runs."""
    lessons = read_lessons(role=role, category=memory_category)

    fv_results = recall_for_task(
        task_prompt=task_prompt,
        repo_path=repo_path,
    )

    pack = build_context_pack(
        repo_path=repo_path,
        task_prompt=task_prompt,
        task_id=task_id,
    )

    repo_agent = RepoAgent(pack)
    repo_review = repo_agent.enrich_context()

    pack_path = None
    if save_pack:
        pack_path = save_context_pack(pack, output_dir=packs_output_dir)

    blocks = []

    fv_block = format_recall_for_prompt(fv_results)
    if fv_block:
        blocks.append(fv_block)

    if lessons:
        lesson_block = "=== LESSONS LEARNED ===\n"
        for lesson in lessons:
            lesson_block += f"[{lesson['category']}] {lesson['lesson']}\n"
        lesson_block += "=== END LESSONS ===\n"
        blocks.append(lesson_block)

    blocks.append(format_pack_for_prompt(pack))
    blocks.append(repo_review["summary"])
    blocks.append(REPO_REVIEWER_GUIDANCE)

    prompt_block = "\n\n".join(blocks)

    def ask_repo_reviewer(question: str):
        return repo_agent.handle_tool_call(question)

    return {
        "memory_lessons": lessons,
        "fv_recall": fv_results,
        "context_pack": pack,
        "repo_review": repo_review,
        "prompt_block": prompt_block,
        "pack_saved_at": str(pack_path) if pack_path else None,
        "repo_agent": repo_agent,
        "tools": [ask_repo_reviewer],
        "tool_schemas": [repo_agent.build_tool_spec()],
    }


# ── Gated memory write/rollback ─────────────────────────────────────────

def record_lesson(role: str, category: str, lesson: str, context: str = "") -> dict:
    """Proxy to gated memory write — enforces role access control."""
    return write_lesson(role=role, category=category, lesson=lesson, context=context)


def undo_lesson(role: str, entry_id: str) -> dict:
    """Proxy to memory rollback."""
    return rollback_lesson(role=role, entry_id=entry_id)


def fetch_audit_log(role: str) -> list:
    """Proxy to audit log — supervisor only."""
    return get_audit_log(role=role)


# ── FV v2.2 long-term memory ────────────────────────────────────────────

def remember_outcome(
    task_prompt: str,
    outcome: str,
    memory_type: str = "solution",
    details: str = "",
    repo_path: str = "",
    agent_role: str = "",
) -> str:
    """Store the outcome of a task in FV v2.2 for future recall."""
    return remember_task_outcome(
        task_prompt=task_prompt,
        outcome=outcome,
        memory_type=memory_type,
        details=details,
        repo_path=repo_path,
        agent_role=agent_role,
    )


def recall_history(query: str, top_k: int = 10) -> list:
    """Direct semantic search of FV v2.2."""
    return recall(query=query, top_k=top_k)
