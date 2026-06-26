# virtual-employees
Memory &amp; grounding layer for a multi-agent virtual software team — gated lesson storage, repo context extraction, and orchestrator interface.

This repository contains the memory and grounding infrastructure 
for an internal virtual software employee system built on top of 
Microsoft Agent Framework, OpenHands, and LLM-powered agents.

It provides two core components:

- A gated memory system that stores validated lessons learned 
  across agent runs, with strict role-based write access 
  (supervisor and QA only), full audit logging, and rollback support.

- A repo context pack pipeline that automatically extracts the most 
  relevant files from a codebase given a task prompt, producing a 
  structured evidence pack injected into agent prompts before execution.

Both components are exposed through a single orchestrator interface 
designed to plug directly into the agent workflow pipeline.
