"""
Repo Context Pack — Grounding Layer
=====================================
Scans a local repository and extracts the most relevant files
for a given task prompt. Produces a structured context pack
that is injected into agent prompts before execution.

This is the v1 (repo-based) implementation.
It will be extended to RAG in a future sprint.
"""

import os
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# ── Configuration ──────────────────────────────────────────────────────────

# Files/dirs to always skip
EXCLUDED_DIRS  = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", ".vscode"}
EXCLUDED_EXTS  = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".lock", ".bin", ".exe", ".zip"}

# Priority files — always included if they exist
PRIORITY_FILES = {
    "README.md", "README.rst", "readme.md",
    "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "requirements.txt",
    ".env.example", "docker-compose.yml", "Dockerfile",
    "ARCHITECTURE.md", "CONTRIBUTING.md",
}

# Max characters per file to include (avoids bloating the context)
MAX_CHARS_PER_FILE = 3000

# Max total files in the pack
MAX_FILES = 20


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in EXCLUDED_EXTS:
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="strict") as f:
            f.read(512)
        return True
    except (UnicodeDecodeError, IsADirectoryError):
        return False

def _read_truncated(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > MAX_CHARS_PER_FILE:
            content = content[:MAX_CHARS_PER_FILE] + f"\n\n... [truncated — {len(content)} chars total]"
        return content
    except Exception as e:
        return f"[Could not read file: {e}]"

def _is_relevant(file_path: Path, task_keywords: list[str]) -> bool:
    """Simple keyword relevance check on filename and first lines."""
    name_lower = file_path.name.lower()
    for kw in task_keywords:
        if kw.lower() in name_lower:
            return True
    try:
        first_lines = file_path.read_text(encoding="utf-8", errors="replace")[:500].lower()
        for kw in task_keywords:
            if kw.lower() in first_lines:
                return True
    except Exception:
        pass
    return False

def _extract_keywords(task_prompt: str) -> list[str]:
    """Extract simple keywords from the task prompt (no NLP needed)."""
    stopwords = {"the","a","an","is","in","on","at","to","for","of","and","or","with","that","this","it","be","as","by","from","are"}
    words = task_prompt.lower().replace(",", " ").replace(".", " ").split()
    return [w for w in words if len(w) > 3 and w not in stopwords]


# ── Core API ───────────────────────────────────────────────────────────────

def build_context_pack(repo_path: str, task_prompt: str, task_id: Optional[str] = None) -> dict:
    """
    Scan the repository and build a context pack for the given task.

    Parameters
    ----------
    repo_path   : absolute or relative path to the local repository root
    task_prompt : the task description (e.g. "Add login endpoint with JWT auth")
    task_id     : optional identifier for this task run

    Returns a structured context pack dict.
    """
    repo = Path(repo_path).resolve()
    if not repo.exists():
        raise FileNotFoundError(f"Repository path not found: {repo}")

    keywords  = _extract_keywords(task_prompt)
    pack_files = []
    seen_paths = set()

    # ── Step 1: Always include priority files ──────────────────────────────
    for priority_name in PRIORITY_FILES:
        candidate = repo / priority_name
        if candidate.exists() and candidate not in seen_paths:
            pack_files.append({
                "path":     str(candidate.relative_to(repo)),
                "reason":   "priority_file",
                "content":  _read_truncated(candidate),
            })
            seen_paths.add(candidate)

    # ── Step 2: Walk repo and collect relevant files ───────────────────────
    for root, dirs, files in os.walk(repo):
        # Prune excluded dirs in-place
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

        for filename in files:
            file_path = Path(root) / filename
            if file_path in seen_paths:
                continue
            if not _is_text_file(file_path):
                continue
            if len(pack_files) >= MAX_FILES:
                break
            if _is_relevant(file_path, keywords):
                pack_files.append({
                    "path":    str(file_path.relative_to(repo)),
                    "reason":  "keyword_match",
                    "content": _read_truncated(file_path),
                })
                seen_paths.add(file_path)

    # ── Step 3: Assemble the pack ──────────────────────────────────────────
    safe_ts = _now().replace(":", "-").replace("+", "UTC")
    context_pack = {
        "pack_id":      task_id or f"pack_{safe_ts}",
        "generated_at": _now(),
        "repo_path":    str(repo),
        "task_prompt":  task_prompt,
        "keywords":     keywords,
        "files_count":  len(pack_files),
        "files":        pack_files,
    }

    return context_pack


def save_context_pack(pack: dict, output_dir: str = ".") -> Path:
    """
    Persist the context pack to disk as a JSON evidence file.
    Each run produces a traceable, dated file.
    """
    out_dir  = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"context_pack_{pack['pack_id']}.json"
    out_path = out_dir / filename
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pack, f, indent=2, ensure_ascii=False)
    return out_path


def format_pack_for_prompt(pack: dict) -> str:
    """
    Convert the context pack into a clean string block
    ready to be injected into an agent's system prompt.
    """
    lines = [
        f"=== REPO CONTEXT PACK ===",
        f"Task  : {pack['task_prompt']}",
        f"Repo  : {pack['repo_path']}",
        f"Files : {pack['files_count']} extracted",
        f"Generated: {pack['generated_at']}",
        "",
    ]
    for file_entry in pack["files"]:
        lines.append(f"--- {file_entry['path']} [{file_entry['reason']}] ---")
        lines.append(file_entry["content"])
        lines.append("")
    lines.append("=== END CONTEXT PACK ===")
    return "\n".join(lines)
