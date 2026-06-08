import uuid
from datetime import date, datetime, time, timedelta
from models import DAY_SHORT
from parser_utils import minutes_to_hhmm

def next_monday(today: date) -> date:
    d = (7 - today.weekday()) % 7
    return today + timedelta(days=d or 7)

def esc(text):
    return str(text).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def render_calendar_html(events, week_start, start_hour=6, end_hour=23, px_per_hour=72):
    body_h=(end_hour-start_hour)*px_per_hour
    hours="".join(f'<div class="hour-line" style="top:{(h-start_hour)*px_per_hour}px"></div><div class="hour-label" style="top:{max((h-start_hour)*px_per_hour-8,0)}px">{h:02d}:00</div>' for h in range(start_hour,end_hour+1))
    heads="".join(f'<div class="day-head"><div class="dow">{DAY_SHORT[i]}</div><div class="date-num">{(week_start+timedelta(days=i)).day}</div></div>' for i in range(7))
    colors={"Critical":"critical","High":"high","Medium":"medium","Low":"low","Optional":"optional"}
    cols=[]
    for i in range(7):
        blocks=[]
        for e in [x for x in events if x.day_index==i]:
            s=max(e.start_min,start_hour*60); en=min(e.end_min,end_hour*60)
            if en <= s: continue
            top=((s/60)-start_hour)*px_per_hour; h=max(((en-s)/60)*px_per_hour-4,20)
            blocks.append(f'<div class="event {colors.get(e.priority,"medium")}" style="top:{top}px;height:{h}px"><div class="event-title">{esc(e.title)}</div><div class="event-time">{minutes_to_hhmm(e.start_min)}–{minutes_to_hhmm(e.end_min)}</div><div class="event-notes">{esc(e.explanation or e.notes)}</div></div>')
        cols.append(f'<div class="day-col">{"".join(blocks)}</div>')
    week_end=week_start+timedelta(days=6)
    return f'''<style>body{{margin:0;font-family:Inter,Roboto,Arial,sans-serif;color:#202124}}.cal{{border:1px solid #dadce0;border-radius:18px;overflow:hidden;background:#fff;box-shadow:0 1px 2px rgba(60,64,67,.15)}}.toolbar{{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid #dadce0}}.title{{font-size:19px;font-weight:700}}.range,.legend{{color:#5f6368;font-size:12px}}.legend{{display:flex;gap:10px;flex-wrap:wrap}}.dot{{width:9px;height:9px;border-radius:99px;display:inline-block;margin-right:4px}}.d-critical{{background:#d93025}}.d-high{{background:#1a73e8}}.d-medium{{background:#188038}}.d-low{{background:#f9ab00}}.d-optional{{background:#9334e6}}.scroll{{overflow-x:auto}}.week-head,.week-body{{min-width:960px;display:grid;grid-template-columns:76px repeat(7,1fr)}}.week-head{{border-bottom:1px solid #dadce0}}.week-body{{height:{body_h}px;position:relative}}.tz{{border-right:1px solid #dadce0;color:#5f6368;font-size:11px;display:flex;align-items:end;justify-content:center;padding-bottom:10px}}.day-head{{height:72px;text-align:center;border-right:1px solid #dadce0;padding-top:9px}}.dow{{color:#5f6368;font-size:12px;text-transform:uppercase}}.date-num{{margin:6px auto 0;width:36px;height:36px;line-height:36px;border-radius:999px;font-size:20px}}.axis{{position:relative;height:{body_h}px;border-right:1px solid #dadce0}}.hour-line{{position:absolute;left:0;right:0;height:1px;background:#eef0f3}}.hour-label{{position:absolute;right:8px;color:#5f6368;font-size:11px}}.day-col{{position:relative;height:{body_h}px;border-right:1px solid #dadce0;background:linear-gradient(to bottom,transparent {px_per_hour-1}px,#eef0f3 {px_per_hour-1}px,#eef0f3 {px_per_hour}px);background-size:100% {px_per_hour}px}}.event{{position:absolute;left:5px;right:5px;border-radius:10px;padding:6px 7px;overflow:hidden;font-size:12px;line-height:1.23;box-shadow:0 1px 2px rgba(0,0,0,.12);border-left:4px solid rgba(0,0,0,.2)}}.event-title{{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.event-time{{font-size:11px;opacity:.88;margin-top:2px}}.event-notes{{font-size:10px;opacity:.70;margin-top:4px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}.critical{{background:#fce8e6;color:#5f0f0a;border-left-color:#d93025}}.high{{background:#e8f0fe;color:#174ea6;border-left-color:#1a73e8}}.medium{{background:#e6f4ea;color:#0d652d;border-left-color:#188038}}.low{{background:#fef7e0;color:#7a4d00;border-left-color:#f9ab00}}.optional{{background:#f3e8fd;color:#681da8;border-left-color:#9334e6}}</style><div class="cal"><div class="toolbar"><div><div class="title">Weekly Schedule</div><div class="range">{week_start.strftime('%d %b %Y')} – {week_end.strftime('%d %b %Y')}</div></div><div class="legend"><span><i class="dot d-critical"></i>Critical</span><span><i class="dot d-high"></i>High</span><span><i class="dot d-medium"></i>Medium</span><span><i class="dot d-low"></i>Low</span><span><i class="dot d-optional"></i>Optional</span></div></div><div class="scroll"><div class="week-head"><div class="tz">GMT+2</div>{heads}</div><div class="week-body"><div class="axis">{hours}</div>{''.join(cols)}</div></div></div>'''

def escape_ics_text(text):
    return str(text).replace("\\","\\\\").replace(";","\\;").replace(",","\\,").replace("\n","\\n")

def fold_line(line, limit=75):
    parts=[]
    while len(line)>limit:
        parts.append(line[:limit]); line=" "+line[limit:]
    return "\r\n".join(parts+[line])

def events_to_ics(events, week_start, calendar_name="Weekly Scheduler"):
    tzid="Europe/Berlin"; stamp=datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Weekly Scheduler//EN","CALSCALE:GREGORIAN","METHOD:PUBLISH",f"X-WR-CALNAME:{escape_ics_text(calendar_name)}","X-WR-TIMEZONE:Europe/Berlin","BEGIN:VTIMEZONE","TZID:Europe/Berlin","X-LIC-LOCATION:Europe/Berlin","BEGIN:DAYLIGHT","TZOFFSETFROM:+0100","TZOFFSETTO:+0200","TZNAME:CEST","DTSTART:19700329T020000","RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU","END:DAYLIGHT","BEGIN:STANDARD","TZOFFSETFROM:+0200","TZOFFSETTO:+0100","TZNAME:CET","DTSTART:19701025T030000","RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU","END:STANDARD","END:VTIMEZONE"]
    for e in events:
        sd=week_start+timedelta(days=e.day_index); stt=datetime.combine(sd,time(e.start_min//60,e.start_min%60)); enn=datetime.combine(sd,time(e.end_min//60,e.end_min%60))
        desc=(e.notes or e.source_task)+("\n\nWhy scheduled here: "+e.explanation if e.explanation else "")
        lines += ["BEGIN:VEVENT",f"UID:{uuid.uuid4()}@weekly-scheduler",f"DTSTAMP:{stamp}",f"DTSTART;TZID={tzid}:{stt.strftime('%Y%m%dT%H%M%S')}",f"DTEND;TZID={tzid}:{enn.strftime('%Y%m%dT%H%M%S')}",f"SUMMARY:{escape_ics_text(e.title)}",f"DESCRIPTION:{escape_ics_text(desc)}"]
        if any(k in e.title.lower() for k in ["experiment","gym","german","send","book lab","doctor","meeting"]):
            lines += ["BEGIN:VALARM","TRIGGER:-PT10M","ACTION:DISPLAY",f"DESCRIPTION:{escape_ics_text(e.title)}","END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(x) for x in lines)+"\r\n"
