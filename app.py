from dataclasses import asdict
from datetime import date, time

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from ai_parser import parse_tasks_with_ai
from ai_planner import plan_week_with_ai
from calendar_utils import events_to_ics, next_monday, render_calendar_html
from category_utils import normalize_task_categories
from metrics_utils import workload_summary
from models import APP_VERSION, CATEGORIES, DAY_NAMES, PLANNING_MODES, PRIORITY_SCORE, Task
from parser_utils import minutes_to_hhmm, tasks_from_json, tasks_to_json
from scheduler_engine import Scheduler

st.set_page_config(page_title="Weekly Scheduler", page_icon="🗓️", layout="wide")
st.title("Weekly Scheduler")
st.caption(f"{APP_VERSION} · AI planner handles messy grammar, typos, and mixed-language input; Python validates the final calendar.")


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
    week_start = st.date_input("Week starts on", value=next_monday(date.today()))
    planning_mode = st.selectbox("Planning mode", PLANNING_MODES, index=0)
    protect_weekend = st.checkbox("Protect weekend from heavy work", value=True)
    include_focus_guard = st.checkbox("Add Focus Guard / transition blocks", value=False)
    wake_time = st.time_input("Wake time", value=time(6, 0))
    sleep_time = st.time_input("Sleep target", value=time(23, 0))
    start_hour = st.slider("Calendar start hour", 4, 10, 6)
    end_hour = st.slider("Calendar end hour", 18, 24, 23)
    px_per_hour = st.slider("Calendar row height", 48, 96, 72)

for key, value in {
    "raw_task_text": "",
    "parsed_tasks": [],
    "ai_warnings": [],
    "editor_version": 0,
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


def visible_task_dataframe(tasks):
    hidden = {"confidence", "duration_is_estimated", "assumptions", "needs_clarification", "clarification_question"}
    rows = []
    for task in tasks:
        row = asdict(task)
        for col in hidden:
            row.pop(col, None)
        rows.append(row)
    return pd.DataFrame(rows)


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
    }


def deterministic_fallback(tasks):
    scheduler = Scheduler(
        wake_time.hour * 60 + wake_time.minute,
        sleep_time.hour * 60 + sleep_time.minute,
        protect_weekend=protect_weekend,
        planning_mode=planning_mode,
    )
    events, unscheduled = scheduler.schedule(tasks, include_focus_guard)
    return events, unscheduled, []


tab_calendar, tab_tasks, tab_issues, tab_table = st.tabs(["Calendar", "Tasks", "Issues", "Table"])

with tab_tasks:
    st.subheader("Task input")
    st.caption("Use natural text. Typos, imperfect grammar, Arabic/English mixing, and informal phrasing are okay.")
    raw = st.text_area(
        "Task list",
        key="raw_task_text",
        height=300,
        label_visibility="collapsed",
        placeholder="Example: i need finish report 6h, dentist thu 15:00, gym 3 times, cook every day, اتصل بأهلي بالمساء",
    )

    c1, c2, c3 = st.columns([1.2, 1.4, 4])
    with c1:
        if st.button("Parse tasks", use_container_width=True):
            api_key = get_secret("OPENAI_" + "API_KEY", "")
            model = get_secret("OPENAI_MODEL", "gpt-4.1-mini")
            if not api_key:
                st.error("AI is not configured for this deployment.")
            elif not raw.strip():
                st.error("Paste tasks first.")
            else:
                with st.spinner("AI is understanding the task text..."):
                    tasks, warnings = parse_tasks_with_ai(raw, api_key, model=model)
                st.session_state.parsed_tasks = normalize_task_categories(tasks)
                st.session_state.ai_warnings = warnings
                st.session_state.editor_version += 1
                reset_schedule()
                st.rerun()
    with c2:
        uploaded = st.file_uploader("Load JSON", type=["json"], label_visibility="collapsed")
        if uploaded is not None:
            try:
                st.session_state.parsed_tasks = normalize_task_categories(tasks_from_json(uploaded.read().decode("utf-8")))
                st.session_state.editor_version += 1
                reset_schedule()
                st.rerun()
            except Exception as exc:
                st.error(f"Could not load JSON: {exc}")

    if st.session_state.ai_warnings:
        with st.expander("Items to review", expanded=True):
            for warning in st.session_state.ai_warnings:
                st.warning(str(warning))

    if st.session_state.parsed_tasks:
        edited = st.data_editor(
            visible_task_dataframe(st.session_state.parsed_tasks),
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
    else:
        st.info("Paste text and click Parse tasks first.")
        reviewed_tasks = []

    b1, b2 = st.columns([1.4, 1.2])
    with b1:
        if st.button("Generate AI schedule", type="primary", use_container_width=True, disabled=not reviewed_tasks):
            api_key = get_secret("OPENAI_" + "API_KEY", "")
            model = get_secret("OPENAI_MODEL", "gpt-4.1-mini")
            if not api_key:
                st.error("AI is not configured for this deployment.")
            else:
                with st.spinner("AI is planning the week and Python is validating it..."):
                    try:
                        tasks, events, unscheduled, issues, warnings = plan_week_with_ai(raw, reviewed_tasks, api_key, model, settings_payload())
                        st.session_state.parsed_tasks = normalize_task_categories(tasks)
                        st.session_state.events = events
                        st.session_state.unscheduled = unscheduled
                        st.session_state.issues = issues
                        st.session_state.ai_warnings = warnings
                        st.session_state.editor_version += 1
                    except Exception as exc:
                        st.warning(f"AI planner failed. Falling back to deterministic scheduler: {exc}")
                        events, unscheduled, issues = deterministic_fallback(reviewed_tasks)
                        st.session_state.events = events
                        st.session_state.unscheduled = unscheduled
                        st.session_state.issues = issues
                st.rerun()
    with b2:
        st.download_button(
            "Save task JSON",
            data=tasks_to_json(reviewed_tasks).encode("utf-8"),
            file_name="weekly_scheduler_tasks.json",
            mime="application/json",
            use_container_width=True,
            disabled=not reviewed_tasks,
        )

if "events" not in st.session_state:
    st.session_state.events, st.session_state.unscheduled, st.session_state.issues = [], [], []

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
        components.html(render_calendar_html(events, week_start, start_hour, end_hour, px_per_hour), height=(end_hour - start_hour) * px_per_hour + 190, scrolling=True)
        st.download_button("Download Google Calendar .ics", data=events_to_ics(events, week_start).encode("utf-8"), file_name="weekly_scheduler_export.ics", mime="text/calendar")
    else:
        st.info("Parse tasks and generate an AI schedule to see the calendar.")

with tab_issues:
    st.subheader("Validation")
    if issues:
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
