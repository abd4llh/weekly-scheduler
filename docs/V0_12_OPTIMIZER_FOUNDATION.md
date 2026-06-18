# v0.12 Optimizer Foundation

Development branch: `feature/v0.12-optimizer-foundation`

Draft pull request: `#1 — v0.12 optimizer foundation`

## Objective

Move exact calendar placement out of the AI planner and into an OR-Tools CP-SAT optimization engine.

The target pipeline is:

```text
Natural-language input
→ AI extracts canonical projects, tasks and constraints
→ CP-SAT optimizer places sessions
→ deterministic validation
→ interactive calendar and selective replanning
```

The existing `main` branch remains the stable legacy application while this branch is developed.

## Implemented in the first foundation milestone

- Canonical domain models for projects, tasks, events, plans and revisions
- OR-Tools dependency
- Single-week CP-SAT optimizer
- Exact task-duration decomposition into sessions
- Fixed events and fixed tasks
- No-overlap constraints
- Task dependencies
- Required weekdays
- Earliest-start and deadline limits
- Wake/sleep boundaries
- Preferred time-window penalties
- Weekend-protection penalty
- Daily-load balancing objective
- Adapter from the current `models.Task` / `models.Event` objects
- Unit tests and branch-specific GitHub Actions workflow
- Passing optimizer and legacy-adapter test suite

## Hard constraints in the current solver

- Fixed task times
- Imported busy events
- No forbidden overlaps
- Exact total duration
- Exact requested session count
- Wake and sleep limits
- Required weekdays
- Earliest start
- Deadline
- Task dependency order

## Soft constraints in the current solver

- Preferred time windows
- Weekend avoidance when enabled
- Earlier placement
- Balanced daily workload

## Not implemented yet

- Optional task omission with priority penalties
- Flexible meal/routine preference objects in the canonical model
- Transition-time constraints between locations or demanding tasks
- Locked manually moved optimizer events
- Partial replanning scopes
- Multi-week project allocation
- Interactive FullCalendar component
- Persistence and revision history
- Alternative schedule generation

## Next implementation milestone

1. Add an experimental optimizer mode to the Streamlit application on this branch.
2. Compare the optimizer against the existing painter and personal-workload test prompts.
3. Add explicit soft-routine windows and schedule-change penalties.
4. Begin the interactive calendar component only after optimizer output is stable.
