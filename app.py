from dataclasses import asdict
from datetime import date, time

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from ai_parser import parse_tasks_with_ai
from calendar_utils import events_to_ics, next_monday, render_calendar_html
from category_utils import normalize_task_categories
from metrics_utils import by_day_dataframe, workload_summary
from models import APP_VERSION, CATEGORIES, DAY_NAMES, PLANNING_MODES, PRIORITY_SCORE, Task
from parser_utils import adapt_tasks_for_mood, minutes_to_hhmm, tasks_from_json, tasks_to_json, validate_tasks
from plan_health import evaluate_plan_health
from scheduler_engine import Scheduler

st.set_page_config(page_title="Weekly Scheduler", page_icon="🗓️", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
<style>
.main > div {padding-top: 1.1rem;}
.block-container {max-width: 1500px;}
.clean-hero {padding: 18px 22px; border: 1px solid #e5e7eb; border-radius: 18px; background: #ffffff; margin-bottom: 14px; box-shadow: 0 1px 2px rgba(16,24,40,.04);}
.hero-title {font-size: 30px; font-weight: 760; letter-spacing: -.035em; color: #111827; margin-bottom: 4px;}
.hero-sub {color: #6b7280; font-size: 14px;}
div[data-testid="stMetric"] {border: 1px solid #e5e7eb; border-radius: 14px; padding: 10px 13px; background: #fff; box-shadow: 0 1px 2px rgba(16,24,40,.03);}
</style>
<div class="clean-hero">
  <div class="hero-title">Weekly Scheduler</div>
  <div class="hero-sub">AI-first task parsing, planning modes, editable assumptions, and realistic workload signals.</div>
</div>
""",
    unsafe_allow_html=True,
)


def get_secret(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def clear_generated_schedule():
    for key in ["events", "unscheduled", "issues"]:
        st.session_state.pop(key, None)


def user_defaults_from_sidebar():
    with st.sidebar.expander("Default assumptions", expanded=False):
        focused_work = st.number_input("Focused work block", min_value=15, max_value=240, value=90, step=15)
        lab_block = st.number_input("Lab / experiment block", min_value=30, max_value=300, value=120, step=15)
        writing_block = st.number_input("Writing block", min_value=30, max_value=240, value=90, step=15)
        admin_block = st.number_input("Admin micro-task", min_value=5, max_value=60, value=20, step=5)
        exercise_duration = st.number_input("Exercise session", min_value=15, max_value=240, value=90, step=15)
        cooking_duration = st.number_input("Cooking / meal prep", min_value=15, max_value=240, value=60, step=15)
        relationship_duration = st.number_input("Relationship / social call", min_value=15, max_value=240, value=60, step=15)
    return {
        "focused_work_block_min": int(focused_work),
        "lab_experiment_block_min": int(lab_block),
        "writing_block_min": int(writing_block),
        "admin_micro_task_min": int(admin_block),
        "exercise_session_min": int(exercise_duration),
        "cooking_or_meal_prep_min": int(cooking_duration),
        "relationship_or_social_call_min": int(relationship_duration),
    }


with st.sidebar:
    st.header("Plan settings")
    st.caption(APP_VERSION)
    week_start = st.date_input("Week starts on", value=next_monday(date.today()))
    planning_mode = st.selectbox("Planning mode", PLANNING_MODES, index=0)
    mood = st.selectbox(
        "Mood / energy mode",
        ["Normal", "Productive", "Creative", "Tired", "Physically energetic", "Low motivation"],
    )
    protect_weekend = st.checkbox("Protect weekend from heavy work", value=True)
    include_focus_guard = st.checkbox("Add Focus Guard / transition blocks", value=False)
    st.divider()
    defaults = user_defaults_from_sidebar()
    st.divider()
    st.header("Day settings")
    wake_time = st.time_input("Wake time", value=time(6, 0))
    sleep_time = st.time_input("Sleep target", value=time(23, 0))
    st.divider()
    st.header("Calendar display")
    start_hour = st.slider("Start hour", 4, 10, 6)
    end_hour = st.slider("End hour", 18, 24, 23)
    px_per_hour = st.slider("Row height", 48, 96, 72)

if "raw_task_text" not in st.session_state:
    st.session_state.raw_task_text = ""
if "last_input_text" not in st.session_state:
    st.session_state.last_input_text = ""
if "editor_version" not in st.session_state:
    st.session_state.editor_version = 0
if "parsed_tasks" not in st.session_state:
    st.session_state.parsed_tasks = []
if "last_mood" not in st.session_state:
    st.session_state.last_mood = mood
if "ai_warnings" not in st.session_state:
    st.session_state.ai_warnings = []

if mood != st.session_state.last_mood and st.session_state.parsed_tasks:
    st.session_state.parsed_tasks = adapt_tasks_for_mood(st.session_state.parsed_tasks, mood)
    st.session_state.last_mood = mood
    st.session_state.editor_version += 1
    clear_generated_schedule()
else:
    st.session_state.last_mood = mood


def df_to_tasks(df: pd.DataFrame):
    out = []
    for _, row in df.iterrows():
        if not str(row.get("title", "")).strip():
            continue
        kw = {field_name: row.get(field_name, field_def.default) for field_name, field_def in Task.__dataclass_fields__.items()}
        for key in ["duration_min", "sessions_per_week", "min_block_min", "max_block_min"]:
            try:
                kw[key] = int(kw[key])
            except Exception:
                kw[key] = int(Task.__dataclass_fields__[key].default)
        try:
            kw["confidence"] = float(kw.get("confidence", 0.8))
        except Exception:
            kw["confidence"] = 0.8
        for key in ["splittable", "can_overlap", "duration_is_estimated", "needs_clarification"]:
            kw[key] = bool(kw.get(key, False))
        out.append(Task(**kw))
    return normalize_task_categories(out)


def run_schedule(tasks):
    rows = normalize_task_categories(tasks)
    scheduler = Scheduler(
        wake_time.hour * 60 + wake_time.minute,
        sleep_time.hour * 60 + sleep_time.minute,
        protect_weekend=protect_weekend,
        planning_mode=planning_mode,
    )
    st.session_state.events, st.session_state.unscheduled = scheduler.schedule(rows, include_focus_guard)
    st.session_state.issues = validate_tasks(rows, wake_time.hour * 60 + wake_time.minute, sleep_time.hour * 60 + sleep_time.minute)


def ordered_day_df(events):
    df = by_day_dataframe(events)
    df["Day"] = pd.Categorical(df["Day"], categories=DAY_NAMES, ordered=True)
    return df.sort_values("Day")


def render_workload_charts(day_df):
    st.markdown("#### True occupied hours")
    st.caption("Actual clock time blocked per day after merging overlapping events.")
    occupied_chart = (
        alt.Chart(day_df)
        .mark_bar()
        .encode(
            x=alt.X("Day:N", sort=DAY_NAMES, title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("True occupied h:Q", title="Hours"),
            tooltip=["Day", "True occupied h"],
        )
        .properties(height=260)
    )
    st.altair_chart(occupied_chart, use_container_width=True)

    st.markdown("#### Category breakdown")
    st.caption("Scheduled category time. This may be higher than true occupied hours when overlap is allowed.")
    long_df = day_df.melt(
        id_vars="Day",
        value_vars=[col for col in ["Work h", "Personal h", "Relationship h", "Other h"] if col in day_df.columns],
        var_name="Category",
        value_name="Hours",
    )
    long_df = long_df[long_df["Hours"] > 0]
    category_chart = (
        alt.Chart(long_df)
        .mark_bar()
        .encode(
            x=alt.X("Day:N", sort=DAY_NAMES, title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Hours:Q", title="Scheduled category hours"),
            color=alt.Color("Category:N", title="Category"),
            tooltip=["Day", "Category", "Hours"],
        )
        .properties(height=280)
    )
    st.altair_chart(category_chart, use_container_width=True)


def render_plan_health(summary, planning_mode):
    level, messages, recommendations = evaluate_plan_health(summary, planning_mode)
    text = "  \n".join([f"• {message}" for message in messages])
    if recommendations:
        text += "\n\nRecommended actions:  \n" + "  \n".join([f"• {recommendation}" for recommendation in recommendations])
    if level == "error":
        st.error(text)
    elif level == "warning":
        st.warning(text)
    elif level == "info":
        st.info(text)
    else:
        st.success(text)


def assumptions_dataframe(tasks):
    rows = []
    for task in tasks:
        rows.append(
            {
                "Task": task.title,
                "Confidence": round(float(getattr(task, "confidence", 0.8)), 2),
                "Duration estimated": bool(getattr(task, "duration_is_estimated", True)),
                "Assumptions": getattr(task, "assumptions", ""),
                "Needs clarification": bool(getattr(task, "needs_clarification", False)),
                "Clarification question": getattr(task, "clarification_question", ""),
            }
        )
    return pd.DataFrame(rows)


tab_calendar, tab_tasks, tab_issues, tab_table = st.tabs(["Calendar", "Tasks", "Issues", "Table"])

with tab_tasks:
    st.subheader("Task input")
    st.caption("Paste tasks as bullet points, notes, or a paragraph. The app uses AI to extract a structured task table.")
    raw = st.text_area(
        "Task list",
        height=320,
        key="raw_task_text",
        label_visibility="collapsed",
        placeholder="Example: This week I need to finish a report, go to the dentist on Thursday at 15:00, exercise three times, cook every day, and call my family in the evenings.",
    )

    if raw != st.session_state.last_input_text:
        st.session_state.last_input_text = raw
        clear_generated_schedule()

    c1, c2 = st.columns([1.2, 5])
    with c1:
        if st.button("Parse tasks with AI", type="primary", use_container_width=True):
            api_key = get_secret("OPENAI_API_KEY", "")
            ai_model = get_secret("OPENAI_MODEL", "gpt-4.1-mini")
            if not api_key:
                st.error("AI parsing is not configured for this deployment.")
            elif not st.session_state.raw_task_text.strip():
                st.error("Paste some tasks first.")
            else:
                with st.spinner("AI is parsing your task list..."):
                    try:
                        ai_tasks, ai_warnings = parse_tasks_with_ai(st.session_state.raw_task_text, api_key, model=ai_model, user_defaults=defaults)
                        st.session_state.parsed_tasks = normalize_task_categories(ai_tasks)
                        st.session_state.ai_warnings = ai_warnings
                        st.session_state.editor_version += 1
                        clear_generated_schedule()
                        st.success("AI parsing complete. Review the table below.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"AI parsing failed: {exc}")
    with c2:
        uploaded = st.file_uploader("Load saved task JSON", type=["json"], label_visibility="collapsed")
        if uploaded is not None:
            try:
                loaded = normalize_task_categories(tasks_from_json(uploaded.read().decode("utf-8")))
                st.session_state.parsed_tasks = loaded
                st.session_state.editor_version += 1
                clear_generated_schedule()
                st.rerun()
            except Exception as exc:
                st.error(f"Could not load JSON: {exc}")

    if st.session_state.ai_warnings:
        with st.expander("AI parser warnings", expanded=True):
            for warning in st.session_state.ai_warnings:
                st.warning(str(warning))

    st.subheader("Review tasks")
    if not st.session_state.parsed_tasks:
        st.info("No parsed tasks yet. Paste tasks above and click Parse tasks with AI.")
        edited_df = pd.DataFrame(columns=[field for field in Task.__dataclass_fields__])
    else:
        edited_df = st.data_editor(
            pd.DataFrame([asdict(task) for task in st.session_state.parsed_tasks]),
            num_rows="dynamic",
            use_container_width=True,
            height=430,
            key=f"task_editor_{st.session_state.editor_version}",
            column_config={
                "priority": st.column_config.SelectboxColumn("priority", options=list(PRIORITY_SCORE.keys())),
                "task_type": st.column_config.SelectboxColumn("task_type", options=["Fixed", "Flexible", "Recurring", "Multi-session"]),
                "fixed_day": st.column_config.SelectboxColumn("fixed_day", options=[""] + DAY_NAMES),
                "preferred_time": st.column_config.SelectboxColumn("preferred_time", options=["Morning", "Workday", "Afternoon", "Evening", "Weekend", "Any"]),
                "energy": st.column_config.SelectboxColumn("energy", options=["High", "Medium", "Low", "Physical", "Creative"]),
                "location": st.column_config.SelectboxColumn("location", options=["Lab", "Home", "Gym", "Any"]),
                "category": st.column_config.SelectboxColumn("category", options=CATEGORIES),
            },
        )

    tasks = df_to_tasks(edited_df)

    if tasks:
        with st.expander("AI assumptions", expanded=True):
            st.dataframe(assumptions_dataframe(tasks), use_container_width=True, hide_index=True)

    b1, b2 = st.columns([1.4, 1.2])
    with b1:
        if st.button("Generate schedule", type="primary", use_container_width=True, disabled=not bool(tasks)):
            st.session_state.parsed_tasks = tasks
            run_schedule(tasks)
            st.success("Schedule updated. Open the Calendar tab.")
    with b2:
        st.download_button(
            "Save task JSON",
            data=tasks_to_json(tasks).encode("utf-8"),
            file_name="weekly_scheduler_tasks.json",
            mime="application/json",
            use_container_width=True,
            disabled=not bool(tasks),
        )

if "events" not in st.session_state:
    if st.session_state.parsed_tasks:
        run_schedule(st.session_state.parsed_tasks)
    else:
        st.session_state.events, st.session_state.unscheduled, st.session_state.issues = [], [], []

events = st.session_state.events
unscheduled = st.session_state.get("unscheduled", [])
issues = st.session_state.get("issues", [])
summary = workload_summary(events, unscheduled)
schedule_df = pd.DataFrame(
    [
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
    ]
)

with tab_calendar:
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("True occupied", f"{summary['true_occupied_hours']:.1f} h")
    m2.metric("Work", f"{summary['work_hours']:.1f} h")
    m3.metric("Personal", f"{summary['personal_hours']:.1f} h")
    m4.metric("Relationship", f"{summary['relationship_hours']:.1f} h")
    m5.metric("Unscheduled", int(summary["unscheduled_count"]))
    extra = f" · other: {summary['other_hours']:.1f} h" if summary.get("other_hours", 0) else ""
    st.caption(f"Scheduled time: {summary['scheduled_hours']:.1f} h · overlap: {summary['overlap_hours']:.1f} h · weekend: {summary['weekend_hours']:.1f} h{extra} · mode: {planning_mode}")
    if events:
        render_plan_health(summary, planning_mode)
        components.html(render_calendar_html(events, week_start, start_hour, end_hour, px_per_hour), height=(end_hour - start_hour) * px_per_hour + 190, scrolling=True)
        st.download_button("Download Google Calendar .ics", data=events_to_ics(events, week_start).encode("utf-8"), file_name="weekly_scheduler_export.ics", mime="text/calendar")
    else:
        st.info("Parse tasks with AI and generate a schedule to see the calendar.")

with tab_issues:
    st.subheader("Validation")
    if not issues:
        st.success("No validation issues found.")
    else:
        for issue in issues:
            msg = f"**{issue['task']}** — {issue['message']}"
            if issue["level"] == "error":
                st.error(msg)
            elif issue["level"] == "warning":
                st.warning(msg)
            else:
                st.info(msg)
        st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)

    st.subheader("Unscheduled or partially scheduled")
    if not unscheduled:
        st.success("Everything was scheduled." if events else "No schedule generated yet.")
    else:
        st.dataframe(pd.DataFrame([asdict(item) for item in unscheduled]), use_container_width=True, hide_index=True)

with tab_table:
    st.subheader("Schedule table")
    st.dataframe(schedule_df, use_container_width=True, hide_index=True)
    st.subheader("Workload by day")
    day_df = ordered_day_df(events)
    st.dataframe(day_df, use_container_width=True, hide_index=True)
    render_workload_charts(day_df)
