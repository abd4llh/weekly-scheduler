from dataclasses import asdict
from datetime import date, time

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from ai_parser import parse_tasks_with_ai
from ai_planner import plan_week_with_ai, validate_ai_plan
from calendar_utils import events_to_ics, next_monday, render_calendar_html
from category_utils import normalize_task_categories
from constraint_completion import complete_schedule_constraints
from metrics_utils import workload_summary
from models import APP_VERSION, CATEGORIES, DAY_NAMES, PLANNING_MODES, PRIORITY_SCORE, Task
from parser_utils import minutes_to_hhmm, tasks_from_json, tasks_to_json
from routine_utils import ROUTINE_CATEGORY, place_routines_flexibly, routine_requirements_payload
from scheduler_engine import Scheduler

st.set_page_config(page_title="Weekly Scheduler", page_icon="🗓️", layout="wide")

st.markdown(
    """
<style>
.block-container {max-width: 1500px; padding-top: 1.2rem;}
.hero {padding: 18px 22px; border: 1px solid #e5e7eb; border-radius: 18px; background: #fff; margin-bottom: 14px; box-shadow: 0 1px 2px rgba(16,24,40,.04);}
.hero h1 {font-size: 32px; margin: 0 0 6px 0; letter-spacing: -.04em;}
.hero p {margin: 0; color: #6b7280; font-size: 15px;}
div[data-testid="stMetric"] {border: 1px solid #e5e7eb; border-radius: 14px; padding: 10px 13px; background: #fff; box-shadow: 0 1px 2px rgba(16,24,40,.03);}
</style>
<div class="hero">
  <h1>Weekly Scheduler</h1>
  <p>Paste your week in natural language. AI builds a realistic day around flexible routines, meals, energy, and task order.</p>
</div>
""",
    unsafe_allow_html=True,
)


def get_secret(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def reset_schedule():
    for key in ["events", "unscheduled", "issues"]:
        st.session_state.pop(key, None)


with st.sidebar:
    st.header("Plan settings")
    st.caption(APP_VERSION)
    week_start = st.date_input("Week starts on", value=next_monday(date.today()))
    planning_mode = st.selectbox("Planning mode", PLANNING_MODES, index=0)
    protect_weekend = st.checkbox("Protect weekend from heavy work", value=True)
    include_focus_guard = st.checkbox("Add Focus Guard / transition blocks", value=False)

    st.divider()
    wake_time = st.time_input("Wake time", value=time(6, 0))
    sleep_time = st.time_input("Sleep target", value=time(23, 0))

    with st.expander("Daily rhythm and meals", expanded=True):
        morning_ramp_enabled = st.checkbox("Add morning routine", value=True)
        morning_ramp_min = st.slider(
            "Morning routine duration",
            min_value=15,
            max_value=120,
            value=60,
            step=15,
            disabled=not morning_ramp_enabled,
        )
        st.caption("Placed flexibly within the first two hours after waking.")

        breakfast_enabled = st.checkbox("Add breakfast", value=False)
        if breakfast_enabled:
            b1, b2, b3 = st.columns(3)
            with b1:
                breakfast_window_start = st.time_input("Breakfast earliest", value=time(7, 0))
            with b2:
                breakfast_preferred_time = st.time_input("Breakfast preferred", value=time(8, 0))
            with b3:
                breakfast_window_end = st.time_input("Breakfast latest", value=time(10, 0))
            breakfast_duration_min = st.selectbox("Breakfast duration", [15, 30, 45, 60], index=1)
        else:
            breakfast_window_start = time(7, 0)
            breakfast_preferred_time = time(8, 0)
            breakfast_window_end = time(10, 0)
            breakfast_duration_min = 30

        lunch_enabled = st.checkbox("Add lunch", value=False)
        if lunch_enabled:
            l1, l2, l3 = st.columns(3)
            with l1:
                lunch_window_start = st.time_input("Lunch earliest", value=time(11, 0))
            with l2:
                lunch_preferred_time = st.time_input("Lunch preferred", value=time(13, 0))
            with l3:
                lunch_window_end = st.time_input("Lunch latest", value=time(14, 0))
            lunch_duration_min = st.selectbox("Lunch duration", [15, 30, 45, 60, 75], index=2)
        else:
            lunch_window_start = time(11, 0)
            lunch_preferred_time = time(13, 0)
            lunch_window_end = time(14, 0)
            lunch_duration_min = 45

        dinner_enabled = st.checkbox("Add dinner", value=False)
        if dinner_enabled:
            d1, d2, d3 = st.columns(3)
            with d1:
                dinner_window_start = st.time_input("Dinner earliest", value=time(18, 0))
            with d2:
                dinner_preferred_time = st.time_input("Dinner preferred", value=time(19, 0))
            with d3:
                dinner_window_end = st.time_input("Dinner latest", value=time(21, 0))
            dinner_duration_min = st.selectbox("Dinner duration", [30, 45, 60, 75, 90], index=2)
        else:
            dinner_window_start = time(18, 0)
            dinner_preferred_time = time(19, 0)
            dinner_window_end = time(21, 0)
            dinner_duration_min = 60

        wind_down_enabled = st.checkbox("Add evening wind-down", value=False)
        wind_down_min = st.slider(
            "Wind-down duration",
            min_value=15,
            max_value=90,
            value=30,
            step=15,
            disabled=not wind_down_enabled,
        )

        transition_min = st.select_slider(
            "Preferred transition between demanding tasks",
            options=[0, 5, 10, 15, 20, 30],
            value=15,
        )

    st.divider()
    start_hour = st.slider("Calendar start hour", 4, 10, 6)
    end_hour = st.slider("Calendar end hour", 18, 24, 23)
    px_per_hour = st.slider("Calendar row height", 48, 96, 72)

for key, value in {
    "raw_task_text": "",
    "parsed_tasks": [],
    "ai_warnings": [],
    "editor_version": 0,
    "events": [],
    "unscheduled": [],
    "issues": [],
}.items():
    if key not in st.session_state:
        st.session_state[key] = value


def df_to_tasks(df: pd.DataFrame):
    tasks = []
    for _, row in df.iterrows():
        if not str(row.get("title", "")).strip():
            continue
        kw = {name: row.get(name, field.default) for name, field in Task.__dataclass_fields__.items()}
        for name in ["duration_min", "sessions_per_week", "min_block_min", "max_block_min", "phase"]:
            try:
                kw[name] = int(kw[name])
            except Exception:
                kw[name] = int(Task.__dataclass_fields__[name].default)
        for name in ["splittable", "can_overlap", "duration_is_estimated", "needs_clarification"]:
            kw[name] = bool(kw.get(name, False))
        try:
            kw["confidence"] = float(kw.get("confidence", 0.8))
        except Exception:
            kw["confidence"] = 0.8
        tasks.append(Task(**kw))
    return normalize_task_categories(tasks)


def user_tasks_only(tasks):
    return [task for task in tasks if task.category != ROUTINE_CATEGORY]


def simple_task_dataframe(tasks):
    rows = []
    for task in user_tasks_only(tasks):
        rows.append({
            "Task": task.title,
            "Duration": f"{task.duration_min} min" if task.task_type == "Recurring" else f"{task.duration_min} min total",
            "Priority": task.priority,
            "Category": task.category,
            "Notes": task.notes,
        })
    return pd.DataFrame(rows)


def technical_task_dataframe(tasks):
    hidden = {"confidence", "duration_is_estimated", "assumptions", "needs_clarification", "clarification_question"}
    rows = []
    for task in user_tasks_only(tasks):
        row = asdict(task)
        for col in hidden:
            row.pop(col, None)
        rows.append(row)
    return pd.DataFrame(rows)


def _time_text(value):
    return value.strftime("%H:%M")


def settings_payload():
    wake_min = wake_time.hour * 60 + wake_time.minute
    sleep_min = sleep_time.hour * 60 + sleep_time.minute
    return {
        "wake_min": wake_min,
        "sleep_min": sleep_min,
        "wake_time": minutes_to_hhmm(wake_min),
        "sleep_time": minutes_to_hhmm(sleep_min),
        "planning_mode": planning_mode,
        "protect_weekend": protect_weekend,
        "include_focus_guard": include_focus_guard,
        "week_start": str(week_start),
        "timezone": "Europe/Berlin",
        "morning_ramp_enabled": morning_ramp_enabled,
        "morning_ramp_min": morning_ramp_min,
        "breakfast_enabled": breakfast_enabled,
        "breakfast_window_start": _time_text(breakfast_window_start),
        "breakfast_preferred_time": _time_text(breakfast_preferred_time),
        "breakfast_window_end": _time_text(breakfast_window_end),
        "breakfast_duration_min": breakfast_duration_min,
        "lunch_enabled": lunch_enabled,
        "lunch_window_start": _time_text(lunch_window_start),
        "lunch_preferred_time": _time_text(lunch_preferred_time),
        "lunch_window_end": _time_text(lunch_window_end),
        "lunch_duration_min": lunch_duration_min,
        "dinner_enabled": dinner_enabled,
        "dinner_window_start": _time_text(dinner_window_start),
        "dinner_preferred_time": _time_text(dinner_preferred_time),
        "dinner_window_end": _time_text(dinner_window_end),
        "dinner_duration_min": dinner_duration_min,
        "wind_down_enabled": wind_down_enabled,
        "wind_down_min": wind_down_min,
        "transition_min": transition_min,
    }


def finalize_and_validate(tasks, events, unscheduled, anchors):
    settings = settings_payload()
    tasks, events, unscheduled = complete_schedule_constraints(
        tasks,
        events,
        unscheduled,
        anchors,
        settings,
    )
    issues = validate_ai_plan(
        tasks,
        events,
        unscheduled,
        wake_time.hour * 60 + wake_time.minute,
        sleep_time.hour * 60 + sleep_time.minute,
        anchors,
        settings,
    )
    return tasks, events, unscheduled, issues


def deterministic_fallback(tasks):
    scheduler = Scheduler(
        wake_time.hour * 60 + wake_time.minute,
        sleep_time.hour * 60 + sleep_time.minute,
        protect_weekend=protect_weekend,
        planning_mode=planning_mode,
    )
    events, unscheduled = scheduler.schedule(tasks, include_focus_guard)
    tasks_with_routines, events = place_routines_flexibly(tasks, events, settings_payload())
    return finalize_and_validate(tasks_with_routines, events, unscheduled, tasks)


def generate_schedule_from_text(raw_text: str):
    api_key = get_secret("OPENAI_" + "API_KEY", "")
    model = get_secret("OPENAI_MODEL", "gpt-4.1-mini")
    if not api_key:
        st.error("AI is not configured for this deployment.")
        return
    if not raw_text.strip():
        st.error("Paste your tasks first.")
        return

    with st.spinner("Generating a realistic weekly schedule..."):
        try:
            parsed_tasks, parse_warnings = parse_tasks_with_ai(raw_text, api_key, model=model)
            parsed_tasks = normalize_task_categories(parsed_tasks)
            tasks, events, unscheduled, _, planner_warnings = plan_week_with_ai(
                raw_text,
                parsed_tasks,
                api_key,
                model,
                settings_payload(),
            )
            tasks, events, unscheduled, issues = finalize_and_validate(
                tasks,
                events,
                unscheduled,
                parsed_tasks,
            )
            st.session_state.parsed_tasks = normalize_task_categories(tasks)
            st.session_state.events = events
            st.session_state.unscheduled = unscheduled
            st.session_state.issues = issues
            st.session_state.ai_warnings = list(parse_warnings) + list(planner_warnings)
            st.session_state.editor_version += 1
        except Exception as exc:
            st.warning(f"AI planner failed. Falling back to deterministic scheduling: {exc}")
            parsed_tasks, parse_warnings = parse_tasks_with_ai(raw_text, api_key, model=model)
            parsed_tasks = normalize_task_categories(parsed_tasks)
            tasks, events, unscheduled, issues = deterministic_fallback(parsed_tasks)
            st.session_state.parsed_tasks = tasks
            st.session_state.events = events
            st.session_state.unscheduled = unscheduled
            st.session_state.issues = issues
            st.session_state.ai_warnings = parse_warnings
            st.session_state.editor_version += 1


tab_calendar, tab_tasks, tab_issues, tab_table = st.tabs(["Calendar", "Tasks", "Issues", "Table"])

with tab_tasks:
    st.subheader("Paste your week")
    raw = st.text_area(
        "Task list",
        key="raw_task_text",
        height=320,
        label_visibility="collapsed",
        placeholder="Example: i need finish report 6h, dentist thu 15:00, gym 3 times, cook every day, call family in the evening",
    )

    b1, b2 = st.columns([1.6, 1.1])
    with b1:
        if st.button("Generate schedule", type="primary", use_container_width=True):
            reset_schedule()
            generate_schedule_from_text(st.session_state.raw_task_text)
            st.rerun()
    with b2:
        uploaded = st.file_uploader("Load saved task JSON", type=["json"], label_visibility="collapsed")
        if uploaded is not None:
            try:
                st.session_state.parsed_tasks = normalize_task_categories(tasks_from_json(uploaded.read().decode("utf-8")))
                st.session_state.editor_version += 1
                reset_schedule()
                st.success("Task JSON loaded. Click Generate schedule to plan it.")
            except Exception as exc:
                st.error(f"Could not load JSON: {exc}")

    active_routines = routine_requirements_payload(settings_payload())
    if active_routines:
        with st.expander("Automatic routine windows", expanded=False):
            routine_df = pd.DataFrame(active_routines)
            st.dataframe(
                routine_df[["title", "duration_min", "window_start", "preferred_start", "window_end"]],
                use_container_width=True,
                hide_index=True,
            )
            st.caption("Preferred times are flexible. Fixed and critical events take priority; routines move elsewhere inside their allowed windows.")

    if st.session_state.ai_warnings:
        with st.expander("Items to review", expanded=True):
            for warning in st.session_state.ai_warnings:
                st.warning(str(warning))

    if st.session_state.parsed_tasks:
        st.subheader("Detected tasks")
        st.caption("Automatic routine windows are controlled in the sidebar and hidden from this task list.")
        st.dataframe(simple_task_dataframe(st.session_state.parsed_tasks), use_container_width=True, hide_index=True)

        with st.expander("Advanced review / edit detected tasks", expanded=False):
            edited = st.data_editor(
                technical_task_dataframe(st.session_state.parsed_tasks),
                num_rows="dynamic",
                use_container_width=True,
                height=420,
                key=f"task_editor_{st.session_state.editor_version}",
                column_config={
                    "priority": st.column_config.SelectboxColumn("priority", options=list(PRIORITY_SCORE.keys())),
                    "task_type": st.column_config.SelectboxColumn("task_type", options=["Fixed", "Flexible", "Recurring", "Multi-session"]),
                    "fixed_day": st.column_config.SelectboxColumn("fixed_day", options=[""] + DAY_NAMES),
                    "required_day": st.column_config.SelectboxColumn("required_day", options=[""] + DAY_NAMES),
                    "earliest_day": st.column_config.SelectboxColumn("earliest_day", options=[""] + DAY_NAMES),
                    "deadline_day": st.column_config.SelectboxColumn("deadline_day", options=[""] + DAY_NAMES),
                    "preferred_time": st.column_config.SelectboxColumn("preferred_time", options=["Morning", "Workday", "Afternoon", "Evening", "Weekend", "Any"]),
                    "energy": st.column_config.SelectboxColumn("energy", options=["High", "Medium", "Low", "Physical", "Creative"]),
                    "location": st.column_config.SelectboxColumn("location", options=["Lab", "Home", "Gym", "Any"]),
                    "category": st.column_config.SelectboxColumn("category", options=CATEGORIES),
                },
            )
            reviewed_tasks = df_to_tasks(edited)
            c1, c2 = st.columns([1.4, 1.2])
            with c1:
                if st.button("Regenerate schedule from edited tasks", use_container_width=True):
                    api_key = get_secret("OPENAI_" + "API_KEY", "")
                    model = get_secret("OPENAI_MODEL", "gpt-4.1-mini")
                    if not api_key:
                        st.error("AI is not configured for this deployment.")
                    else:
                        with st.spinner("Planning from edited tasks..."):
                            tasks, events, unscheduled, _, warnings = plan_week_with_ai(
                                st.session_state.raw_task_text,
                                reviewed_tasks,
                                api_key,
                                model,
                                settings_payload(),
                            )
                            tasks, events, unscheduled, issues = finalize_and_validate(
                                tasks,
                                events,
                                unscheduled,
                                reviewed_tasks,
                            )
                            st.session_state.parsed_tasks = normalize_task_categories(tasks)
                            st.session_state.events = events
                            st.session_state.unscheduled = unscheduled
                            st.session_state.issues = issues
                            st.session_state.ai_warnings = warnings
                            st.session_state.editor_version += 1
                        st.rerun()
            with c2:
                st.download_button(
                    "Save task JSON",
                    data=tasks_to_json(reviewed_tasks).encode("utf-8"),
                    file_name="weekly_scheduler_tasks.json",
                    mime="application/json",
                    use_container_width=True,
                )
    else:
        st.info("Paste your tasks and click Generate schedule.")

events = st.session_state.events
unscheduled = st.session_state.unscheduled
issues = st.session_state.issues
summary = workload_summary(events, unscheduled)
schedule_df = pd.DataFrame([
    {
        "Day": DAY_NAMES[event.day_index],
        "Start": minutes_to_hhmm(event.start_min),
        "End": minutes_to_hhmm(event.end_min),
        "Task": event.title,
        "Category": event.category,
        "Priority": event.priority,
        "Explanation": event.explanation,
        "Notes": event.notes,
    }
    for event in events
])

with tab_calendar:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Occupied", f"{summary['true_occupied_hours']:.1f} h")
    c2.metric("Work", f"{summary['work_hours']:.1f} h")
    c3.metric("Personal", f"{summary['personal_hours']:.1f} h")
    c4.metric("Relationship", f"{summary['relationship_hours']:.1f} h")
    c5.metric("Unscheduled", int(summary["unscheduled_count"]))
    if events:
        if issues:
            st.warning("Schedule generated, but some items need review. Check the Issues tab.")
        else:
            st.success("Schedule generated successfully.")
        components.html(render_calendar_html(events, week_start, start_hour, end_hour, px_per_hour), height=(end_hour - start_hour) * px_per_hour + 190, scrolling=True)
        st.download_button("Download Google Calendar .ics", data=events_to_ics(events, week_start).encode("utf-8"), file_name="weekly_scheduler_export.ics", mime="text/calendar")
    else:
        st.info("Paste your tasks and click Generate schedule to see the calendar.")

with tab_issues:
    st.subheader("Validation")
    if issues:
        st.warning("Some tasks could not be scheduled perfectly.")
        st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)
    else:
        st.success("No validation issues found." if events else "No schedule generated yet.")
    st.subheader("Unscheduled")
    if unscheduled:
        st.dataframe(pd.DataFrame([asdict(item) for item in unscheduled]), use_container_width=True, hide_index=True)
    else:
        st.success("Nothing unscheduled." if events else "No schedule generated yet.")

with tab_table:
    st.subheader("Schedule table")
    st.dataframe(schedule_df, use_container_width=True, hide_index=True)
