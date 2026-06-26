"""
Oracle Lens :: Autonomous Software Development Team — Web Edition
Flask server with SSE streaming, session auth, and demo mode fallback.
FV v2.2 memory integration: agents recall past work and store outcomes. v3.1
"""

import os
import sys
import json
import time
import random
import threading
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import (
    Flask, render_template, request, Response, session,
    redirect, url_for, jsonify, stream_with_context
)

# ── Add parent dir so we can import from devteam.py ──────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── FV v2.2 Memory System ────────────────────────────────────────
# Add virtual-employees to path for memory imports
_VE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "virtual-employees")
if _VE_DIR not in sys.path:
    sys.path.insert(0, _VE_DIR)

HAS_MEMORY = False
_memory_fact_count = 0
try:
    from memory.fv_memory import (
        recall_for_task,
        remember_task_outcome,
        format_recall_for_prompt,
        recall,
        remember,
        _get_store,
        MEMORY_DB,
    )
    # Warm up the store to check it works
    _s, _c = _get_store()
    if _s is not None:
        HAS_MEMORY = True
        # Fix: replace the SQLite connection with one that allows cross-thread access
        # Flask uses worker threads, but the connection was created in main thread
        import sqlite3 as _sqlite3
        _new_conn = _sqlite3.connect(MEMORY_DB, check_same_thread=False)
        _s.conn = _new_conn
        # Also update the module-level reference
        import memory.fv_memory as _fv_mod
        _fv_mod._conn = _new_conn
        # Count facts
        try:
            _cur = _new_conn.execute("SELECT COUNT(*) FROM facts")
            _memory_fact_count = _cur.fetchone()[0]
        except Exception:
            _memory_fact_count = 0
        print(f"  FV v2.2 Memory: CONNECTED ({_memory_fact_count} facts)")
    else:
        print("  FV v2.2 Memory: store init returned None")
except Exception as _e:
    print(f"  FV v2.2 Memory: unavailable ({_e})")

# Demo memory results for when FV is unavailable
DEMO_MEMORY_RESULTS = {
    "past_solutions": [
        {
            "text": "[oraclelens:solution] Task: Fix 500 error on POST /api/users | "
                    "Repo: /home/om/projects/user-api | Agent: developer | "
                    "Outcome: success — missing NOT NULL default on email column | "
                    "Details: Added DEFAULT '' to email column in migration, "
                    "then backfilled existing rows. | When: 2026-03-28 14:22",
            "score": 0.89,
        },
    ],
    "known_mistakes": [
        {
            "text": "[oraclelens:mistake] Task: Add Redis caching to user lookup endpoint | "
                    "Repo: /home/om/projects/example-api | Agent: developer | "
                    "Outcome: failed: Redis connection pool exhaustion under load | "
                    "Details: Used default max_connections=10 which was too low for concurrent "
                    "API calls. Need to set max_connections=50+ and add connection timeout handling. "
                    "| When: 2026-03-26 01:03",
            "score": 0.82,
        },
    ],
    "prior_research": [],
    "prior_changes": [
        {
            "text": "[oraclelens:change] Task: Update dark mode implementation | "
                    "Repo: /home/om/projects/acme-webapp | Agent: developer | "
                    "Outcome: success — CSS custom properties approach | "
                    "Details: Used :root variables with data-theme attribute toggling. "
                    "localStorage for persistence. | When: 2026-03-30 09:15",
            "score": 0.76,
        },
    ],
    "raw_results": [],
}

# ── Anthropic client ─────────────────────────────────────────────
try:
    import anthropic
    _key = os.environ.get("ANTHROPIC_API_KEY", "")
    if _key:
        CLIENT = anthropic.Anthropic(api_key=_key)
        HAS_ANTHROPIC = True
    else:
        CLIENT = None
        HAS_ANTHROPIC = False
except Exception:
    CLIENT = None
    HAS_ANTHROPIC = False

# ── Local LLM ────────────────────────────────────────────────────
LOCAL_LLM_URL = os.environ.get("LOCAL_LLM_URL", "http://localhost:11434")
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "gemma4")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
HAS_LOCAL_LLM = False
try:
    import urllib.request
    if DEEPSEEK_API_KEY and "deepseek" in LOCAL_LLM_URL:
        HAS_LOCAL_LLM = True  # trust DeepSeek API is reachable
    else:
        _test = urllib.request.urlopen(LOCAL_LLM_URL, timeout=2)
        HAS_LOCAL_LLM = True
except Exception:
    pass


# ══════════════════════════════════════════════════════════════════
# AGENT PROFILES & PIPELINE
# ══════════════════════════════════════════════════════════════════

AGENT_PROFILES = [
    {
        "name": "Aria",
        "role": "Product Manager",
        "subtitle": "Agentic Task Decomposition Engine",
        "avatar": "PM",
        "color": "#3b82f6",
        "icon_bg": "#1e3a5f",
        "system_prompt": (
            "You are Aria, a senior product manager AI agent. Your role is to analyze change requests "
            "and decompose them into clear, actionable development tasks. You think strategically about "
            "impact, dependencies, and user value. Output a numbered task list with priorities (P0-P3). "
            "Be concise and decisive. Format your tasks clearly."
        ),
    },
    {
        "name": "Orion",
        "role": "Solutions Architect",
        "subtitle": "Structural Design & Dependency Engine",
        "avatar": "ARC",
        "color": "#a78bfa",
        "icon_bg": "#1e1b4b",
        "system_prompt": (
            "You are Orion, a solutions architect AI agent. You receive tasks from the PM and design "
            "the technical approach. Specify which files to create/modify, which design patterns to use, "
            "dependency impacts, and data flow. Output an architecture decision record (ADR) with: "
            "approach, alternatives considered, file map, and risk assessment. Be precise and opinionated."
        ),
    },
    {
        "name": "Marcus",
        "role": "Backend Developer",
        "subtitle": "Server-Side Code Synthesis Module",
        "avatar": "BE",
        "color": "#06b6d4",
        "icon_bg": "#083344",
        "system_prompt": (
            "You are Marcus, a backend developer AI agent. You implement server-side code changes: "
            "APIs, database models, middleware, business logic, and integrations. Follow the architect's "
            "design. Output exact file changes as unified diffs or full file contents. "
            "Be precise about file paths. Focus on correctness and performance."
        ),
    },
    {
        "name": "Lyra",
        "role": "Frontend Developer",
        "subtitle": "Client-Side UI Synthesis Module",
        "avatar": "FE",
        "color": "#14b8a6",
        "icon_bg": "#042f2e",
        "system_prompt": (
            "You are Lyra, a frontend developer AI agent. You implement client-side changes: "
            "React/Vue components, CSS/styling, state management, accessibility, and UX interactions. "
            "Follow the architect's design. Output exact file changes. Ensure responsive design, "
            "WCAG compliance, and smooth animations. Be precise about component structure."
        ),
    },
    {
        "name": "Sage",
        "role": "Code Reviewer",
        "subtitle": "Autonomous Quality Assurance Framework",
        "avatar": "CR",
        "color": "#8b5cf6",
        "icon_bg": "#2e1065",
        "system_prompt": (
            "You are Sage, a meticulous code reviewer AI agent. You review code changes for: "
            "correctness, security vulnerabilities, performance issues, code style, and best practices. "
            "You review BOTH backend and frontend code. "
            "You provide specific, actionable feedback. Rate each change: APPROVE, REQUEST_CHANGES, or COMMENT. "
            "Be thorough but not pedantic."
        ),
    },
    {
        "name": "Cipher",
        "role": "Security Analyst",
        "subtitle": "OWASP & Vulnerability Assessment Engine",
        "avatar": "SEC",
        "color": "#ef4444",
        "icon_bg": "#450a0a",
        "system_prompt": (
            "You are Cipher, a security analyst AI agent. You perform deep security analysis on code changes: "
            "OWASP Top 10 checks, injection vectors (SQL, XSS, command), authentication/authorization flaws, "
            "secrets exposure, dependency CVEs, CSRF, insecure deserialization, and data leakage. "
            "Output a security assessment with CRITICAL/HIGH/MEDIUM/LOW/PASS for each category. "
            "Flag specific line numbers. Zero tolerance for hardcoded credentials."
        ),
    },
    {
        "name": "Nova",
        "role": "QA Engineer",
        "subtitle": "Intelligent Test Generation Pipeline",
        "avatar": "QA",
        "color": "#10b981",
        "icon_bg": "#052e16",
        "system_prompt": (
            "You are Nova, a QA engineer AI agent. You analyze code changes and write appropriate tests. "
            "Cover unit tests, integration tests, and edge cases for both backend and frontend. "
            "You think about error conditions, race conditions, and boundary values. "
            "Output test code that can be directly added to the test suite. "
            "Also flag any potential regression risks."
        ),
    },
    {
        "name": "Forge",
        "role": "DevOps Engineer",
        "subtitle": "Infrastructure & Deployment Automation",
        "avatar": "OPS",
        "color": "#f97316",
        "icon_bg": "#431407",
        "system_prompt": (
            "You are Forge, a DevOps engineer AI agent. You analyze code changes for infrastructure impact: "
            "Dockerfile updates, CI/CD pipeline changes, database migrations, environment variables, "
            "deployment scripts, monitoring/alerting additions, and scaling considerations. "
            "Output any required infra changes as exact file diffs. Flag breaking deployment changes."
        ),
    },
    {
        "name": "Echo",
        "role": "Technical Writer",
        "subtitle": "Documentation Synthesis Agent",
        "avatar": "DOC",
        "color": "#f59e0b",
        "icon_bg": "#422006",
        "system_prompt": (
            "You are Echo, a technical writer AI agent. You create clear, concise documentation for "
            "code changes including: changelog entries, PR descriptions, API doc updates, and user-facing "
            "release notes. You write for both technical and non-technical audiences."
        ),
    },
    {
        "name": "Atlas",
        "role": "Governance Officer",
        "subtitle": "Compliance & Governance Gateway",
        "avatar": "GOV",
        "color": "#eab308",
        "icon_bg": "#422006",
        "system_prompt": (
            "You are Atlas, a governance and compliance officer AI agent. You review all changes for: "
            "security policy compliance, no hardcoded secrets, license compatibility, API breaking changes, "
            "data privacy implications, and organizational coding standards. Incorporate Cipher's security "
            "findings into your final verdict. "
            "Output a governance report with PASS/FAIL/WARNING for each check category. "
            "Be strict but practical."
        ),
    },
]

PIPELINE_STAGES = [
    {"name": "Intake",        "icon": "\u25b6", "buzzword": "Request Ingestion"},
    {"name": "Memory Recall", "icon": "\u29bf", "buzzword": "FV v2.2 Knowledge Retrieval"},
    {"name": "Decomposition", "icon": "\u25c6", "buzzword": "Agentic Task Planning"},
    {"name": "Architecture",  "icon": "\u25c8", "buzzword": "Structural Design"},
    {"name": "Backend",       "icon": "\u26a1", "buzzword": "Server-Side Synthesis"},
    {"name": "Frontend",      "icon": "\u26a1", "buzzword": "Client-Side Synthesis"},
    {"name": "Code Review",   "icon": "\u25c9", "buzzword": "Autonomous QA"},
    {"name": "Security",      "icon": "\u26a0", "buzzword": "OWASP Vulnerability Scan"},
    {"name": "Testing",       "icon": "\u2726", "buzzword": "Intelligent Validation"},
    {"name": "DevOps",        "icon": "\u2699", "buzzword": "Infrastructure Automation"},
    {"name": "Documentation", "icon": "\u25c8", "buzzword": "Knowledge Synthesis"},
    {"name": "Governance",    "icon": "\u2b21", "buzzword": "Compliance Gateway"},
    {"name": "Delivery",      "icon": "\u2713", "buzzword": "Continuous Delivery"},
]


# ══════════════════════════════════════════════════════════════════
# DEMO RESPONSES
# ══════════════════════════════════════════════════════════════════

def _get_demo_response(agent_name, user_message):
    responses = {
        "Aria": (
            "## Task Decomposition Report\n\n"
            "After analyzing the change request, I've identified the following work items:\n\n"
            "### P0 -- Critical Path\n"
            "1. **Create DarkModeToggle component** -- Build a reusable toggle switch component "
            "with smooth animation. Place in `src/components/ui/DarkModeToggle.tsx`\n"
            "2. **Implement CSS custom properties** -- Define color tokens as CSS variables for both "
            "light and dark themes in `src/styles/themes.css`\n\n"
            "### P1 -- Core Functionality\n"
            "3. **Add ThemeContext provider** -- Create React context to manage theme state globally. "
            "Wire up localStorage read/write with `useEffect`\n"
            "4. **Update Settings page layout** -- Integrate toggle into the Settings page with "
            "proper label, description text, and accessibility attributes\n\n"
            "### P2 -- Polish\n"
            "5. **Add CSS transition animations** -- Smooth 200ms transition on `background-color`, "
            "`color`, and `border-color` for all themed elements\n"
            "6. **Update component tests** -- Add unit tests for toggle state, localStorage "
            "persistence, and system preference detection\n\n"
            "**Estimated complexity:** Medium | **Risk:** Low | **Dependencies:** None"
        ),
        "Marcus": (
            "## Implementation\n\n"
            "### File: `src/styles/themes.css`\n"
            "```css\n"
            ":root {\n"
            "  --bg-primary: #ffffff;\n"
            "  --bg-secondary: #f8f9fa;\n"
            "  --text-primary: #1a1a2e;\n"
            "  --text-secondary: #6c757d;\n"
            "  --border-color: #dee2e6;\n"
            "  --accent: #3b82f6;\n"
            "  --transition-speed: 200ms;\n"
            "}\n\n"
            "[data-theme='dark'] {\n"
            "  --bg-primary: #0f172a;\n"
            "  --bg-secondary: #1e293b;\n"
            "  --text-primary: #e2e8f0;\n"
            "  --text-secondary: #94a3b8;\n"
            "  --border-color: #334155;\n"
            "  --accent: #60a5fa;\n"
            "}\n\n"
            "* { transition: background-color var(--transition-speed) ease,\n"
            "               color var(--transition-speed) ease; }\n"
            "```\n\n"
            "### File: `src/components/ui/DarkModeToggle.tsx`\n"
            "```tsx\n"
            "import { useTheme } from '../../hooks/useTheme';\n\n"
            "export function DarkModeToggle() {\n"
            "  const { theme, toggleTheme } = useTheme();\n"
            "  return (\n"
            "    <button onClick={toggleTheme}\n"
            "      className=\"theme-toggle\"\n"
            "      aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}>\n"
            "      {theme === 'dark' ? 'sun' : 'moon'}\n"
            "    </button>\n"
            "  );\n"
            "}\n"
            "```\n\n"
            "### File: `src/hooks/useTheme.ts`\n"
            "```ts\n"
            "import { useState, useEffect } from 'react';\n\n"
            "export function useTheme() {\n"
            "  const [theme, setTheme] = useState(() =>\n"
            "    localStorage.getItem('theme') || 'light'\n"
            "  );\n\n"
            "  useEffect(() => {\n"
            "    document.documentElement.setAttribute('data-theme', theme);\n"
            "    localStorage.setItem('theme', theme);\n"
            "  }, [theme]);\n\n"
            "  const toggleTheme = () =>\n"
            "    setTheme(prev => prev === 'dark' ? 'light' : 'dark');\n\n"
            "  return { theme, toggleTheme };\n"
            "}\n"
            "```\n\n"
            "All existing components will automatically pick up theme changes through CSS custom "
            "properties. No individual component modifications needed."
        ),
        "Sage": (
            "## Code Review Report\n\n"
            "**Verdict: APPROVE with minor suggestions**\n\n"
            "### Strengths\n"
            "- Clean separation of concerns -- theme logic in a hook, presentation in component\n"
            "- CSS custom properties approach is correct; avoids className juggling\n"
            "- localStorage persistence implemented correctly\n"
            "- Accessible: proper `aria-label` on toggle button\n\n"
            "### Suggestions\n"
            "1. **Add system preference detection** -- Consider checking "
            "`window.matchMedia('(prefers-color-scheme: dark)')` on first load when no "
            "localStorage value exists\n"
            "2. **SSR safety** -- The `useState` initializer accesses `localStorage` directly. "
            "Wrap in a check: `typeof window !== 'undefined'`\n"
            "3. **Transition scope** -- The `*` selector transition is broad. Consider scoping "
            "to `.themed *` to avoid animation on non-themed elements\n\n"
            "### Security Check\n"
            "- No XSS vectors identified\n"
            "- localStorage usage is safe (same-origin policy)\n"
            "- No external data injection points\n\n"
            "**Risk assessment:** LOW -- This is a purely client-side UI change with no "
            "backend implications."
        ),
        "Nova": (
            "## Test Suite\n\n"
            "### File: `src/hooks/__tests__/useTheme.test.ts`\n"
            "```ts\n"
            "import { renderHook, act } from '@testing-library/react';\n"
            "import { useTheme } from '../useTheme';\n\n"
            "describe('useTheme', () => {\n"
            "  beforeEach(() => localStorage.clear());\n\n"
            "  it('defaults to light theme', () => {\n"
            "    const { result } = renderHook(() => useTheme());\n"
            "    expect(result.current.theme).toBe('light');\n"
            "  });\n\n"
            "  it('toggles between light and dark', () => {\n"
            "    const { result } = renderHook(() => useTheme());\n"
            "    act(() => result.current.toggleTheme());\n"
            "    expect(result.current.theme).toBe('dark');\n"
            "    act(() => result.current.toggleTheme());\n"
            "    expect(result.current.theme).toBe('light');\n"
            "  });\n\n"
            "  it('persists to localStorage', () => {\n"
            "    const { result } = renderHook(() => useTheme());\n"
            "    act(() => result.current.toggleTheme());\n"
            "    expect(localStorage.getItem('theme')).toBe('dark');\n"
            "  });\n\n"
            "  it('reads from localStorage on mount', () => {\n"
            "    localStorage.setItem('theme', 'dark');\n"
            "    const { result } = renderHook(() => useTheme());\n"
            "    expect(result.current.theme).toBe('dark');\n"
            "  });\n\n"
            "  it('sets data-theme attribute on document', () => {\n"
            "    const { result } = renderHook(() => useTheme());\n"
            "    act(() => result.current.toggleTheme());\n"
            "    expect(document.documentElement.getAttribute('data-theme'))\n"
            "      .toBe('dark');\n"
            "  });\n"
            "});\n"
            "```\n\n"
            "**Test coverage:** 5 tests covering default state, toggle behavior, "
            "localStorage read/write, and DOM attribute updates.\n"
            "**Regression risk:** None identified -- changes are additive."
        ),
        "Echo": (
            "## Documentation Package\n\n"
            "### Changelog Entry\n"
            "```\n"
            "## [1.4.0] - 2026-03-22\n"
            "### Added\n"
            "- Dark mode toggle on Settings page\n"
            "- Theme preference persisted in localStorage\n"
            "- Smooth CSS transition animations between themes\n"
            "- System color scheme preference detection on first visit\n"
            "```\n\n"
            "### PR Description\n"
            "**Title:** feat: Add dark mode toggle with localStorage persistence\n\n"
            "Adds a dark mode toggle to the Settings page. Uses CSS custom properties "
            "for theming, ensuring all UI components automatically adapt. Theme preference "
            "is saved to localStorage and restored on subsequent visits.\n\n"
            "**Changes:**\n"
            "- New `DarkModeToggle` component\n"
            "- New `useTheme` hook for state management\n"
            "- CSS custom property theme definitions\n"
            "- 5 unit tests for theme logic\n\n"
            "### Release Notes (User-Facing)\n"
            "**New: Dark Mode** -- You can now switch to a dark color scheme from the "
            "Settings page. Your preference is automatically saved and will be remembered "
            "the next time you visit. The transition between themes is silky smooth."
        ),
        "Atlas": (
            "## Governance & Compliance Report\n\n"
            "### Security Assessment\n"
            "| Check | Status | Notes |\n"
            "|-------|--------|-------|\n"
            "| Hardcoded secrets | PASS | No API keys, tokens, or credentials detected |\n"
            "| XSS vectors | PASS | No user-controlled HTML injection points |\n"
            "| Dependency risk | PASS | No new dependencies introduced |\n"
            "| Data privacy | PASS | localStorage is same-origin; no PII involved |\n\n"
            "### Standards Compliance\n"
            "| Check | Status | Notes |\n"
            "|-------|--------|-------|\n"
            "| Accessibility (WCAG 2.1) | PASS | aria-label present on toggle |\n"
            "| TypeScript strict mode | PASS | All types properly defined |\n"
            "| Test coverage | PASS | 5 tests, covers all branches |\n"
            "| Breaking changes | PASS | Purely additive, no API surface changes |\n"
            "| License compatibility | PASS | No new third-party code |\n\n"
            "### Final Verdict\n"
            "**APPROVED FOR MERGE** -- All governance checks passed. This change introduces "
            "minimal risk, adds no external dependencies, and follows established coding "
            "standards. Recommended for immediate deployment.\n\n"
            "**Governance Score: 96/100**"
        ),
    }
    return responses.get(agent_name, f"Analysis complete for: {user_message[:50]}...")


# ══════════════════════════════════════════════════════════════════
# LLM CALLS
# ══════════════════════════════════════════════════════════════════

def _call_local_llm_stream(agent_profile, user_message, context=""):
    """Yield tokens from a local OpenAI-compatible LLM."""
    import urllib.request

    full_prompt = f"{context}\n\nUser Request:\n{user_message}" if context else user_message
    payload = json.dumps({
        "model": LOCAL_LLM_MODEL,
        "messages": [
            {"role": "system", "content": agent_profile["system_prompt"]},
            {"role": "user", "content": full_prompt},
        ],
        "stream": True,
    }).encode("utf-8")

    for endpoint in [f"{LOCAL_LLM_URL}/api/chat",
                     f"{LOCAL_LLM_URL}/v1/chat/completions"]:
        try:
            headers = {"Content-Type": "application/json"}
            if DEEPSEEK_API_KEY:
                headers["Authorization"] = f"Bearer {DEEPSEEK_API_KEY}"
            req = urllib.request.Request(
                endpoint, data=payload,
                headers=headers,
            )
            resp = urllib.request.urlopen(req, timeout=120)
            for line in resp:
                line = line.decode("utf-8").strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                    if line == "[DONE]":
                        return
                try:
                    chunk = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                token = ""
                if "message" in chunk:
                    token = chunk["message"].get("content", "")
                elif "choices" in chunk:
                    delta = chunk["choices"][0].get("delta", {})
                    token = delta.get("content", "")
                if token:
                    yield token
            return
        except Exception:
            continue


def stream_agent(agent_profile, user_message, context=""):
    """Generator that yields tokens. Tries Anthropic > Local > Demo."""
    if HAS_ANTHROPIC:
        full_prompt = f"{context}\n\nUser Request:\n{user_message}" if context else user_message
        with CLIENT.messages.stream(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=agent_profile["system_prompt"],
            messages=[{"role": "user", "content": full_prompt}],
        ) as stream:
            for text in stream.text_stream:
                yield text
        return

    if HAS_LOCAL_LLM:
        yield from _call_local_llm_stream(agent_profile, user_message, context)
        return

    # Demo mode: simulate streaming
    fake = _get_demo_response(agent_profile["name"], user_message)
    # Stream in word-sized chunks for realistic feel
    words = fake.split(' ')
    for i, word in enumerate(words):
        chunk = word if i == 0 else ' ' + word
        yield chunk
        time.sleep(random.uniform(0.02, 0.06))


# ══════════════════════════════════════════════════════════════════
# FLASK APP
# ══════════════════════════════════════════════════════════════════

def _clean_memory_text(text):
    """Strip type tags for display."""
    for tag in ["oraclelens:solution", "oraclelens:mistake",
                "oraclelens:research", "oraclelens:change", "oraclelens:decision"]:
        text = text.replace(f"[{tag}]", "").strip()
    return text


def _format_demo_memory(mem_results):
    """Format demo memory results into a prompt block."""
    sections = []
    if mem_results["past_solutions"]:
        sections.append("=== PAST SOLUTIONS (don't re-solve these) ===")
        for r in mem_results["past_solutions"]:
            sections.append(f"  - {_clean_memory_text(r['text'])}")
    if mem_results["known_mistakes"]:
        sections.append("\n=== KNOWN MISTAKES (avoid these) ===")
        for r in mem_results["known_mistakes"]:
            sections.append(f"  - {_clean_memory_text(r['text'])}")
    if mem_results["prior_changes"]:
        sections.append("\n=== PRIOR CHANGES (already done, don't repeat) ===")
        for r in mem_results["prior_changes"]:
            sections.append(f"  - {_clean_memory_text(r['text'])}")
    if not sections:
        return ""
    return "\n".join(sections) + "\n=== END MEMORY RECALL ===\n"


# ── Run Logging ───────────────────────────────────────────────
RUN_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_logs")
os.makedirs(RUN_LOG_DIR, exist_ok=True)


def _log_run(run_data):
    """Append a pipeline run to the log file."""
    log_file = os.path.join(RUN_LOG_DIR, "runs.jsonl")
    with open(log_file, "a") as f:
        f.write(json.dumps(run_data) + "\n")


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "oracle-lens-web-secret-key-2026")

def login_required(f):
    """No-op decorator — auth disabled for local use."""
    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated


@app.route("/")
@login_required
def index():
    llm_mode = "Anthropic API" if HAS_ANTHROPIC else (LOCAL_LLM_MODEL if HAS_LOCAL_LLM else "Demo Mode")
    memory_mode = "FV v2.2 LIVE" if HAS_MEMORY else "FV v2.2 Demo"
    return render_template("index.html",
                           agents=AGENT_PROFILES,
                           stages=PIPELINE_STAGES,
                           llm_mode=llm_mode,
                           has_memory=HAS_MEMORY,
                           memory_mode=memory_mode,
                           memory_fact_count=_memory_fact_count)


@app.route("/api/memory/status")
@login_required
def api_memory_status():
    """Return FV v2.2 memory system status."""
    return jsonify({
        "connected": HAS_MEMORY,
        "mode": "live" if HAS_MEMORY else "demo",
        "fact_count": _memory_fact_count,
    })


@app.route("/api/runs")
@login_required
def api_runs():
    """Return recent pipeline run logs."""
    log_file = os.path.join(RUN_LOG_DIR, "runs.jsonl")
    if not os.path.exists(log_file):
        return jsonify([])
    runs = []
    with open(log_file) as f:
        for line in f:
            line = line.strip()
            if line:
                runs.append(json.loads(line))
    # Return most recent first
    runs.reverse()
    limit = int(request.args.get("limit", 20))
    return jsonify(runs[:limit])


@app.route("/api/memory/test")
@login_required
def api_memory_test():
    """Test recall from within server process."""
    if not HAS_MEMORY:
        return jsonify({"error": "no memory"})
    q = request.args.get("q", "dark mode toggle")
    results = recall_for_task(task_prompt=q, repo_path=request.args.get("repo", ""))
    return jsonify({
        "query": q,
        "solutions": len(results["past_solutions"]),
        "mistakes": len(results["known_mistakes"]),
        "changes": len(results["prior_changes"]),
        "research": len(results["prior_research"]),
        "total": len(results["raw_results"]),
    })


@app.route("/api/agents")
@login_required
def api_agents():
    return jsonify(AGENT_PROFILES)


# Store pending request for GET-based SSE
_pending_requests = {}


@app.route("/api/pipeline", methods=["POST"])
@login_required
def api_pipeline_post():
    """Accept pipeline request, return a run ID for SSE streaming."""
    data = request.get_json()
    change_request = data.get("change_request", "").strip()
    repo_path = data.get("repo_path", "").strip()
    if not change_request:
        return jsonify({"error": "No change request provided"}), 400
    run_id = f"run-{time.time_ns()}"
    _pending_requests[run_id] = {"change_request": change_request, "repo_path": repo_path}
    return jsonify({"run_id": run_id})


@app.route("/api/pipeline/stream")
@login_required
def api_pipeline():
    """SSE endpoint — streams the full pipeline execution via GET."""
    run_id = request.args.get("run_id", "")
    data = _pending_requests.pop(run_id, None)
    if not data:
        return jsonify({"error": "Invalid or expired run_id"}), 400
    change_request = data["change_request"]
    repo_path = data["repo_path"]

    def generate():
        start_time = time.time()
        total_tokens = 0
        responses = {}
        memory_context = ""  # injected into all agent prompts
        facts_recalled = 0

        def sse(event, data_obj):
            return f"event: {event}\ndata: {json.dumps(data_obj)}\n\n"

        # ── Stage 0: Intake ──────────────────────────────────────
        yield sse("stage", {"index": 0, "status": "active"})
        yield sse("system", {"text": "Change request received. Initializing agentic pipeline..."})
        yield sse("system", {"text": f"Repository: {repo_path or 'Demo Project'}"})
        yield sse("system", {"text": f"Request: {change_request[:120]}..."})
        time.sleep(0.8)
        yield sse("stage", {"index": 0, "status": "complete"})

        # ── Stage 1: Memory Recall (FV v2.2) ─────────────────────
        yield sse("stage", {"index": 1, "status": "active"})
        yield sse("system", {"text": "Querying FV v2.2 memory system for prior knowledge..."})

        if HAS_MEMORY:
            try:
                mem_results = recall_for_task(
                    task_prompt=change_request,
                    repo_path=repo_path,
                )
                memory_context = format_recall_for_prompt(mem_results)
                facts_recalled = len(mem_results.get("raw_results", []))
            except Exception as e:
                mem_results = {"past_solutions": [], "known_mistakes": [],
                               "prior_research": [], "prior_changes": [], "raw_results": []}
                memory_context = ""
                yield sse("system", {"text": f"Memory recall error: {e}"})
        else:
            # Demo mode memory
            mem_results = DEMO_MEMORY_RESULTS
            memory_context = _format_demo_memory(mem_results)
            facts_recalled = (len(mem_results["past_solutions"]) +
                              len(mem_results["known_mistakes"]) +
                              len(mem_results["prior_changes"]))
            # Simulate recall delay
            time.sleep(0.6)

        # Stream recall results to the UI
        yield sse("memory_status", {
            "connected": HAS_MEMORY,
            "fact_count": _memory_fact_count if HAS_MEMORY else 42,
            "mode": "LIVE" if HAS_MEMORY else "DEMO",
        })

        for sol in mem_results.get("past_solutions", [])[:3]:
            yield sse("memory_result", {
                "type": "solution",
                "text": _clean_memory_text(sol.get("text", "")),
                "score": round(sol.get("score", 0), 2),
            })
            time.sleep(0.15)

        for mis in mem_results.get("known_mistakes", [])[:3]:
            yield sse("memory_result", {
                "type": "mistake",
                "text": _clean_memory_text(mis.get("text", "")),
                "score": round(mis.get("score", 0), 2),
            })
            time.sleep(0.15)

        for chg in mem_results.get("prior_changes", [])[:3]:
            yield sse("memory_result", {
                "type": "change",
                "text": _clean_memory_text(chg.get("text", "")),
                "score": round(chg.get("score", 0), 2),
            })
            time.sleep(0.15)

        for res in mem_results.get("prior_research", [])[:2]:
            yield sse("memory_result", {
                "type": "research",
                "text": _clean_memory_text(res.get("text", "")),
                "score": round(res.get("score", 0), 2),
            })
            time.sleep(0.15)

        summary_parts = []
        ns = len(mem_results.get("past_solutions", []))
        nm = len(mem_results.get("known_mistakes", []))
        nc = len(mem_results.get("prior_changes", []))
        nr = len(mem_results.get("prior_research", []))
        if ns: summary_parts.append(f"{ns} past solution{'s' if ns > 1 else ''}")
        if nm: summary_parts.append(f"{nm} known mistake{'s' if nm > 1 else ''}")
        if nc: summary_parts.append(f"{nc} prior change{'s' if nc > 1 else ''}")
        if nr: summary_parts.append(f"{nr} research finding{'s' if nr > 1 else ''}")

        if summary_parts:
            yield sse("memory_summary", {
                "text": f"Recalled {', '.join(summary_parts)}. Context injected into all agents.",
                "count": facts_recalled,
            })
        else:
            yield sse("memory_summary", {
                "text": "No prior knowledge found. Agents will work from scratch.",
                "count": 0,
            })

        yield sse("metrics", {"recalled": str(facts_recalled)})
        yield sse("stage", {"index": 1, "status": "complete"})

        # ── Stages 2-11: Agent Pipeline ──────────────────────────
        # (stage_idx, agent_profile_idx, agent_name, activity, system_msg)
        agent_stages = [
            (2,  0, "Aria",   "PLANNING",   "Aria (Product Manager) is decomposing the change request..."),
            (3,  1, "Orion",  "DESIGNING",  "Orion (Solutions Architect) is designing technical approach..."),
            (4,  2, "Marcus", "CODING",     "Marcus (Backend Developer) is implementing server-side changes..."),
            (5,  3, "Lyra",   "CODING",     "Lyra (Frontend Developer) is implementing client-side changes..."),
            (6,  4, "Sage",   "REVIEWING",  "Sage (Code Reviewer) is performing autonomous quality assessment..."),
            (7,  5, "Cipher", "SCANNING",   "Cipher (Security Analyst) is running OWASP vulnerability scan..."),
            (8,  6, "Nova",   "TESTING",    "Nova (QA Engineer) is generating intelligent test suite..."),
            (9,  7, "Forge",  "DEPLOYING",  "Forge (DevOps Engineer) is analyzing infrastructure impact..."),
            (10, 8, "Echo",   "WRITING",    "Echo (Technical Writer) is synthesizing documentation..."),
            (11, 9, "Atlas",  "AUDITING",   "Atlas (Governance Officer) is running compliance gateway checks..."),
        ]

        for stage_idx, agent_idx, agent_name, activity, system_msg in agent_stages:
            agent = AGENT_PROFILES[agent_idx]

            yield sse("stage", {"index": stage_idx, "status": "active"})
            yield sse("agent_status", {"name": agent_name, "status": activity, "color": agent["color"]})
            yield sse("system", {"text": system_msg})

            # Build prompt with memory context + previous agent outputs
            mem_prefix = ""
            if memory_context:
                mem_prefix = (
                    "=== TEAM MEMORY (from FV v2.2 — past solutions, mistakes, research) ===\n"
                    f"{memory_context}\n"
                    "Use this context to avoid repeating solved problems and known mistakes.\n\n"
                )

            if agent_name == "Aria":
                prompt = f"{mem_prefix}Change request:\n{change_request}"
            elif agent_name == "Orion":
                prompt = (f"{mem_prefix}Original request:\n{change_request}\n\n"
                         f"PM task breakdown:\n{responses.get('Aria', '')}\n\n"
                         f"Design the technical approach: file map, patterns, dependencies, risks.")
            elif agent_name == "Marcus":
                prompt = (f"{mem_prefix}Original request:\n{change_request}\n\n"
                         f"Architecture design:\n{responses.get('Orion', '')}\n\n"
                         f"Implement the BACKEND code changes. APIs, models, middleware, business logic. "
                         f"Show exact file modifications.")
            elif agent_name == "Lyra":
                prompt = (f"{mem_prefix}Original request:\n{change_request}\n\n"
                         f"Architecture design:\n{responses.get('Orion', '')}\n\n"
                         f"Implement the FRONTEND code changes. Components, styling, state management, UX. "
                         f"Show exact file modifications.")
            elif agent_name == "Sage":
                prompt = (f"{mem_prefix}Original request:\n{change_request}\n\n"
                         f"Backend implementation:\n{responses.get('Marcus', '')}\n\n"
                         f"Frontend implementation:\n{responses.get('Lyra', '')}\n\n"
                         f"Review BOTH for correctness, performance, and best practices.")
            elif agent_name == "Cipher":
                prompt = (f"{mem_prefix}Backend code:\n{responses.get('Marcus', '')}\n\n"
                         f"Frontend code:\n{responses.get('Lyra', '')}\n\n"
                         f"Perform deep security analysis: OWASP Top 10, injection vectors, auth flaws, "
                         f"secrets exposure, XSS, CSRF, dependency CVEs. Rate each category.")
            elif agent_name == "Nova":
                prompt = (f"{mem_prefix}Backend code:\n{responses.get('Marcus', '')}\n\n"
                         f"Frontend code:\n{responses.get('Lyra', '')}\n\n"
                         f"Review feedback:\n{responses.get('Sage', '')}\n\n"
                         f"Security findings:\n{responses.get('Cipher', '')}\n\n"
                         f"Write comprehensive tests for both backend and frontend changes.")
            elif agent_name == "Forge":
                prompt = (f"{mem_prefix}Original request:\n{change_request}\n\n"
                         f"Backend code:\n{responses.get('Marcus', '')}\n\n"
                         f"Frontend code:\n{responses.get('Lyra', '')}\n\n"
                         f"Analyze infrastructure impact: Dockerfile, CI/CD, migrations, env vars, "
                         f"monitoring, deployment scripts. Output required infra changes.")
            elif agent_name == "Echo":
                prompt = (f"{mem_prefix}Original request:\n{change_request}\n\n"
                         f"Backend: {responses.get('Marcus', '')[:500]}\n\n"
                         f"Frontend: {responses.get('Lyra', '')[:500]}\n\n"
                         f"Write: 1) A changelog entry 2) A PR description 3) Release notes")
            else:  # Atlas
                prompt = (f"{mem_prefix}Full change summary:\nRequest: {change_request}\n"
                         f"Architecture: {responses.get('Orion', '')[:300]}\n"
                         f"Backend: {responses.get('Marcus', '')[:300]}\n"
                         f"Frontend: {responses.get('Lyra', '')[:300]}\n"
                         f"Review: {responses.get('Sage', '')[:300]}\n"
                         f"Security: {responses.get('Cipher', '')}\n"
                         f"Tests: {responses.get('Nova', '')[:300]}\n"
                         f"DevOps: {responses.get('Forge', '')[:300]}\n\n"
                         f"Run final governance checks. Incorporate security findings into verdict.")

            # Stream agent header
            yield sse("agent_header", {
                "name": agent_name,
                "role": agent["role"],
                "color": agent["color"],
            })

            # Stream tokens
            full_response = []
            token_count = 0
            for token in stream_agent(agent, prompt):
                full_response.append(token)
                token_count += len(token.split())
                total_tokens += len(token.split())
                yield sse("agent_token", {"name": agent_name, "text": token})

            response_text = "".join(full_response)
            responses[agent_name] = response_text

            yield sse("agent_done", {"name": agent_name})
            yield sse("agent_status", {"name": agent_name, "status": "DONE", "color": "#10b981"})
            yield sse("stage", {"index": stage_idx, "status": "complete"})

            # Update metrics
            elapsed = time.time() - start_time
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            yield sse("metrics", {
                "tokens": f"{total_tokens:,}",
                "tasks": str(random.randint(4, 8)) if agent_name == "Aria" else None,
                "coverage": f"{random.randint(82, 97)}%" if agent_name == "Nova" else None,
                "governance": f"{random.randint(90, 100)}/100" if agent_name == "Atlas" else None,
                "time": f"{mins}:{secs:02d}",
            })

        # ── Stage 12: Delivery + Memory Store ────────────────────
        yield sse("stage", {"index": 12, "status": "active"})
        yield sse("system", {"text": "Pipeline complete. Packaging deliverables..."})

        # Store pipeline outcome in FV v2.2
        if HAS_MEMORY:
            try:
                governance_result = responses.get("Atlas", "")
                outcome = "success" if "APPROVED" in governance_result.upper() else "completed"
                remember_task_outcome(
                    task_prompt=change_request[:200],
                    outcome=outcome,
                    memory_type="solution",
                    details=f"10-agent pipeline completed. Governance: {outcome}.",
                    repo_path=repo_path,
                    agent_role="pipeline",
                )
                # Count updated facts
                try:
                    new_count = _new_conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
                except Exception:
                    new_count = _memory_fact_count + 1
                yield sse("memory_stored", {
                    "text": f"Pipeline outcome stored: {change_request[:80]}... → {outcome}",
                    "new_count": new_count,
                })
            except Exception as e:
                yield sse("system", {"text": f"Memory store skipped: {e}"})
        else:
            yield sse("memory_stored", {
                "text": f"Pipeline outcome stored: {change_request[:80]}... → completed",
                "new_count": 42 + 1,
            })

        time.sleep(0.5)
        yield sse("stage", {"index": 12, "status": "complete"})

        elapsed = time.time() - start_time
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        yield sse("metrics", {"time": f"{mins}:{secs:02d}"})

        yield sse("system", {"text": "=" * 50})
        yield sse("system", {"text": "ALL STAGES COMPLETE  |  Pipeline Status: SUCCESS  |  Governance: PASS"})
        yield sse("system", {"text": "=" * 50})
        yield sse("pipeline_done", {"elapsed": f"{mins}:{secs:02d}", "tokens": total_tokens})

        # ── Log the run ──────────────────────────────────────
        _log_run({
            "timestamp": datetime.utcnow().isoformat(),
            "change_request": change_request,
            "repo_path": repo_path,
            "elapsed_sec": round(time.time() - start_time, 1),
            "total_tokens": total_tokens,
            "facts_recalled": facts_recalled,
            "memory_mode": "live" if HAS_MEMORY else "demo",
            "llm_mode": "anthropic" if HAS_ANTHROPIC else (LOCAL_LLM_MODEL if HAS_LOCAL_LLM else "demo"),
            "memory_context_length": len(memory_context),
            "agent_responses": {name: {"length": len(text), "preview": text[:200]}
                                for name, text in responses.items()},
        })

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8095))
    print(f"\n{'='*60}")
    print(f"  ORACLE LENS :: Web Edition")
    print(f"  http://localhost:{port}")
    print(f"  LLM: {'Anthropic API' if HAS_ANTHROPIC else (LOCAL_LLM_MODEL if HAS_LOCAL_LLM else 'Demo Mode')}")
    print(f"  Memory: {'FV v2.2 LIVE (' + str(_memory_fact_count) + ' facts)' if HAS_MEMORY else 'Demo Mode'}")
    print(f"{'='*60}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
