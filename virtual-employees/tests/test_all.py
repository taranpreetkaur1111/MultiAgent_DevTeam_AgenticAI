"""
Test Suite — Virtual Employees Memory & Grounding
===================================================
Covers:
  - Gated memory: writes, reads, access control, rollback, audit log
  - Context pack: repo scan, keyword extraction, prompt formatting
  - Orchestrator interface: get_context end-to-end
"""

import sys
import shutil
import tempfile
import unittest
from pathlib import Path

# Allow imports from parent package
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.gated_memory import (
    write_lesson, read_lessons, rollback_lesson, get_audit_log,
    MEMORY_FILE, AUDIT_LOG_FILE
)
from grounding.context_pack import (
    build_context_pack, format_pack_for_prompt, _extract_keywords
)
from orchestrator.interface import get_context, record_lesson, undo_lesson


# ── Test helpers ────────────────────────────────────────────────────────────

def _reset_memory():
    """Wipe memory and audit files between tests."""
    for f in [MEMORY_FILE, AUDIT_LOG_FILE]:
        if f.exists():
            f.unlink()

def _make_fake_repo(base_dir: Path) -> Path:
    """Create a minimal fake repository for context pack tests."""
    repo = base_dir / "fake_repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Fake Repo\nThis is a test repository.")
    (repo / "requirements.txt").write_text("fastapi\nuvicorn\npydantic")
    src = repo / "src"
    src.mkdir()
    (src / "auth.py").write_text("# Authentication module\ndef login(user, password): pass")
    (src / "database.py").write_text("# Database connection\ndef connect(): pass")
    (src / "utils.py").write_text("# Utility functions\ndef format_date(d): pass")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_auth.py").write_text("def test_login():\n    assert True")
    return repo


class TestGatedMemoryWrites(unittest.TestCase):

    def setUp(self):
        _reset_memory()

    def test_supervisor_can_write(self):
        entry = write_lesson("supervisor", "testing", "Always write integration tests for auth flows.")
        self.assertEqual(entry["author"], "supervisor")
        self.assertEqual(entry["category"], "testing")
        self.assertTrue(entry["active"])
        self.assertIn("id", entry)

    def test_qa_can_write(self):
        entry = write_lesson("qa", "api", "Validate all API inputs at the gateway level.")
        self.assertEqual(entry["author"], "qa")

    def test_developer_cannot_write(self):
        with self.assertRaises(PermissionError):
            write_lesson("developer", "testing", "This should fail.")

    def test_pm_cannot_write(self):
        with self.assertRaises(PermissionError):
            write_lesson("pm", "architecture", "This should fail too.")

    def test_unknown_role_cannot_write(self):
        with self.assertRaises(PermissionError):
            write_lesson("hacker", "security", "Trying to inject memory.")


class TestGatedMemoryReads(unittest.TestCase):

    def setUp(self):
        _reset_memory()
        write_lesson("supervisor", "testing", "Use pytest for all unit tests.")
        write_lesson("qa", "api", "Always return 422 for validation errors.")
        write_lesson("supervisor", "testing", "Mock external calls in tests.")

    def test_developer_can_read_all(self):
        lessons = read_lessons("developer")
        self.assertEqual(len(lessons), 3)

    def test_filter_by_category(self):
        testing = read_lessons("developer", category="testing")
        self.assertEqual(len(testing), 2)
        api = read_lessons("developer", category="api")
        self.assertEqual(len(api), 1)

    def test_unknown_role_cannot_read(self):
        with self.assertRaises(PermissionError):
            read_lessons("anonymous")

    def test_empty_category_returns_empty(self):
        result = read_lessons("developer", category="nonexistent")
        self.assertEqual(result, [])


class TestGatedMemoryRollback(unittest.TestCase):

    def setUp(self):
        _reset_memory()

    def test_supervisor_can_rollback(self):
        entry = write_lesson("supervisor", "testing", "Bad lesson to remove.")
        result = rollback_lesson("supervisor", entry["id"])
        self.assertFalse(result["active"])
        self.assertEqual(result["rolled_back_by"], "supervisor")

    def test_rolled_back_entry_not_returned_in_reads(self):
        entry = write_lesson("qa", "api", "Lesson to roll back.")
        rollback_lesson("supervisor", entry["id"])
        lessons = read_lessons("developer")
        ids = [l["id"] for l in lessons]
        self.assertNotIn(entry["id"], ids)

    def test_developer_cannot_rollback(self):
        entry = write_lesson("supervisor", "testing", "Some lesson.")
        with self.assertRaises(PermissionError):
            rollback_lesson("developer", entry["id"])

    def test_rollback_nonexistent_raises_key_error(self):
        with self.assertRaises(KeyError):
            rollback_lesson("supervisor", "nonexistent-id-xyz")

    def test_double_rollback_raises_value_error(self):
        entry = write_lesson("qa", "testing", "Double rollback test.")
        rollback_lesson("supervisor", entry["id"])
        with self.assertRaises(ValueError):
            rollback_lesson("supervisor", entry["id"])


class TestAuditLog(unittest.TestCase):

    def setUp(self):
        _reset_memory()

    def test_write_creates_audit_entry(self):
        write_lesson("supervisor", "testing", "Audit test lesson.")
        log = get_audit_log("supervisor")
        actions = [e["action"] for e in log]
        self.assertIn("WRITE", actions)

    def test_denied_write_creates_denied_audit_entry(self):
        try:
            write_lesson("developer", "testing", "Denied attempt.")
        except PermissionError:
            pass
        log = get_audit_log("supervisor")
        actions = [e["action"] for e in log]
        self.assertIn("WRITE_DENIED", actions)

    def test_only_supervisor_can_view_audit(self):
        with self.assertRaises(PermissionError):
            get_audit_log("developer")
        with self.assertRaises(PermissionError):
            get_audit_log("qa")


class TestContextPack(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = _make_fake_repo(Path(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_pack_includes_priority_files(self):
        pack = build_context_pack(str(self.repo), "Add login endpoint")
        paths_lower = [f["path"].lower() for f in pack["files"]]
        self.assertTrue(any("readme" in p for p in paths_lower))
        self.assertIn("requirements.txt", [f["path"] for f in pack["files"]])

    def test_pack_includes_keyword_relevant_files(self):
        pack = build_context_pack(str(self.repo), "Implement authentication login")
        paths = [f["path"] for f in pack["files"]]
        self.assertTrue(any("auth" in p for p in paths))

    def test_pack_has_correct_structure(self):
        pack = build_context_pack(str(self.repo), "Fix database connection")
        self.assertIn("pack_id", pack)
        self.assertIn("generated_at", pack)
        self.assertIn("task_prompt", pack)
        self.assertIn("keywords", pack)
        self.assertIn("files", pack)
        self.assertIn("files_count", pack)

    def test_invalid_repo_raises_error(self):
        with self.assertRaises(FileNotFoundError):
            build_context_pack("/nonexistent/path", "Some task")

    def test_keyword_extraction(self):
        keywords = _extract_keywords("Add login endpoint with JWT authentication")
        self.assertIn("login", keywords)
        self.assertIn("endpoint", keywords)
        self.assertIn("authentication", keywords)
        self.assertNotIn("with", keywords)
        self.assertNotIn("add", keywords)

    def test_format_pack_for_prompt(self):
        pack = build_context_pack(str(self.repo), "Fix auth module")
        prompt = format_pack_for_prompt(pack)
        self.assertIn("=== REPO CONTEXT PACK ===", prompt)
        self.assertIn("=== END CONTEXT PACK ===", prompt)
        self.assertIn("Fix auth module", prompt)


class TestOrchestratorInterface(unittest.TestCase):

    def setUp(self):
        _reset_memory()
        self.tmp = tempfile.mkdtemp()
        self.repo = _make_fake_repo(Path(self.tmp))
        self.packs_dir = Path(self.tmp) / "packs"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_get_context_returns_all_keys(self):
        result = get_context(
            role="developer",
            task_prompt="Add login endpoint",
            repo_path=str(self.repo),
            save_pack=True,
            packs_output_dir=str(self.packs_dir),
        )
        self.assertIn("memory_lessons", result)
        self.assertIn("fv_recall", result)
        self.assertIn("context_pack", result)
        self.assertIn("repo_review", result)
        self.assertIn("prompt_block", result)
        self.assertIn("pack_saved_at", result)
        self.assertIn("tools", result)
        self.assertIn("tool_schemas", result)
        self.assertIn("repo_agent", result)

    def test_get_context_prompt_block_contains_repo_info(self):
        result = get_context(
            role="developer",
            task_prompt="Implement JWT authentication",
            repo_path=str(self.repo),
            save_pack=False,
        )
        self.assertIn("REPO CONTEXT PACK", result["prompt_block"])
        self.assertIn("REPOSITORY REVIEW", result["prompt_block"])
        self.assertIn("ask_repo_reviewer", result["prompt_block"])

    def test_get_context_includes_memory_lessons(self):
        record_lesson("supervisor", "testing", "Always test auth edge cases.")
        result = get_context(
            role="developer",
            task_prompt="Add login feature",
            repo_path=str(self.repo),
            memory_category="testing",
            save_pack=False,
        )
        self.assertEqual(len(result["memory_lessons"]), 1)
        self.assertIn("LESSONS LEARNED", result["prompt_block"])

    def test_pack_is_saved_to_disk(self):
        get_context(
            role="developer",
            task_prompt="Fix database issue",
            repo_path=str(self.repo),
            task_id="task_001",
            save_pack=True,
            packs_output_dir=str(self.packs_dir),
        )
        saved_files = list(self.packs_dir.glob("*.json"))
        self.assertGreater(len(saved_files), 0)

    def test_record_and_undo_lesson(self):
        entry = record_lesson("qa", "api", "Validate all query params.")
        self.assertTrue(entry["active"])
        rolled = undo_lesson("supervisor", entry["id"])
        self.assertFalse(rolled["active"])

    def test_repo_tool_is_callable(self):
        result = get_context(
            role="developer",
            task_prompt="Inspect auth flow",
            repo_path=str(self.repo),
            save_pack=False,
        )
        tool_result = result["tools"][0]("Which files handle authentication and tests?")
        self.assertIn("answer", tool_result)
        self.assertIn("confidence", tool_result)
        self.assertIn("evidence", tool_result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
