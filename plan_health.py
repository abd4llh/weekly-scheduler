def evaluate_plan_health(summary, planning_mode):
    true_hours = summary.get("true_occupied_hours", 0)
    work_hours = summary.get("work_hours", 0)
    weekend_hours = summary.get("weekend_hours", 0)
    unscheduled = summary.get("unscheduled_count", 0)
    unscheduled_high = summary.get("unscheduled_high_count", 0)

    messages = []
    level = "success"

    if true_hours >= 75:
        level = "error"
        messages.append(f"This is an overloaded week: {true_hours:.1f} true occupied hours.")
    elif true_hours >= 65:
        level = "warning"
        messages.append(f"This is a heavy week: {true_hours:.1f} true occupied hours.")
    else:
        messages.append(f"This week looks realistic: {true_hours:.1f} true occupied hours.")

    if work_hours >= 35:
        level = "error" if level == "error" else "warning"
        messages.append(f"Workload is high: {work_hours:.1f} work hours are scheduled.")
    elif work_hours >= 28:
        if level == "success":
            level = "info"
        messages.append(f"Workload is substantial but plausible: {work_hours:.1f} work hours.")

    if weekend_hours >= 18:
        level = "error" if level == "error" else "warning"
        messages.append(f"Weekend load is too high: {weekend_hours:.1f} hours are scheduled on Saturday/Sunday.")
    elif weekend_hours >= 12:
        if level == "success":
            level = "info"
        messages.append(f"Weekend is quite full: {weekend_hours:.1f} hours scheduled.")

    if unscheduled_high > 0:
        level = "error" if level == "error" else "warning"
        messages.append(f"There are {unscheduled_high} high-priority unscheduled items.")
    elif unscheduled > 0:
        if level == "success":
            level = "info"
        messages.append(f"There are {unscheduled} unscheduled lower-priority items.")

    recommendations = []
    if true_hours >= 75:
        recommendations.append("Move optional tasks to next week and reduce weekend load.")
    if unscheduled_high > 0:
        recommendations.append("Use Work-heavy week or Deadline mode, or postpone lower-priority tasks.")
    if weekend_hours >= 18:
        recommendations.append("Switch to Social weekend mode if you want a more realistic weekend.")
    if planning_mode == "Balanced week" and true_hours >= 70:
        recommendations.append("Balanced mode still produced a heavy week, so the input task list is overloaded.")

    return level, messages, recommendations
