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
from models import (
    APP_VERSION,
    CATEGORIES,
    COGNITIVE_LOADS,
    DAY_NAMES,
    PHYSICAL_LOADS,
    PLANNING_MODES,
    PRIORITY_SCORE,
    SESSION_DISTRIBUTIONS,
    Task,
)
from optimizer.app_bridge import optimize_legacy_week
from parser_utils import minutes_to_hhmm, tasks_from_json, tasks_to_json
from routine_utils import ROUTINE_CATEGORY, place_routines_flexibly, routine_requirements_payload
from scheduler_engine import Scheduler

st.set_page_config(page_title="Weekly Scheduler", page_icon="🗓️", layout="wide")
st.markdown("""
<style>
.block-container {max-width: 1500px; padding-top: 1.2rem;}
.hero {padding:18px 22px;border:1px solid #e5e7eb;border-radius:18px;background:#fff;margin-bottom:14px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.hero h1 {font-size:32px;margin:0 0 6px;letter-spacing:-.04em}.hero p{margin:0;color:#6b7280;font-size:15px}
div[data-testid="stMetric"] {border:1px solid #e5e7eb;border-radius:14px;padding:10px 13px;background:#fff}
</style>
<div class="hero"><h1>Weekly Scheduler</h1><p>AI extracts profession-independent task metadata. OR-Tools places the work.</p></div>
""", unsafe_allow_html=True)


def get_secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def reset_schedule():
    for key in ["events", "unscheduled", "issues", "optimizer_info"]:
        st.session_state.pop(key, None)


with st.sidebar:
    st.header("Plan settings")
    st.caption(APP_VERSION)
    week_start = st.date_input("Week starts on", value=next_monday(date.today()))
    planning_engine = st.selectbox("Planning engine", ["Optimizer preview", "Legacy AI planner"], index=0)
    planning_mode = st.selectbox("Planning mode", PLANNING_MODES, index=0)
    protect_weekend = st.checkbox("Protect weekend from heavy work", value=True)
    include_focus_guard = st.checkbox("Add Focus Guard / transition blocks", value=False, disabled=planning_engine == "Optimizer preview")
    wake_time = st.time_input("Wake time", value=time(6, 0))
    sleep_time = st.time_input("Sleep target", value=time(23, 0))

    with st.expander("Daily rhythm and meals", expanded=True):
        morning_ramp_enabled = st.checkbox("Add morning routine", value=True)
        morning_ramp_min = st.slider("Morning routine duration", 15, 120, 60, 15, disabled=not morning_ramp_enabled)
        breakfast_enabled = st.checkbox("Add breakfast", value=False)
        if breakfast_enabled:
            breakfast_window_start = st.time_input("Breakfast earliest", value=time(7, 0))
            breakfast_preferred_time = st.time_input("Breakfast preferred", value=time(8, 0))
            breakfast_window_end = st.time_input("Breakfast latest", value=time(10, 0))
            breakfast_duration_min = st.selectbox("Breakfast duration", [15, 30, 45, 60], index=1)
        else:
            breakfast_window_start, breakfast_preferred_time, breakfast_window_end, breakfast_duration_min = time(7), time(8), time(10), 30
        lunch_enabled = st.checkbox("Add lunch", value=False)
        if lunch_enabled:
            lunch_window_start = st.time_input("Lunch earliest", value=time(11, 0))
            lunch_preferred_time = st.time_input("Lunch preferred", value=time(13, 0))
            lunch_window_end = st.time_input("Lunch latest", value=time(14, 0))
            lunch_duration_min = st.selectbox("Lunch duration", [15, 30, 45, 60, 75], index=2)
        else:
            lunch_window_start, lunch_preferred_time, lunch_window_end, lunch_duration_min = time(11), time(13), time(14), 45
        dinner_enabled = st.checkbox("Add dinner", value=False)
        if dinner_enabled:
            dinner_window_start = st.time_input("Dinner earliest", value=time(18, 0))
            dinner_preferred_time = st.time_input("Dinner preferred", value=time(19, 0))
            dinner_window_end = st.time_input("Dinner latest", value=time(21, 0))
            dinner_duration_min = st.selectbox("Dinner duration", [30, 45, 60, 75, 90], index=2)
        else:
            dinner_window_start, dinner_preferred_time, dinner_window_end, dinner_duration_min = time(18), time(19), time(21), 60
        wind_down_enabled = st.checkbox("Add evening wind-down", value=False)
        wind_down_min = st.slider("Wind-down duration", 15, 90, 30, 15, disabled=not wind_down_enabled)
        transition_min = st.select_slider("Default transition after demanding tasks", [0, 5, 10, 15, 20, 30], value=15)

    with st.expander("Optimizer preferences", expanded=False):
        preferred_daily_hours = st.slider("Preferred flexible workload per day", 2.0, 12.0, 8.0, 0.5)
        max_daily_hours = st.slider("Hard flexible-work maximum", preferred_daily_hours, 14.0, max(10.0, preferred_daily_hours), 0.5)
        total_burden_hours = st.slider("Preferred total scheduled burden", 4.0, 16.0, 10.0, 0.5)
        focused_hours = st.slider("Preferred focused work per day", 1.0, 8.0, 4.0, 0.5)
        late_focus_time = st.time_input("Penalize focused work after", value=time(19, 0))
        default_travel_min = st.select_slider("Default travel between different locations", [0, 5, 10, 15, 20, 30, 45, 60], value=20)
        compact_gap_min = st.select_slider("Preferred maximum gap in routine sequences", [0, 10, 15, 20, 30, 45, 60], value=30)

    start_hour = st.slider("Calendar start hour", 4, 10, 6)
    end_hour = st.slider("Calendar end hour", 18, 24, 23)
    px_per_hour = st.slider("Calendar row height", 48, 96, 72)

for key, value in {"raw_task_text":"", "parsed_tasks":[], "ai_warnings":[], "editor_version":0, "events":[], "unscheduled":[], "issues":[], "optimizer_info":{}}.items():
    if key not in st.session_state:
        st.session_state[key] = value


def df_to_tasks(df):
    tasks = []
    for _, row in df.iterrows():
        if not str(row.get("title", "")).strip():
            continue
        kw = {name: row.get(name, field.default) for name, field in Task.__dataclass_fields__.items()}
        for name in ["duration_min", "sessions_per_week", "recovery_min", "min_block_min", "max_block_min", "phase"]:
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
    return pd.DataFrame([{
        "Task": t.title,
        "Duration": f"{t.duration_min} min" if t.task_type == "Recurring" else f"{t.duration_min} min total",
        "Priority": t.priority,
        "Location": t.location,
        "Cognitive load": t.cognitive_load,
        "Distribution": t.session_distribution,
        "Notes": t.notes,
    } for t in user_tasks_only(tasks)])


def technical_task_dataframe(tasks):
    hidden = {"confidence", "duration_is_estimated", "assumptions", "needs_clarification", "clarification_question"}
    rows = []
    for task in user_tasks_only(tasks):
        row = asdict(task)
        for col in hidden:
            row.pop(col, None)
        rows.append(row)
    return pd.DataFrame(rows)


def settings_payload():
    wake_min = wake_time.hour * 60 + wake_time.minute
    sleep_min = sleep_time.hour * 60 + sleep_time.minute
    return {
        "wake_min": wake_min, "sleep_min": sleep_min,
        "wake_time": minutes_to_hhmm(wake_min), "sleep_time": minutes_to_hhmm(sleep_min),
        "planning_mode": planning_mode, "planning_engine": planning_engine,
        "protect_weekend": protect_weekend, "include_focus_guard": include_focus_guard,
        "week_start": str(week_start), "timezone": "Europe/Berlin",
        "morning_ramp_enabled": morning_ramp_enabled, "morning_ramp_min": morning_ramp_min,
        "breakfast_enabled": breakfast_enabled, "breakfast_window_start": breakfast_window_start.strftime("%H:%M"),
        "breakfast_preferred_time": breakfast_preferred_time.strftime("%H:%M"), "breakfast_window_end": breakfast_window_end.strftime("%H:%M"),
        "breakfast_duration_min": breakfast_duration_min,
        "lunch_enabled": lunch_enabled, "lunch_window_start": lunch_window_start.strftime("%H:%M"),
        "lunch_preferred_time": lunch_preferred_time.strftime("%H:%M"), "lunch_window_end": lunch_window_end.strftime("%H:%M"),
        "lunch_duration_min": lunch_duration_min,
        "dinner_enabled": dinner_enabled, "dinner_window_start": dinner_window_start.strftime("%H:%M"),
        "dinner_preferred_time": dinner_preferred_time.strftime("%H:%M"), "dinner_window_end": dinner_window_end.strftime("%H:%M"),
        "dinner_duration_min": dinner_duration_min,
        "wind_down_enabled": wind_down_enabled, "wind_down_min": wind_down_min, "transition_min": transition_min,
        "preferred_daily_flexible_min": int(preferred_daily_hours * 60),
        "max_daily_flexible_min": int(max_daily_hours * 60),
        "preferred_daily_total_min": int(total_burden_hours * 60),
        "preferred_daily_focus_min": int(focused_hours * 60),
        "late_focus_start_min": late_focus_time.hour * 60 + late_focus_time.minute,
        "default_travel_min": default_travel_min, "compact_gap_min": compact_gap_min,
    }


def store_schedule(tasks, events, unscheduled, issues, warnings=None, optimizer_info=None):
    st.session_state.parsed_tasks = normalize_task_categories(tasks)
    st.session_state.events, st.session_state.unscheduled, st.session_state.issues = events, unscheduled, issues
    st.session_state.ai_warnings = list(warnings or [])
    st.session_state.optimizer_info = dict(optimizer_info or {})
    st.session_state.editor_version += 1


def finalize_and_validate(tasks, events, unscheduled, anchors):
    settings = settings_payload()
    tasks, events, unscheduled = complete_schedule_constraints(tasks, events, unscheduled, anchors, settings)
    issues = validate_ai_plan(tasks, events, unscheduled, settings["wake_min"], settings["sleep_min"], anchors, settings)
    return tasks, events, unscheduled, issues


def deterministic_fallback(tasks):
    settings = settings_payload()
    scheduler = Scheduler(settings["wake_min"], settings["sleep_min"], protect_weekend=protect_weekend, planning_mode=planning_mode)
    events, unscheduled = scheduler.schedule(tasks, include_focus_guard)
    tasks, events = place_routines_flexibly(tasks, events, settings)
    return finalize_and_validate(tasks, events, unscheduled, tasks)


def generate_schedule(raw_text):
    api_key, model = get_secret("OPENAI_API_KEY", ""), get_secret("OPENAI_MODEL", "gpt-4.1-mini")
    if not api_key:
        st.error("AI is not configured for this deployment."); return
    if not raw_text.strip():
        st.error("Paste your tasks first."); return
    with st.spinner("Understanding tasks and optimizing the week..."):
        try:
            parsed, warnings = parse_tasks_with_ai(raw_text, api_key, model=model)
            parsed = normalize_task_categories(parsed)
            if planning_engine == "Optimizer preview":
                tasks, events, unscheduled, issues, metadata = optimize_legacy_week(parsed, week_start, settings_payload())
                store_schedule(tasks, events, unscheduled, issues, warnings, metadata); return
            tasks, events, unscheduled, _, planner_warnings = plan_week_with_ai(raw_text, parsed, api_key, model, settings_payload())
            tasks, events, unscheduled, issues = finalize_and_validate(tasks, events, unscheduled, parsed)
            store_schedule(tasks, events, unscheduled, issues, list(warnings) + list(planner_warnings), {"engine":"Legacy AI planner"})
        except Exception as exc:
            st.error(f"Schedule generation failed: {exc}")


def regenerate(reviewed):
    if planning_engine == "Optimizer preview":
        tasks, events, unscheduled, issues, metadata = optimize_legacy_week(reviewed, week_start, settings_payload())
        store_schedule(tasks, events, unscheduled, issues, optimizer_info=metadata)
    else:
        api_key, model = get_secret("OPENAI_API_KEY", ""), get_secret("OPENAI_MODEL", "gpt-4.1-mini")
        tasks, events, unscheduled, _, warnings = plan_week_with_ai(st.session_state.raw_task_text, reviewed, api_key, model, settings_payload())
        tasks, events, unscheduled, issues = finalize_and_validate(tasks, events, unscheduled, reviewed)
        store_schedule(tasks, events, unscheduled, issues, warnings, {"engine":"Legacy AI planner"})


tab_calendar, tab_tasks, tab_issues, tab_table = st.tabs(["Calendar", "Tasks", "Issues", "Table"])
with tab_tasks:
    st.subheader("Paste your week")
    st.text_area("Task list", key="raw_task_text", height=320, label_visibility="collapsed", placeholder="Describe appointments, workloads, routines, locations, deadlines, and preferences in your own words.")
    if st.button("Generate schedule", type="primary", width="stretch"):
        reset_schedule(); generate_schedule(st.session_state.raw_task_text); st.rerun()
    uploaded = st.file_uploader("Load saved task JSON", type=["json"])
    if uploaded:
        st.session_state.parsed_tasks = normalize_task_categories(tasks_from_json(uploaded.read().decode("utf-8")))
        st.session_state.editor_version += 1; reset_schedule(); st.rerun()
    routines = routine_requirements_payload(settings_payload())
    if routines:
        with st.expander("Automatic routine windows"):
            st.dataframe(pd.DataFrame(routines)[["title","duration_min","window_start","preferred_start","window_end"]], width="stretch", hide_index=True)
    for warning in st.session_state.ai_warnings:
        st.warning(str(warning))
    if st.session_state.parsed_tasks:
        st.subheader("Detected tasks")
        st.dataframe(simple_task_dataframe(st.session_state.parsed_tasks), width="stretch", hide_index=True)
        with st.expander("Advanced review / edit detected tasks"):
            edited = st.data_editor(
                technical_task_dataframe(st.session_state.parsed_tasks), num_rows="dynamic", width="stretch", height=460,
                key=f"task_editor_{st.session_state.editor_version}",
                column_config={
                    "priority": st.column_config.SelectboxColumn(options=list(PRIORITY_SCORE.keys())),
                    "task_type": st.column_config.SelectboxColumn(options=["Fixed","Flexible","Recurring","Multi-session"]),
                    "fixed_day": st.column_config.SelectboxColumn(options=[""] + DAY_NAMES),
                    "required_day": st.column_config.SelectboxColumn(options=[""] + DAY_NAMES),
                    "earliest_day": st.column_config.SelectboxColumn(options=[""] + DAY_NAMES),
                    "deadline_day": st.column_config.SelectboxColumn(options=[""] + DAY_NAMES),
                    "preferred_time": st.column_config.SelectboxColumn(options=["Morning","Workday","Afternoon","Evening","Weekend","Any"]),
                    "location": st.column_config.TextColumn(help="Use the same concise label for the same place."),
                    "cognitive_load": st.column_config.SelectboxColumn(options=COGNITIVE_LOADS),
                    "physical_load": st.column_config.SelectboxColumn(options=PHYSICAL_LOADS),
                    "session_distribution": st.column_config.SelectboxColumn(options=SESSION_DISTRIBUTIONS),
                    "recovery_min": st.column_config.NumberColumn(min_value=0, max_value=180, step=5),
                    "category": st.column_config.SelectboxColumn(options=CATEGORIES),
                })
            reviewed = df_to_tasks(edited)
            if st.button("Regenerate schedule from edited tasks", width="stretch"):
                with st.spinner("Rebuilding schedule..."):
                    regenerate(reviewed)
                st.rerun()
            st.download_button("Save task JSON", tasks_to_json(reviewed).encode("utf-8"), "weekly_scheduler_tasks.json", "application/json", width="stretch")


events, unscheduled, issues, info = st.session_state.events, st.session_state.unscheduled, st.session_state.issues, st.session_state.optimizer_info
summary = workload_summary(events, unscheduled)
schedule_df = pd.DataFrame([{"Day":DAY_NAMES[e.day_index],"Start":minutes_to_hhmm(e.start_min),"End":minutes_to_hhmm(e.end_min),"Task":e.title,"Category":e.category,"Location":e.location,"Priority":e.priority,"Explanation":e.explanation} for e in events])
with tab_calendar:
    cols = st.columns(5)
    for col, label, value in zip(cols, ["Occupied","Work","Personal","Relationship","Unscheduled"], [f"{summary['true_occupied_hours']:.1f} h",f"{summary['work_hours']:.1f} h",f"{summary['personal_hours']:.1f} h",f"{summary['relationship_hours']:.1f} h",int(summary['unscheduled_count'])]):
        col.metric(label, value)
    if info:
        st.caption(f"Engine: {info.get('engine')} · status: {info.get('status','')} · solver: {info.get('solve_seconds',0):.3f} s")
    if events:
        components.html(render_calendar_html(events, week_start, start_hour, end_hour, px_per_hour), height=(end_hour-start_hour)*px_per_hour+190, scrolling=True)
        st.download_button("Download Google Calendar .ics", events_to_ics(events, week_start).encode("utf-8"), "weekly_scheduler_export.ics", "text/calendar")
    else:
        st.info("Paste tasks and generate a schedule.")
with tab_issues:
    st.subheader("Validation")
    st.dataframe(pd.DataFrame(issues), width="stretch", hide_index=True) if issues else st.success("No validation issues found." if events else "No schedule generated yet.")
    st.subheader("Unscheduled")
    st.dataframe(pd.DataFrame([asdict(x) for x in unscheduled]), width="stretch", hide_index=True) if unscheduled else st.success("Nothing unscheduled." if events else "No schedule generated yet.")
with tab_table:
    st.dataframe(schedule_df, width="stretch", hide_index=True)
