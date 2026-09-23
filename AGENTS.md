# Hackathon agent protocol

This repository uses a single shared worktree with one orchestrator and several Codex workers.

## Coordination

- The primary chat/session is the orchestrator. It owns priorities, task assignment, integration,
  and final decisions.
- Read `.hackflow/BRIEF.md` before beginning work and `.hackflow/BOARD.md` before choosing a task.
- Never silently broaden the product scope. Record assumptions in your role report.
- Do not overwrite another agent's uncommitted work. Inspect `git status` before editing and keep
  changes narrowly scoped to the task assigned by the orchestrator.
- Do not create commits, switch branches, reset, clean, or revert files unless the orchestrator
  explicitly asks you to do so.
- Put durable findings and handoffs in `.hackflow/reports/<role>.md`. Reports are coordination
  artifacts, not product code.
- If blocked, write the blocker, evidence, and the smallest decision needed from the orchestrator
  to your role report.

## Completion standard

A worker is not done after producing code. It must report changed files, checks run, failures,
remaining risks, and a recommended next action. The orchestrator verifies and integrates the work.
