"""
Gated Memory System
====================
Stores stable lessons learned from the virtual employee workflow.

Rules:
- Only 'supervisor' and 'qa' roles can WRITE
- All roles can READ
- Every write is logged in an audit log
- Rollback is supported (soft delete with timestamp)
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
MEMORY_FILE    = BASE_DIR / "memory_store.json"
AUDIT_LOG_FILE = BASE_DIR / "audit_log.json"

# ── Access control ─────────────────────────────────────────────────────────
WRITE_ROLES = {"supervisor", "qa"}
READ_ROLES  = {"supervisor", "qa", "pm", "architect", "developer", "tester"}


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_json(path: Path, data: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _append_audit(action: str, role: str, entry_id: Optional[str], detail: str) -> None:
    logs = _load_json(AUDIT_LOG_FILE)
    logs.append({
        "timestamp": _now(),
        "action":    action,
        "role":      role,
        "entry_id":  entry_id,
        "detail":    detail,
    })
    _save_json(AUDIT_LOG_FILE, logs)


# ── Core API ───────────────────────────────────────────────────────────────

def write_lesson(role: str, category: str, lesson: str, context: str = "") -> dict:
    """
    Write a new lesson to memory.

    Parameters
    ----------
    role     : the agent writing the lesson (must be 'supervisor' or 'qa')
    category : topic tag, e.g. 'testing', 'architecture', 'api'
    lesson   : the lesson text
    context  : optional — what task/PR triggered this lesson

    Returns the saved entry, or raises PermissionError.
    """
    if role not in WRITE_ROLES:
        _append_audit("WRITE_DENIED", role, None, f"Attempted to write: '{lesson[:60]}'")
        raise PermissionError(
            f"Role '{role}' is not allowed to write to memory. "
            f"Only {WRITE_ROLES} can write."
        )

    entry = {
        "id":        str(uuid.uuid4()),
        "timestamp": _now(),
        "author":    role,
        "category":  category,
        "lesson":    lesson,
        "context":   context,
        "active":    True,   # False = soft-deleted (rolled back)
    }

    entries = _load_json(MEMORY_FILE)
    entries.append(entry)
    _save_json(MEMORY_FILE, entries)
    _append_audit("WRITE", role, entry["id"], f"category={category} | '{lesson[:60]}'")

    return entry


def read_lessons(role: str, category: Optional[str] = None) -> list:
    """
    Read active lessons from memory.

    Parameters
    ----------
    role     : the agent reading (any recognised role)
    category : optional filter by category tag

    Returns a list of active lesson entries.
    """
    if role not in READ_ROLES:
        _append_audit("READ_DENIED", role, None, "Attempted to read memory")
        raise PermissionError(f"Role '{role}' is not a recognised agent role.")

    entries = _load_json(MEMORY_FILE)
    active  = [e for e in entries if e.get("active", True)]

    if category:
        active = [e for e in active if e.get("category") == category]

    _append_audit("READ", role, None, f"category={category or 'all'} | {len(active)} entries returned")
    return active


def rollback_lesson(role: str, entry_id: str) -> dict:
    """
    Soft-delete a lesson by ID (sets active=False).
    Only supervisor/qa can rollback.

    Returns the updated entry, or raises errors.
    """
    if role not in WRITE_ROLES:
        _append_audit("ROLLBACK_DENIED", role, entry_id, "Insufficient permissions")
        raise PermissionError(f"Role '{role}' cannot rollback memory entries.")

    entries = _load_json(MEMORY_FILE)
    for entry in entries:
        if entry["id"] == entry_id:
            if not entry.get("active", True):
                raise ValueError(f"Entry '{entry_id}' is already rolled back.")
            entry["active"]      = False
            entry["rolled_back_by"] = role
            entry["rolled_back_at"] = _now()
            _save_json(MEMORY_FILE, entries)
            _append_audit("ROLLBACK", role, entry_id, f"Rolled back: '{entry['lesson'][:60]}'")
            return entry

    raise KeyError(f"Entry with id '{entry_id}' not found.")


def get_audit_log(role: str) -> list:
    """
    Return the full audit log. Only supervisor can view it.
    """
    if role != "supervisor":
        raise PermissionError("Only 'supervisor' can access the audit log.")
    return _load_json(AUDIT_LOG_FILE)
