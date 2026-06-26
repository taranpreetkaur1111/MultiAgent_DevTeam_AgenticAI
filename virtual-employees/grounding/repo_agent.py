"""
Repo Agent
===========
A lightweight repository reviewer that can run as a required first-step
context enhancer and also expose a callable tool for follow-up questions.

The agent primarily uses the repo context pack as its knowledge base.
If Ollama is available locally, it may generate better review questions,
but it always falls back to deterministic heuristics so the workflow
continues to function offline.
"""

from __future__ import annotations

import re
from typing import Any

from grounding.ollama_client import OllamaClient


class RepoAgent:
    """
    Heuristic repository reviewer with optional local-LLM assistance.

    Parameters
    ----------
    context_pack : dict
        The structured context pack returned by build_context_pack().
    """

    def __init__(self, context_pack: dict):
        self.pack = context_pack
        self.files = context_pack.get("files", [])
        self.history: list[dict[str, str]] = []
        self.llm = OllamaClient(model="tinyllama")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pack_summary(self) -> str:
        lines = [
            f"Task: {self.pack.get('task_prompt', '')}",
            f"Repo: {self.pack.get('repo_path', '')}",
            f"Files extracted: {self.pack.get('files_count', len(self.files))}",
        ]
        for file_entry in self.files[:10]:
            lines.append(f"- {file_entry.get('path', '')} [{file_entry.get('reason', '')}]")
        return "\n".join(lines)

    def _files_matching(self, patterns: list[str]) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for file_entry in self.files:
            haystack = f"{file_entry.get('path', '')}\n{file_entry.get('content', '')}".lower()
            if any(p in haystack for p in patterns):
                matches.append(file_entry)
        return matches

    def _answer_purpose(self) -> str:
        readme = next(
            (f for f in self.files if f.get("path", "").lower().endswith("readme.md")),
            None,
        )
        if readme:
            content = readme.get("content", "").strip()
            first_lines = " ".join([line.strip() for line in content.splitlines()[:6] if line.strip()])
            if first_lines:
                return f"Based on the README and extracted files, this repository appears to be: {first_lines[:500]}"

        if self.files:
            top_paths = ", ".join(f.get("path", "") for f in self.files[:5])
            return (
                "I do not have a clean repo summary in the context pack, but the extracted files suggest "
                f"the project centers around: {top_paths}."
            )

        return "The context pack does not contain enough information to describe the repository yet."

    def _answer_dependencies(self) -> str:
        dep_files = self._files_matching(["requirements.txt", "pyproject.toml", "package.json", "setup.py"])
        if not dep_files:
            return "I could not find a dependency manifest in the extracted context pack."

        snippets = []
        for file_entry in dep_files[:3]:
            content = file_entry.get("content", "")
            lines = [ln.strip() for ln in content.splitlines() if ln.strip()][:12]
            snippets.append(f"{file_entry.get('path')}: {', '.join(lines[:8])}")
        return "Main dependency files found: " + " | ".join(snippets)

    def _answer_tests(self) -> str:
        test_files = [
            f for f in self.files
            if "test" in f.get("path", "").lower() or "pytest" in f.get("content", "").lower()
        ]
        if not test_files:
            return "I did not find any obvious test files in the extracted context pack."
        paths = ", ".join(f.get("path", "") for f in test_files[:8])
        return f"The repo does appear to include tests or test-related files, including: {paths}."

    def _answer_modules(self) -> str:
        if not self.files:
            return "No files are available in the context pack yet."
        paths = ", ".join(f.get("path", "") for f in self.files[:10])
        return f"The main extracted modules/files in the current context are: {paths}."

    def _answer_configuration(self) -> str:
        config_files = self._files_matching([
            "config", ".env", "docker", "compose", "yaml", "yml", "toml", "ini", "settings"
        ])
        if not config_files:
            return "I did not find obvious configuration files in the extracted context pack."
        paths = ", ".join(f.get("path", "") for f in config_files[:10])
        return f"Relevant configuration or environment files include: {paths}."

    def _answer_history_limitations(self) -> str:
        return (
            "I can describe the repository as it appears in the current extracted snapshot, but I cannot reliably "
            "describe the original or prior state unless that history is explicitly present in the files I can see. "
            "This context pack does not include git history by default."
        )

    def _answer_keyword_search(self, question: str) -> str:
        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_\-]+", question.lower())
        tokens = [t for t in tokens if len(t) > 2 and t not in {
            "what", "which", "where", "when", "does", "this", "that", "repo", "repository",
            "about", "with", "from", "have", "main", "need", "more", "clarity", "look", "looks",
            "like", "made", "changes", "originally", "state", "current"
        }]
        if not tokens:
            return "I could not extract meaningful keywords from that question."

        scored: list[tuple[int, dict[str, Any]]] = []
        for file_entry in self.files:
            haystack = f"{file_entry.get('path', '')}\n{file_entry.get('content', '')}".lower()
            score = sum(1 for t in tokens if t in haystack)
            if score > 0:
                scored.append((score, file_entry))

        if not scored:
            return (
                "I could not find strong evidence for that question in the current context pack. "
                "You may need a broader context pack or additional repo files."
            )

        scored.sort(key=lambda x: (-x[0], x[1].get("path", "")))
        top = scored[:5]
        evidence = []
        for score, file_entry in top:
            preview = " ".join(file_entry.get("content", "").splitlines()[:6])[:220]
            evidence.append(f"{file_entry.get('path')} (score={score}): {preview}")
        return "Most relevant evidence I found: " + " | ".join(evidence)

    def _fallback_questions(self) -> list[str]:
        return [
            "What does this repository do?",
            "What are the main dependencies?",
            "Does this project have tests?",
        ]

    # ------------------------------------------------------------------
    # Review generation
    # ------------------------------------------------------------------

    def generate_questions(self) -> list[str]:
        if self.llm.is_available():
            prompt = (
                "You are reviewing a software repository. Based on this repo context, generate exactly 3 short "
                "questions that would improve an engineer's understanding of the codebase. Return one question per line.\n\n"
                + self._pack_summary()
            )
            try:
                response = self.llm.generate(prompt)
                questions = [q.strip(" -\t") for q in response.splitlines() if q.strip()]
                if questions:
                    return questions[:3]
            except Exception:
                pass
        return self._fallback_questions()

    def self_interrogate(self) -> list[tuple[str, str]]:
        questions = self.generate_questions()
        return [(q, self.ask(q)) for q in questions]

    def enrich_context(self) -> dict[str, Any]:
        qa_pairs = self.self_interrogate()
        self.history = []
        for q, a in qa_pairs:
            self.history.append({"question": q, "answer": a})

        notes = self.render_review_notes()
        return {
            "questions": [q for q, _ in qa_pairs],
            "qa_pairs": self.history,
            "notes": notes,
        }

    def render_review_notes(self) -> str:
        lines = ["=== REPO REVIEW ==="]
        for idx, item in enumerate(self.history, start=1):
            lines.append(f"Q{idx}: {item['question']}")
            lines.append(f"A{idx}: {item['answer']}")
            lines.append("")
        lines.append("=== END REPO REVIEW ===")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ask(self, question: str) -> str:
        q = question.lower().strip()

        if any(p in q for p in ["what does", "what is this", "purpose", "overview", "about"]):
            return self._answer_purpose()
        if any(p in q for p in ["depend", "requirements", "packages", "libraries", "install"]):
            return self._answer_dependencies()
        if any(p in q for p in ["test", "unittest", "pytest", "spec"]):
            return self._answer_tests()
        if any(p in q for p in ["module", "structure", "files", "components", "list"]):
            return self._answer_modules()
        if any(p in q for p in ["config", "configuration", "settings", "environment", ".env", "docker"]):
            return self._answer_configuration()
        if any(p in q for p in ["originally", "before", "prior", "history", "used to", "looked like"]):
            return self._answer_history_limitations()

        return self._answer_keyword_search(question)

    def build_tool_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "ask_repo_reviewer",
                "description": (
                    "Ask the repository reviewer for clarification about code structure, important files, "
                    "dependencies, tests, and uncertainty about current versus prior repo state."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "A natural-language question about the repository.",
                        }
                    },
                    "required": ["question"],
                },
            },
        }

    def handle_tool_call(self, question: str) -> dict[str, Any]:
        answer = self.ask(question)
        confidence = "high"
        lowered = answer.lower()
        if "could not find" in lowered or "not enough information" in lowered:
            confidence = "low"
        elif "cannot reliably" in lowered or "may need" in lowered:
            confidence = "medium"

        response = {
            "answer": answer,
            "confidence": confidence,
            "history_length": len(self.history),
        }
        self.history.append({"question": question, "answer": answer})
        return response
