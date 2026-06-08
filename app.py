from dataclasses import asdict
from datetime import date, time

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from calendar_utils import events_to_ics, next_monday, render_calendar_html
from models import APP_VERSION, DAY_NAMES, PRIORITY_SCORE, Task
from parser_utils import DEFAULT_TASKS, adapt_tasks_for_mood, minutes_to_hhmm, parse_tasks, tasks_from_json, tasks_to_json, validate_tasks
from scheduler_engine import Scheduler

st.set_page_config(page_title="Weekly Scheduler", page_icon="🗓️", layout="wide", initial_sidebar_state="expanded")
st.markdown('''<style>.main>div{padding-top:1.25rem}.hero{padding:22px 24px;border:1px solid #dadce0;border-radius:22px;background:linear-gradient(135deg,#f8fafd 0%,#fff 45%,#f1f5ff 100%);margin-bottom:18px}.hero-title{font-size:34px;font-weight:760;letter-spacing:-.045em;color:#202124;margin-bottom:6px}.hero-sub{color:#5f6368;font-size:15px;max-width:920px}div[data-testid="stMetric"]{border:1px solid #dadce0;border-radius:16px;padding:10px 14px;background:#fff}</style><div class="hero"><div class="hero-title">Weekly Scheduler</div><div class="hero-sub">Phase 1: validation, unscheduled tasks, conflict detection, explanations, JSON save/load, and Google Calendar export.</div></div>''', unsafe_allow_html=True)

with st.sidebar:
    st.header("Schedule settings")
    st.caption(APP_VERSION)
    week_start = st.date_input("Week starts on", value=next_monday(date.today()))
    wake_time = st.time_input("Wake time", value=time(6,0))
    sleep_time = st.time_input("Sleep target", value=time(23,0))
    mood = st.selectbox("Mood / energy mode", ["Normal","Productive","Creative","Tired","Physically energetic","Low motivation"])
    protect_weekend = st.checkbox("Protect weekend from heavy work", value=True)
    include_focus_guard = st.checkbox("Add Focus Guard / no-reels blocks", value=False)
    st.divider(); st.caption("Calendar display")
    start_hour = st.slider("Start hour", 4, 10, 6)
    end_hour = st.slider("End hour", 18, 24, 23)
    px_per_hour = st.slider("Row height", 48, 96, 72)

if "raw_task_text" not in st.session_state: st.session_state.raw_task_text = DEFAULT_TASKS
if "last_parsed_raw" not in st.session_state: st.session_state.last_parsed_raw = st.session_state.raw_task_text
if "editor_version" not in st.session_state: st.session_state.editor_version = 0
if "parsed_tasks" not in st.session_state: st.session_state.parsed_tasks = parse_tasks(st.session_state.raw_task_text)

def df_to_tasks(df):
    out = []
    for _, row in df.iterrows():
        if not str(row.get("title", "")).strip(): continue
        kw = {f: row.get(f, Task.__dataclass_fields__[f].default) for f in Task.__dataclass_fields__}
        for k in ["duration_min","sessions_per_week","min_block_min","max_block_min"]:
            try: kw[k] = int(kw[k])
            except Exception: kw[k] = int(Task.__dataclass_fields__[k].default)
        kw["splittable"] = bool(kw["splittable"]); kw["can_overlap"] = bool(kw["can_overlap"])
        out.append(Task(**kw))
    return out

def run_schedule(tasks):
    rows = adapt_tasks_for_mood(tasks, mood) if mood != "Normal" else tasks
    sch = Scheduler(wake_time.hour*60+wake_time.minute, sleep_time.hour*60+sleep_time.minute, protect_weekend=protect_weekend)
    st.session_state.events, st.session_state.unscheduled = sch.schedule(rows, include_focus_guard)
    st.session_state.issues = validate_tasks(rows, wake_time.hour*60+wake_time.minute, sleep_time.hour*60+sleep_time.minute)

tab_calendar, tab_tasks, tab_issues, tab_table = st.tabs(["📅 Calendar","📝 Tasks","⚠️ Issues","📋 Table"])

with tab_tasks:
    st.subheader("1) Paste messy task list")
    raw = st.text_area("Task list", height=320, key="raw_task_text")
    if raw != st.session_state.last_parsed_raw:
        st.session_state.parsed_tasks = parse_tasks(raw)
        st.session_state.last_parsed_raw = raw
        st.session_state.editor_version += 1
        for k in ["events","unscheduled","issues"]: st.session_state.pop(k, None)
    c1,c2,c3 = st.columns([1.2,1.2,4])
    with c1:
        if st.button("Refresh task table", type="primary", use_container_width=True):
            st.session_state.parsed_tasks = parse_tasks(st.session_state.raw_task_text)
            st.session_state.last_parsed_raw = st.session_state.raw_task_text
            st.session_state.editor_version += 1
            st.rerun()
    with c2:
        uploaded = st.file_uploader("Load JSON", type=["json"], label_visibility="collapsed")
        if uploaded is not None:
            try:
                loaded = tasks_from_json(uploaded.read().decode("utf-8"))
                st.session_state.parsed_tasks = loaded
                st.session_state.raw_task_text = "\n".join("• " + (t.notes or t.title) for t in loaded)
                st.session_state.last_parsed_raw = st.session_state.raw_task_text
                st.session_state.editor_version += 1
                st.rerun()
            except Exception as exc: st.error(f"Could not load JSON: {exc}")
    with c3: st.caption("New lines are automatically parsed. Review the table, then generate the calendar.")
    st.subheader("2) Review and edit parsed tasks")
    edited_df = st.data_editor(
        pd.DataFrame([asdict(t) for t in st.session_state.parsed_tasks]),
        num_rows="dynamic", use_container_width=True, height=430, key=f"task_editor_{st.session_state.editor_version}",
        column_config={
            "priority": st.column_config.SelectboxColumn("priority", options=list(PRIORITY_SCORE.keys())),
            "task_type": st.column_config.SelectboxColumn("task_type", options=["Fixed","Flexible","Recurring","Multi-session"]),
            "fixed_day": st.column_config.SelectboxColumn("fixed_day", options=[""]+DAY_NAMES),
            "preferred_time": st.column_config.SelectboxColumn("preferred_time", options=["Morning","Workday","Afternoon","Evening","Weekend","Any"]),
            "energy": st.column_config.SelectboxColumn("energy", options=["High","Medium","Low","Physical","Creative"]),
            "location": st.column_config.SelectboxColumn("location", options=["Lab","Home","Gym","Any"]),
        })
    tasks = df_to_tasks(edited_df)
    b1,b2 = st.columns([1.4,1.2])
    with b1:
        if st.button("Generate / update calendar", type="primary", use_container_width=True):
            st.session_state.parsed_tasks = tasks
            run_schedule(tasks)
            st.success("Calendar updated. Open the Calendar tab.")
    with b2:
        st.download_button("Save reviewed task JSON", data=tasks_to_json(tasks).encode("utf-8"), file_name="weekly_scheduler_tasks.json", mime="application/json", use_container_width=True)

if "events" not in st.session_state: run_schedule(st.session_state.parsed_tasks)
events = st.session_state.events
unscheduled = st.session_state.get("unscheduled", [])
issues = st.session_state.get("issues", [])
schedule_df = pd.DataFrame([{"Day":DAY_NAMES[e.day_index],"Start":minutes_to_hhmm(e.start_min),"End":minutes_to_hhmm(e.end_min),"Task":e.title,"Priority":e.priority,"Explanation":e.explanation,"Notes":e.notes} for e in events])

with tab_calendar:
    counted = [e for e in events if e.source_task != "Focus Guard"]
    total = sum((e.end_min-e.start_min) for e in counted)/60
    high = sum(1 for e in counted if e.priority in ["Critical","High"])
    weekend = sum((e.end_min-e.start_min) for e in counted if e.day_index in [5,6])/60
    a,b,c,d,e = st.columns(5)
    a.metric("Scheduled tasks", len(counted)); b.metric("Scheduled hours", f"{total:.1f}"); c.metric("Unscheduled", len(unscheduled)); d.metric("High-priority blocks", high); e.metric("Weekend hours", f"{weekend:.1f}")
    if unscheduled: st.warning(f"{len(unscheduled)} task(s) could not be fully scheduled. Check the Issues tab.")
    components.html(render_calendar_html(events, week_start, start_hour, end_hour, px_per_hour), height=(end_hour-start_hour)*px_per_hour+190, scrolling=True)
    st.download_button("Download Google Calendar .ics", data=events_to_ics(events, week_start).encode("utf-8"), file_name="weekly_scheduler_export.ics", mime="text/calendar")

with tab_issues:
    st.markdown("### Validation warnings")
    if not issues: st.success("No validation issues found.")
    else:
        for i in issues:
            msg = f"**{i['task']}** — {i['message']}"
            (st.error if i['level']=="error" else st.warning if i['level']=="warning" else st.info)(msg)
        st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)
    st.markdown("### Unscheduled or partially scheduled tasks")
    if not unscheduled: st.success("Everything was scheduled.")
    else: st.dataframe(pd.DataFrame([asdict(u) for u in unscheduled]), use_container_width=True, hide_index=True)

with tab_table:
    st.markdown("### Schedule table"); st.dataframe(schedule_df, use_container_width=True, hide_index=True)
    st.markdown("### Day-by-day")
    for day in DAY_NAMES:
        with st.expander(day, expanded=day in ["Monday","Tuesday"]):
            cols = ["Start","End","Task","Priority","Explanation"]
            st.dataframe(schedule_df[schedule_df["Day"]==day][cols] if not schedule_df.empty else pd.DataFrame(columns=cols), use_container_width=True, hide_index=True)
    if not schedule_df.empty:
        st.markdown("### Workload summary")
        tmp = schedule_df.copy()
        tmp["Duration_h"] = (pd.to_timedelta(tmp["End"]+":00") - pd.to_timedelta(tmp["Start"]+":00")).dt.total_seconds()/3600
        st.bar_chart(tmp.groupby("Day", sort=False)["Duration_h"].sum().reindex(DAY_NAMES).fillna(0))
