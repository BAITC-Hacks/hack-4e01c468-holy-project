# Hackathon brief

Source: `docs/superpowers/specs/2026-09-23-wind-power-agent-design.md`.
Approved execution plan: `docs/superpowers/plans/2026-09-23-wind-power-agent.md`.

## Goal

Build a reproducible 24/48-hour forecast for two wind turbines with point-in-time weather,
CatBoost/baselines, auditable bounded agent, CLI and polished responsive dashboard.

## Judging criteria

- Working behavior 25, technical implementation 25, reproducibility 25, value 15, originality 10.

## Constraints

- Four-hour target; CPU only; same shared worktree; no commits/branch switches.
- Luna xhigh coding workers, primary orchestrator reviews every result.
- Read applicable local skills (`skills/backend.md`, `skills/frontend.md`) before implementation.
- No secrets in logs/artifacts. Historical weather must prove availability before origin.
- February turbine truth unavailable: do not fabricate metrics. Unverified weather is demo-only.

## Demo definition of done

- CLI produces both turbines' full horizon and six auditable artifacts; offline fallback works.
- Dashboard has custom typography/layout/charts and browser-verified responsive states.
- January backtest compares three baselines and CatBoost; historical provenance gate reported honestly.
