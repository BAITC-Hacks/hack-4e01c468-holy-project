# holy-project hackathon flow

The repository includes a small Codex swarm for hackathon work. The current/main session acts as
the orchestrator; three separate terminal tabs run focused workers:

| Worker | Responsibility | Product-code access |
|---|---|---|
| `product` | Problem framing, research, MVP backlog, risk reduction | Read-only by protocol |
| `builder` | One explicitly assigned vertical slice | May edit |
| `qa` | Acceptance checks, review, demo-path validation | Read-only by protocol |

All workers use `gpt-5.6-luna` with `xhigh` reasoning by default. They share the current worktree,
so only the builder edits product code; coordination happens through `.hackflow/BOARD.md` and role
reports under `.hackflow/reports/`.

## Start

1. Fill in `.hackflow/BRIEF.md` with the challenge, constraints, judging criteria, and demo goal.
2. Validate the environment:

   ```bash
   scripts/hackflow doctor
   ```

3. Start the worker tabs:

   ```bash
   scripts/hackflow start
   ```

4. Stay in the main Codex session as orchestrator. Assign one concrete task at a time by editing
   `.hackflow/BOARD.md`, then tell the relevant worker to pick it up.
5. Inspect the shared state at any time:

   ```bash
   scripts/hackflow status
   ```

## Orchestrator loop

Use this loop throughout the hackathon:

1. **Frame:** keep the brief and demo definition of done current.
2. **Assign:** give every active worker one bounded task with an acceptance check.
3. **Observe:** read reports and `git diff`; resolve blockers and ownership conflicts.
4. **Verify:** have QA reproduce the critical path before marking a task done.
5. **Integrate:** accept, revise, or reject the slice; update the board and choose the next bottleneck.

The launcher uses normal Codex approvals and the `workspace-write` sandbox. It intentionally does
not bypass safety prompts, commit code, switch branches, or kill terminal sessions.

To test another model or reasoning level temporarily:

```bash
HACKFLOW_MODEL=gpt-5.6-luna HACKFLOW_REASONING=xhigh scripts/hackflow start
```
