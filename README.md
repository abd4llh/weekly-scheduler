# Weekly Scheduler MVP — Calendar View

This version adds a Google Calendar-style weekly layout.

## What is new

- Calendar tab with days as columns and time down the left side.
- Event blocks positioned by start/end time.
- Priority colors and workload metrics.
- Display controls for start hour, end hour, and row height.
- Keeps task parsing, editable task table, mood mode, Focus Guard, and `.ics` export.

## Run

### Windows PowerShell

```powershell
cd weekly_scheduler_mvp_calendar_view
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

### macOS/Linux

```bash
cd weekly_scheduler_mvp_calendar_view
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL Streamlit prints, usually `http://localhost:8501`.

## Note

This is still a rule-based MVP. The AI parser and adaptive learning layer can be added next.

## Bug fix in this version

The task text area, editable task table, and calendar are now synchronized.

- When you add a new line to the task list, the app automatically reparses it.
- The editable table refreshes immediately.
- The calendar updates when you click **Generate / update calendar**.
- The data editor uses a versioned widget key, so it no longer keeps stale rows after parsing.


## Patch notes: fixed-time parsing

This version fixes a bug where a task like:

```text
go to the doctor at sunday 14:00
```

was detected as Sunday, but the time `14:00` was ignored. The scheduler then used its default fixed-event time of 10:00.

The parser now supports:

- `Sunday 14:00`
- `at Sunday 14:00`
- `Sunday at 14:00`
- `Sunday 2 pm`
- `on Monday at 09:30`

Focus Guard is now disabled by default, and Focus Guard reminder blocks are excluded from the top workload metrics.


## Phase 1 additions

- Unscheduled-task tracking instead of silent failure.
- Fixed-event conflict detection.
- Validation warnings for missing/invalid fixed times, bad durations, conflicting events, and long tasks.
- Better fixed-time parsing, including `Sunday 14:00`, `Sunday at 2 pm`, and time ranges like `14:00-15:30`.
- Scheduling explanations in the table, calendar tooltip, and `.ics` descriptions.
- Save/load task lists as JSON.

Recommended quick tests:

```text
go to the doctor at sunday 14:00
```

```text
go to the doctor at sunday 14:00
meet Ahmad Sunday at 14:30
```

The second test should show a fixed-event conflict unless one task is marked as overlap-compatible.
