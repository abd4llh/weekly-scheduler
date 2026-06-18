# v0.12 Optimizer Foundation

Development branch: `feature/v0.12-optimizer-foundation`

Draft pull request: `#1 — v0.12 optimizer foundation and Streamlit preview`

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

## Implemented

- Canonical domain models for projects, tasks, events, plans and revisions
- OR-Tools CP-SAT single-week optimizer
- Streamlit planning-engine selector and optimizer preview
- Exact task-duration decomposition into sessions
- Fixed events and fixed tasks
- No-overlap constraints
- Task dependencies
- Required weekdays
- Earliest-start and deadline limits
- Wake/sleep boundaries
- Preferred time-window targets and fallback penalties
- Recurring sessions on distinct days
- Quantity-aware multi-session deliverables
- Weekend-protection penalty
- Daily flexible-work target and hard maximum
- Stronger daily workload balancing
- Soft distribution of multi-session deliverables across days
- Location inference and sequence-dependent travel buffers
- Transition buffers around fixed commitments
- Compact morning routine sequencing
- Adapter from the current `models.Task` / `models.Event` objects
- Optimizer diagnostics in the Streamlit calendar
- Automated tests and branch-specific GitHub Actions workflow

## Current hard constraints

- Fixed task times
- Imported busy events
- No forbidden overlaps
- Exact total duration
- Exact requested recurring-session count
- Recurring sessions on distinct days
- Wake and sleep limits
- Required weekdays
- Earliest start
- Deadline
- Task dependency order
- Daily flexible-work hard maximum
- Required transition/travel time between applicable events

## Current soft constraints

- Preferred time windows and preferred start points
- Later-first meal fallback
- Weekend avoidance when enabled
- Earlier placement when no preference exists
- Preferred daily workload target
- Balanced daily workload
- Multi-session distribution across days
- Compact gaps between morning routine, breakfast and morning practice

## Default schedule-quality values

- Preferred flexible workload: 8 hours per day
- Hard flexible-work maximum: 10 hours per day
- Default travel time between different known locations: 20 minutes
- Compact morning-sequence gap: no more than 30 minutes preferred

Fixed commitments and automatic routines are excluded from the flexible-work ceiling.

## Not implemented yet

- Optional task omission with priority penalties
- Locked manually moved optimizer events
- Partial replanning scopes
- Multi-week project allocation
- Interactive FullCalendar component
- Persistence and revision history
- Alternative schedule generation

## Next milestone

1. Stress-test the schedule-quality model on painter, laboratory, student and household scenarios.
2. Tune objective weights and defaults from those results.
3. Start the interactive calendar component with drag, resize and lock support.
4. Add selective replanning that preserves completed, fixed and user-locked events.
