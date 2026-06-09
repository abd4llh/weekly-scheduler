from dataclasses import asdict
from datetime import date, time

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from calendar_utils import events_to_ics, next_monday, render_calendar_html
from category_utils import normalize_task_categories
from metrics_utils import by_day_dataframe, workload_summary
from models import APP_VERSION, CATEGORIES, DAY_NAMES, PLANNING_MODES, PRIORITY_SCORE, Task
from parser_utils import (
    DEFAULT_TASKS,
    adapt_tasks_for_mood,
    minutes_to_hhmm,
    parse_tasks,
    tasks_from_json,
    tasks_to_json,
    validate_tasks,
)
from plan_health import evaluate_plan_health
from scheduler_engine import Scheduler

try:
    from ai_parser import parse_tasks_with_ai
except Exception:
    parse_tasks_with_ai = None


st.set_page_config(
    page_title="Weekly Scheduler",
    page_icon="🗓️",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown(
    """
<style>
.main > div {padding-top: 1.1rem;}
.block-container {max-width: 1500px;}
.clean-hero {
    padding: 18px 22px;
    border: 1px solid #e5e7eb;
    border-radius: 18px;
    background: #ffffff;
    margin-bottom: 14px;
    box-shadow: 0 1px 2px rgba(16,24,40,.04);
}
.hero-title {
    font-size: 30px;
    font-weight: 760;
    letter-spacing: -.035em;
    color: #111827;
    margin-bottom: 4px;
}
.hero-sub {
    color: #6b7280;
    font-size: 14px;
}
div[data-testid="stMetric"] {
    border: 1px solid #e5e7eb;
    border-radius: 14px;
    padding: 10px 13px;
    background: #fff;
    box-shadow: 0 1px 2px rgba(16,24,40,.03);
}
</style>

<div class="clean-hero">
  <div class="hero-title">Weekly Scheduler</div>
  <div class="hero-sub">
    AI-assisted task parsing, planning modes, category-aware metrics, and realistic workload signals.
  </div>
</div>
""",
    unsafe_allow_html=True,
)


with st.sidebar:
    st.header("Plan settings")
    st.caption(APP_VERSION)

    week_start = st.date_input("Week starts on", value=next_monday(date.today()))

    planning_mode = st.selectbox(
        "Planning mode",
        PLANNING_MODES,
        index=0,
    )

    mood = st.selectbox(
        "Mood / energy mode",
        [
            "Normal",
            "Productive",
            "Creative",
            "Tired",
            "Physically energetic",
            "Low motivation",
        ],
    )

    protect_weekend = st.checkbox("Protect weekend from heavy work", value=True)
    include_focus_guard = st.checkbox("Add Focus Guard / no-reels blocks", value=False)

    st.divider()

    st.header("AI settings")
    ai_model = st.text_input("AI model", value="gpt-4.1-mini")
    st.caption("Set `OPENAI_API_KEY` in Streamlit secrets to enable AI parsing.")

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
    st.session_state.raw_task_text = DEFAULT_TASKS

if "last_parsed_raw" not in st.session_state:
    st.session_state.last_parsed_raw = st.session_state.raw_task_text

if "editor_version" not in st.session_state:
    st.session_state.editor_version = 0

if "parsed_tasks" not in st.session_state:
    st.session_state.parsed_tasks = normalize_task_categories(
        parse_tasks(st.session_state.raw_task_text)
    )


def get_openai_key() -> str:
    try:
        return st.secrets.get("OPENAI_API_KEY", "")
    except Exception:
        return ""


def clear_generated_schedule():
    for key in ["events", "unscheduled", "issues"]:
        st.session_state.pop(key, None)


def df_to_tasks(df: pd.DataFrame):
    out = []

    for _, row in df.iterrows():
        if not str(row.get("title", "")).strip():
            continue

        kw = {
            field_name: row.get(field_name, field_def.default)
            for field_name, field_def in Task.__dataclass_fields__.items()
        }

        for key in ["duration_min", "sessions_per_week", "min_block_min", "max_block_min"]:
            try:
                kw[key] = int(kw[key])
            except Exception:
                kw[key] = int(Task.__dataclass_fields__[key].default)

        kw["splittable"] = bool(kw["splittable"])
        kw["can_overlap"] = bool(kw["can_overlap"])

        out.append(Task(**kw))

    return normalize_task_categories(out)


def run_schedule(tasks):
    rows = adapt_tasks_for_mood(tasks, mood) if mood != "Normal" else tasks
    rows = normalize_task_categories(rows)

    scheduler = Scheduler(
        wake_time.hour * 60 + wake_time.minute,
        sleep_time.hour * 60 + sleep_time.minute,
        protect_weekend=protect_weekend,
        planning_mode=planning_mode,
    )

    st.session_state.events, st.session_state.unscheduled = scheduler.schedule(
        rows,
        include_focus_guard,
    )

    st.session_state.issues = validate_tasks(
        rows,
        wake_time.hour * 60 + wake_time.minute,
        sleep_time.hour * 60 + sleep_time.minute,
    )


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
            x=alt.X(
                "Day:N",
                sort=DAY_NAMES,
                title=None,
                axis=alt.Axis(labelAngle=0),
            ),
            y=alt.Y("True occupied h:Q", title="Hours"),
            tooltip=["Day", "True occupied h"],
        )
        .properties(height=260)
    )

    st.altair_chart(occupied_chart, use_container_width=True)

    st.markdown("#### Category breakdown")
    st.caption(
        "Scheduled category time. This may be higher than true occupied hours "
        "because overlap is allowed for relationship time."
    )

    long_df = day_df.melt(
        id_vars="Day",
        value_vars=[
            col
            for col in ["Work h", "Personal h", "Relationship h", "Other h"]
            if col in day_df.columns
        ],
        var_name="Category",
        value_name="Hours",
    )

    long_df = long_df[long_df["Hours"] > 0]

    category_chart = (
        alt.Chart(long_df)
        .mark_bar()
        .encode(
            x=alt.X(
                "Day:N",
                sort=DAY_NAMES,
                title=None,
                axis=alt.Axis(labelAngle=0),
            ),
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
        text += "\n\nRecommended actions:  \n"
        text += "  \n".join([f"• {recommendation}" for recommendation in recommendations])

    if level == "error":
        st.error(text)
    elif level == "warning":
        st.warning(text)
    elif level == "info":
        st.info(text)
    else:
        st.success(text)


tab_calendar, tab_tasks, tab_issues, tab_table = st.tabs(
    ["Calendar", "Tasks", "Issues", "Table"]
)


with tab_tasks:
    st.subheader("Task input")
    st.caption(
        "Paste a messy list. Use rule-based parsing or AI parsing, then review the editable table."
    )

    raw = st.text_area(
        "Task list",
        height=320,
        key="raw_task_text",
        label_visibility="collapsed",
    )

    if raw != st.session_state.last_parsed_raw:
        st.session_state.parsed_tasks = normalize_task_categories(parse_tasks(raw))
        st.session_state.last_parsed_raw = raw
        st.session_state.editor_version += 1
        clear_generated_schedule()

    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.5, 3])

    with c1:
        if st.button("Rule parse", type="secondary", use_container_width=True):
            st.session_state.parsed_tasks = normalize_task_categories(
                parse_tasks(st.session_state.raw_task_text)
            )
            st.session_state.last_parsed_raw = st.session_state.raw_task_text
            st.session_state.editor_version += 1
            clear_generated_schedule()
            st.rerun()

    with c2:
        if st.button("AI parse tasks", type="primary", use_container_width=True):
            api_key = get_openai_key()

            if not api_key:
                st.error("Missing `OPENAI_API_KEY` in Streamlit secrets.")
            elif parse_tasks_with_ai is None:
                st.error(
                    "AI parser could not be imported. Check that `ai_parser.py` exists "
                    "and `openai` is installed in requirements.txt."
                )
            else:
                with st.spinner("AI is parsing your task list..."):
                    try:
                        ai_tasks, ai_warnings = parse_tasks_with_ai(
                            st.session_state.raw_task_text,
                            api_key,
                            model=ai_model,
                        )

                        st.session_state.parsed_tasks = normalize_task_categories(ai_tasks)
                        st.session_state.last_parsed_raw = st.session_state.raw_task_text
                        st.session_state.editor_version += 1
                        clear_generated_schedule()

                        if ai_warnings:
                            st.warning("AI warnings: " + "; ".join(map(str, ai_warnings)))

                        st.success("AI parsing complete. Review the table below.")
                        st.rerun()

                    except Exception as exc:
                        st.error(f"AI parsing failed: {exc}")

    with c3:
        uploaded = st.file_uploader(
            "Load JSON",
            type=["json"],
            label_visibility="collapsed",
        )

        if uploaded is not None:
            try:
                loaded = normalize_task_categories(
                    tasks_from_json(uploaded.read().decode("utf-8"))
                )
                st.session_state.parsed_tasks = loaded
                st.session_state.raw_task_text = "\n".join(
                    "• " + (task.notes or task.title) for task in loaded
                )
                st.session_state.last_parsed_raw = st.session_state.raw_task_text
                st.session_state.editor_version += 1
                clear_generated_schedule()
                st.rerun()

            except Exception as exc:
                st.error(f"Could not load JSON: {exc}")

    with c4:
        st.caption(
            "AI parsing is best for category, energy, useful block size, fixed time, "
            "and overlap decisions."
        )

    st.subheader("Review tasks")

    edited_df = st.data_editor(
        pd.DataFrame([asdict(task) for task in st.session_state.parsed_tasks]),
        num_rows="dynamic",
        use_container_width=True,
        height=430,
        key=f"task_editor_{st.session_state.editor_version}",
        column_config={
            "priority": st.column_config.SelectboxColumn(
                "priority",
                options=list(PRIORITY_SCORE.keys()),
            ),
            "task_type": st.column_config.SelectboxColumn(
                "task_type",
                options=["Fixed", "Flexible", "Recurring", "Multi-session"],
            ),
            "fixed_day": st.column_config.SelectboxColumn(
                "fixed_day",
                options=[""] + DAY_NAMES,
            ),
            "preferred_time": st.column_config.SelectboxColumn(
                "preferred_time",
                options=["Morning", "Workday", "Afternoon", "Evening", "Weekend", "Any"],
            ),
            "energy": st.column_config.SelectboxColumn(
                "energy",
                options=["High", "Medium", "Low", "Physical", "Creative"],
            ),
            "location": st.column_config.SelectboxColumn(
                "location",
                options=["Lab", "Home", "Gym", "Any"],
            ),
            "category": st.column_config.SelectboxColumn(
                "category",
                options=CATEGORIES,
            ),
        },
    )

    tasks = df_to_tasks(edited_df)

    b1, b2 = st.columns([1.4, 1.2])

    with b1:
        if st.button("Generate schedule", type="primary", use_container_width=True):
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
        )


if "events" not in st.session_state:
    run_schedule(st.session_state.parsed_tasks)

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

    extra = (
        f" · other: {summary['other_hours']:.1f} h"
        if summary.get("other_hours", 0)
        else ""
    )

    st.caption(
        f"Scheduled time: {summary['scheduled_hours']:.1f} h · "
        f"overlap: {summary['overlap_hours']:.1f} h · "
        f"weekend: {summary['weekend_hours']:.1f} h"
        f"{extra} · mode: {planning_mode}"
    )

    render_plan_health(summary, planning_mode)

    components.html(
        render_calendar_html(
            events,
            week_start,
            start_hour,
            end_hour,
            px_per_hour,
        ),
        height=(end_hour - start_hour) * px_per_hour + 190,
        scrolling=True,
    )

    st.download_button(
        "Download Google Calendar .ics",
        data=events_to_ics(events, week_start).encode("utf-8"),
        file_name="weekly_scheduler_export.ics",
        mime="text/calendar",
    )


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
        st.success("Everything was scheduled.")
    else:
        st.dataframe(
            pd.DataFrame([asdict(item) for item in unscheduled]),
            use_container_width=True,
            hide_index=True,
        )


with tab_table:
    st.subheader("Schedule table")
    st.dataframe(schedule_df, use_container_width=True, hide_index=True)

    st.subheader("Workload by day")
    day_df = ordered_day_df(events)
    st.dataframe(day_df, use_container_width=True, hide_index=True)

    render_workload_charts(day_df)
