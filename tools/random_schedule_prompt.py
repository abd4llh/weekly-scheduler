"""Print a different cross-domain scheduler prompt on each run.

Use this for manual regression testing so development does not overfit one profession.
"""

import random

SCENARIOS = [
    """I coordinate a community center. This week I need six hours to prepare a funding proposal, preferably split across two or three days. I have a volunteer briefing Tuesday at 18:30 for 90 minutes at the center and a supplier visit Thursday sometime in the afternoon. Inventory counting takes two hours and is easiest to batch with the supplier visit because both happen at the center. I want a 40-minute walk on four different days and need Friday evening free for family.""",
    """I am studying for professional exams. I need nine hours of statistics revision and six hours of case-study practice before Sunday. Revision should be spread across at least three days, while the three mock cases can be batched if useful. I attend an online seminar Wednesday 10:00 to 12:00, work at a cafe Friday 16:00 to 21:00, and want one hour at the gym three times this week. Avoid high-concentration study after 20:00.""",
    """I manage maintenance for two buildings. Inspect Building A on Monday morning for two hours and Building B on Thursday afternoon for two hours. Order replacement parts after both inspections are complete; that admin work takes 45 minutes and can be done remotely. I also need four hours for preventive-maintenance documentation, two tenant calls on different days, and a fixed safety meeting Wednesday at 14:00 for one hour at the main office.""",
    """I teach at a language school. Prepare four lesson plans this week, about 75 minutes each, preferably on different days. Classes are fixed Monday 17:00 to 20:00 and Wednesday 17:00 to 20:00 at the school. Marking takes five hours total and is easier in two long batches at home. I need to collect printed materials from the copy shop Tuesday afternoon, and I want a quiet planning session Sunday morning for 90 minutes.""",
    """I am organizing a small conference. Finalize the attendee list for three hours, then send badges to print for 30 minutes. Venue walkthrough is Tuesday at 15:00 for two hours, sponsor call Thursday at 11:00 for one hour, and the registration-team training is Saturday 09:00 to 12:00. I also need six hours for the program booklet, preferably in focused blocks before Friday, plus two grocery trips for the volunteer kitchen that can be grouped with other errands.""",
    """I work rotating shifts and manage a household. My shifts are Monday 07:00 to 15:00, Wednesday 15:00 to 23:00, and Saturday 09:00 to 17:00 at the hospital. I need two hours for an online course on two separate days, laundry for 90 minutes at home, grocery shopping for one hour at the supermarket, and three 45-minute mobility sessions. Schedule demanding study away from the late shift and leave at least 30 minutes after each shift before another task.""",
    """I run a small repair workshop. Diagnose three customer devices, about one hour each; these can be batched at the workshop. Replacement parts arrive Wednesday, so the four-hour repair block cannot begin before then. Customer pickup appointments are Friday at 13:00 and 16:00 for 30 minutes each. I need two hours for bookkeeping at home, one supply-store trip, and a two-hour training session Sunday afternoon.""",
    """I am preparing a research field trip. Complete five hours of route planning at the office, book accommodation for 45 minutes online, and test four equipment kits for 50 minutes each at the storage facility. Equipment tests should be on at least two days because batteries need charging between rounds. I have a team meeting Tuesday at 09:30 and a medical appointment Thursday at 16:00. Keep Friday afternoon available for unexpected changes.""",
]

if __name__ == "__main__":
    print(random.choice(SCENARIOS))
