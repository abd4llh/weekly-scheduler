# v0.12 Optimizer Foundation

Development branch: `feature/v0.12-optimizer-foundation`

Draft pull request: `#1 — v0.12 optimizer foundation and Streamlit preview`

## Objective

Move exact calendar placement out of the AI planner and into an OR-Tools CP-SAT optimizer.

```text
Natural-language input
→ AI extracts profession-independent task metadata
→ CP-SAT optimizer places sessions
→ deterministic validation
→ interactive calendar and selective replanning
```

The stable `main` application remains unchanged while this branch is developed.

## Generality boundary

The optimizer does not infer behavior from task titles. It consumes normalized fields:

- duration and block sizes
- fixed times, required days, earliest starts and deadlines
- dependency IDs
- location labels
- cognitive and physical load
- task-specific recovery time
- session-distribution preference

The AI parser may interpret natural language, including specialist terminology and mixed languages. The deterministic adapter only maps structured fields. Unknown metadata receives conservative defaults instead of keyword guesses.

## Implemented

- Canonical project, task, event, plan and revision models
- OR-Tools CP-SAT weekly optimizer
- Exact duration decomposition and no-overlap rules
- Fixed events, dependencies, deadlines and weekday restrictions
- Wake/sleep boundaries and preferred time targets
- Later-only meal fallback and compact routine sequences
- Daily flexible-work ceiling, total-burden scoring and focused-work scoring
- Arbitrary location labels with default travel between different places
- Explicit task recovery time
- Explicit session distribution: any, prefer/require different days, or prefer same day
- User-configurable workload, focus, late-work, travel and routine-gap defaults
- Streamlit metadata editor and optimizer diagnostics
- Full application/parser compilation and automated tests

## Testing policy

The demonstration vocabulary must never become an optimizer rule.

- Changing task titles while preserving metadata must preserve solver behavior.
- Arbitrary location labels must work without a fixed location vocabulary.
- Session spreading or batching must come from explicit metadata.
- Random structured scenarios run in CI.
- `python tools/random_schedule_prompt.py` provides a different cross-domain prompt for manual testing.
- A failed example may become a regression test, but its profession-specific words may not be added to the adapter.

## Current hard constraints

- Fixed task times and imported busy events
- No forbidden overlaps
- Exact total duration and requested session count
- Required distinct days when explicitly requested or intrinsic to recurrence
- Wake/sleep limits, required weekdays, earliest starts and deadlines
- Dependency order
- Daily flexible-work hard maximum
- Required task recovery and inter-location travel time

## Current soft constraints

- Preferred time windows and preferred start points
- Weekend protection
- Daily workload, total burden and focused-work targets
- Late focused-work penalty
- Prefer-different-days or prefer-same-day session behavior
- Compact routine gaps

## Next milestone

1. Use rotating prompts from unrelated domains for manual stress testing.
2. Tune defaults only from patterns repeated across multiple scenarios.
3. Start the interactive calendar with drag, resize and lock support.
4. Add selective replanning that preserves completed, fixed and user-locked events.
