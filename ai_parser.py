import json
from openai import OpenAI
from models import Task, CATEGORIES, DAY_NAMES

PRIORITIES=['Critical','High','Medium','Low','Optional']
TYPES=['Fixed','Flexible','Recurring','Multi-session']
PREFS=['Morning','Workday','Afternoon','Evening','Weekend','Any']
ENERGIES=['High','Medium','Low','Physical','Creative']
LOCS=['Lab','Home','Gym','Any']

def pick(v, allowed, default):
    return v if v in allowed else default

def num(v, default, lo=None, hi=None):
    try: x=int(v)
    except Exception: x