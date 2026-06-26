# demo.py — Virtual Employees Memory & Grounding Layer (with FV v2.2)
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.interface import (
    get_context,
    record_lesson,
    remember_outcome,
    recall_history,
)

print("=" * 60)
print("DEMO - Virtual Employees Memory & Grounding Layer")
print("       with FV v2.2 Long-Term Memory")
print("=" * 60)

# Step 1: Write lessons to gated memory
print("\n[STEP 1] Writing lessons to gated memory...")

entry1 = record_lesson(
    role="supervisor",
    category="testing",
    lesson="Always use unittest for Python agent modules.",
    context="Sprint 1 setup"
)
print(f"  OK - Lesson written by supervisor: {entry1['id']}")

entry2 = record_lesson(
    role="qa",
    category="memory",
    lesson="Only supervisor and QA can write to memory.",
    context="Gated memory design decision"
)
print(f"  OK - Lesson written by qa: {entry2['id']}")

# Step 2: Store a task outcome in FV v2.2
print("\n[STEP 2] Remembering task outcomes in FV v2.2...")

fact_id = remember_outcome(
    task_prompt="Fix JWT token validation in auth middleware",
    outcome="success",
    memory_type="solution",
    details="The issue was expired tokens not being caught because the "
            "verify_exp parameter was False by default in PyJWT 2.x. "
            "Fixed by explicitly setting options={'verify_exp': True}.",
    repo_path="/home/om/projects/example-api",
    agent_role="developer",
)
print(f"  OK - Solution stored in FV: {fact_id}")

fact_id2 = remember_outcome(
    task_prompt="Add Redis caching to user lookup endpoint",
    outcome="failed: Redis connection pool exhaustion under load",
    memory_type="mistake",
    details="Used default max_connections=10 which was too low for "
            "concurrent API calls. Need to set max_connections=50+ "
            "and add connection timeout handling.",
    repo_path="/home/om/projects/example-api",
    agent_role="developer",
)
print(f"  OK - Mistake stored in FV: {fact_id2}")

# Step 3: Full context retrieval (gated memory + FV + repo)
print("\n[STEP 3] Getting full context for a new task...")
result = get_context(
    role="developer",
    task_prompt="Fix authentication bug in the API middleware",
    repo_path=".",
    task_id="demo_run_002",
    save_pack=True,
    packs_output_dir="./context_packs",
)

print(f"  OK - Gated memory lessons : {len(result['memory_lessons'])}")
print(f"  OK - FV past solutions: {len(result['fv_recall']['past_solutions'])}")
print(f"  OK - FV known mistakes: {len(result['fv_recall']['known_mistakes'])}")
print(f"  OK - FV prior research: {len(result['fv_recall']['prior_research'])}")
print(f"  OK - FV prior changes : {len(result['fv_recall']['prior_changes'])}")
print(f"  OK - Repo files extracted : {result['context_pack']['files_count']}")
print(f"  OK - Pack saved at        : {result['pack_saved_at']}")

# Step 4: Direct FV recall
print("\n[STEP 4] Direct FV recall for past work...")
history = recall_history("JWT authentication token validation")
print(f"  OK - Found {len(history)} relevant memories")
for h in history[:3]:
    print(f"      score={h.get('score', 0):.3f}: {h.get('text', '')[:80]}...")

# Step 5: Preview the prompt block
print("\n[STEP 5] Preview of full prompt block sent to agent:")
print("-" * 60)
print(result['prompt_block'][:1000])
if len(result['prompt_block']) > 1000:
    print("...")
print("-" * 60)

print("\nDEMO COMPLETE")
print("  - Gated memory: role-gated lessons with audit logging")
print("  - FV v2.2: semantic long-term memory (solutions, mistakes, research)")
print("  - Context pack: relevant repo files for task grounding")
print("  - All three combined into a single prompt block for the agent")
