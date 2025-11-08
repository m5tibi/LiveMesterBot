import os
import time
import csv
import signal
import requests
from dotenv import load_dotenv
from datetime import datetime, time as dtime
import pytz

load_dotenv()

# --- ENV (általános) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN","").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID","").strip()

RAPIDAPI_KEY  = os.getenv("RAPIDAPI_KEY","").strip()
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST","api-football-v1.p.rapidapi.com").strip()

POLL_SECONDS = int(os.getenv("POLL_SECONDS","150"))
ACTIVE_HOURS_START = os.getenv("ACTIVE_HOURS_START","05:00")
ACTIVE_HOURS_END   = os.getenv("ACTIVE_HOURS_END","23:00")
PEAK_HOURS_START   = os.getenv("PEAK_HOURS_START","18:00")
PEAK_HOURS_END     = os.getenv("PEAK_HOURS_END","22:00")
PEAK_POLL_SECONDS  = int(os.getenv("PEAK_POLL_SECONDS","120"))

MAX_FIXTURES_PER_CYCLE = int(os.getenv("MAX_FIXTURES_PER_CYCLE","12"))
PEAK_MAX_FIXTURES_PER_CYCLE = int(os.getenv("PEAK_MAX_FIXTURES_PER_CYCLE","16"))

STATS_COOLDOWN_MIN = int(os.getenv("STATS_COOLDOWN_MIN","5"))

TIMEZONE = os.getenv("TIMEZONE","Europe/Budapest")
MUTE_NO_SIGNAL = os.getenv("MUTE_NO_SIGNAL", "1") == "1"
SEND_ONLINE_ON_START = os.getenv("SEND_ONLINE_ON_START", "0") == "1"
RUN_MINUTES = int(os.getenv("RUN_MINUTES", "8"))
BACKOFF_MAX_SEC = int(os.getenv("BACKOFF_MAX_SEC", "60"))

# --- Piacok ---
ENABLE_NEXT_GOAL = os.getenv("ENABLE_NEXT_GOAL","1") == "1"
ENABLE_OVER      = os.getenv("ENABLE_OVER","1") == "1"
ENABLE_DNB       = os.getenv("ENABLE_DNB","1") == "1"
ENABLE_LATE_GOAL = os.getenv("ENABLE_LATE_GOAL","1") == "1"
ENABLE_UNDER     = os.getenv("ENABLE_UNDER","0") == "1"

# --- Küszöbök + xG opcionális kapcsolók ---
NG_DOM   = float(os.getenv("NG_DOM","1.35"))
NG_SHOTS = float(os.getenv("NG_SHOTS","1.30"))
NG_XG    = float(os.getenv("NG_XG","1.25"))
NG_REQUIRE_XG = os.getenv("NG_REQUIRE_XG","0") == "1"

OVER_MINUTE_START = int(os.getenv("OVER_MINUTE_START","42"))  # csak 42' után
OVER_XG_SUM       = float(os.getenv("OVER_XG_SUM","1.20"))
OVER_SHOTS_SUM    = int(os.getenv("OVER_SHOTS_SUM","4"))
OVER_REQUIRE_XG   = os.getenv("OVER_REQUIRE_XG","0") == "1"

DNB_DOM   = float(os.getenv("DNB_DOM","1.45"))
DNB_SHOTS = float(os.getenv("DNB_SHOTS","1.35"))
DNB_XG    = float(os.getenv("DNB_XG","1.25"))
DNB_REQUIRE_XG = os.getenv("DNB_REQUIRE_XG","0") == "1"

LATE_MINUTE_START = int(os.getenv("LATE_MINUTE_START","68"))
LATE_XG_SUM       = float(os.getenv("LATE_XG_SUM","1.60"))
LATE_SHOTS_SUM    = int(os.getenv("LATE_SHOTS_SUM","9"))
LATE_DA_RUN       = int(os.getenv("LATE_DA_RUN","10"))
LATE_REQUIRE_XG   = os.getenv("LATE_REQUIRE_XG","0") == "1"

SIGNAL_COOLDOWN_MIN = int(os.getenv("SIGNAL_COOLDOWN_MIN","7"))
MARKET_COOLDOWN_MIN = int(os.getenv("MARKET_COOLDOWN_MIN","10"))

# Piros lap szűrés
ENABLE_RED_CARD_FILTER = os.getenv("ENABLE_RED_CARD_FILTER","1") == "1"

# Napi jel-limit
MAX_SIGNALS_PER_DAY = int(os.getenv("MAX_SIGNALS_PER_DAY","60"))

# Odds megjelenítés módja: none | estimated
ODDS_MODE = os.getenv("ODDS_MODE","none").strip().lower()
if ODDS_MODE not in ("none","estimated"):
    ODDS_MODE = "none"

# Debug
DEBUG_LOG = os.getenv("DEBUG_LOG","1") == "1"
DEBUG_FILE = "logs/debug.csv"

tz = pytz.timezone(TIMEZONE)

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "events.csv")

# --- kíméletes leállítás ---
stop_flag = False
def _handle_term(sig, frm):
    global stop_flag
    stop_flag = True
signal.signal(signal.SIGTERM, _handle_term)
signal.signal(signal.SIGINT, _handle_term)

# --- Helpers ---
def now_str():
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

def today_date_str():
    return datetime.now(tz).strftime("%Y-%m-%d")

def _parse_hhmm(s):
    h,m = map(int, s.split(":"))
    return dtime(h,m)

def in_range(now_t, start_s, end_s):
    s, e = _parse_hhmm(start_s), _parse_hhmm(end_s)
    return s <= now_t <= e

def current_limits():
    now = datetime.now(tz).time()
    if not in_range(now, ACTIVE_HOURS_START, ACTIVE_HOURS_END):
        return 0, 0
    if in_range(now, PEAK_HOURS_START, PEAK_HOURS_END):
        return PEAK_POLL_SECONDS, PEAK_MAX_FIXTURES_PER_CYCLE
    return POLL_SECONDS, MAX_FIXTURES_PER_CYCLE

def send_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[{now_str()}] ERROR: Telegram token/chat_id hiányzik.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            print(f"[{now_str()}] Telegram hiba: {r.status_code} {r.text}")
            return False
        return True
    except Exception as e:
        print(f"[{now_str()}] Telegram kivétel: {e}")
        return False

# --- API-Football via RapidAPI ---
BASE_URL = "https://api-football-v1.p.rapidapi.com/v3"
def _rapidapi_headers():
    return {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}

def backoff_sleep(i):
    time.sleep(min(BACKOFF_MAX_SEC, 2 ** min(i,6)))

def fetch_live_fixtures():
    if not RAPIDAPI_KEY:
        return None, "Nincs RAPIDAPI_KEY – adatforrás nélkül futunk."
    url = f"{BASE_URL}/fixtures"
    params = {"live":"all"}
    headers = _rapidapi_headers()
    retry = 0
    while True:
        if stop_flag: return [], None
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 429:
                retry += 1
                backoff_sleep(retry)
                continue
            if r.status_code != 200:
                return None, f"API hiba: {r.status_code} {r.text}"
            return r.json().get("response", []), None
        except Exception as e:
            return None, f"API kivétel: {e}"

def fetch_statistics(fixture_id: int):
    if not RAPIDAPI_KEY:
        return None
    url = f"{BASE_URL}/fixtures/statistics"
    params = {"fixture": fixture_id}
    headers = _rapidapi_headers()
    retry = 0
    while True:
        if stop_flag: return None
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 429:
                retry += 1
                backoff_sleep(retry)
                continue
            if r.status_code != 200:
                return None
            return r.json().get("response", [])
        except Exception:
            return None

def fetch_red_card_flag(fixture_id: int):
    if not RAPIDAPI_KEY:
        return False
    url = f"{BASE_URL}/fixtures/events"
    params = {"fixture": fixture_id}
    headers = _rapidapi_headers()
    retry = 0
    while True:
        if stop_flag: return False
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 429:
                retry += 1
                backoff_sleep(retry)
                continue
            if r.status_code != 200:
                return False
            resp = r.json().get("response", [])
            for ev in resp:
                if ev.get("type") == "Card":
                    detail = (ev.get("detail") or "").lower()
                    if "red" in detail or "second yellow" in detail:
                        return True
            return False
        except Exception:
            return False

# --- Stat kulcs-fallback ---
STAT_ALIASES = {
    "Shots on Goal": ["Shots on Goal", "Shots on Target"],
    "Shots off Goal": ["Shots off Goal", "Shots Off Goal"],
    "Dangerous Attacks": ["Dangerous Attacks", "Dangerous attacks"],
    "Expected Goals": ["Expected Goals", "xG", "Expected goals", "Exp. Goals", "Exp Goals"],
    "Ball Possession": ["Ball Possession", "Possession", "Possession %"],
}
def extract_stat(stats_list, team_name: str, stat_key: str):
    if not stats_list: 
        return None
    keys = STAT_ALIASES.get(stat_key, [stat_key])
    for team_block in stats_list:
        team = team_block.get("team", {}).get("name", "")
        if team_name and team != team_name:
            continue
        for item in team_block.get("statistics", []):
            t = item.get("type")
            if t in keys:
                val = item.get("value")
                if isinstance(val, str) and val.endswith("%"):
                    try: return float(val.strip("%"))
                    except: return None
                try: return float(val)
                except: return None
    return None

# --- Meccs-szelekció ---
def select_top_fixtures(fixtures, limit):
    scored = []
    for fx in fixtures or []:
        fix = fx.get("fixture", {})
        goals = fx.get("goals", {})
        status_short = fix.get("status",{}).get("short")
        if status_short not in ("1H","HT","2H"):
            continue
        total_goals = (goals.get("home",0) or 0) + (goals.get("away",0) or 0)
        minute = fix.get("status",{}).get("elapsed") or 0
        low_goal_bias = 1 if total_goals <= 2 else 0
        mid_late_bias = 1 if 35 <= minute <= 90 else 0
        scored.append((low_goal_bias + mid_late_bias, fx))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [fx for _, fx in scored[:limit]]

# --- Cooldown, deduplikáció ---
last_stats_fetch = {}
last_signal_time = {}
last_market_time = {}
sent_hashes = set()

def can_fetch_stats(fid):
    last = last_stats_fetch.get(fid, 0)
    return (time.time() - last) >= STATS_COOLDOWN_MIN*60

def allow_signal(fid, market, side_or_kind, minute):
    now_t = time.time()
    if (now_t - last_signal_time.get(fid, 0)) < SIGNAL_COOLDOWN_MIN*60:
        return False
    if (now_t - last_market_time.get((fid, market), 0)) < MARKET_COOLDOWN_MIN*60:
        return False
    bucket = int(minute // 5) * 5
    h = (fid, market, side_or_kind, bucket)
    if h in sent_hashes:
        return False
    last_signal_time[fid] = now_t
    last_market_time[(fid, market)] = now_t
    sent_hashes.add(h)
    return True

# --- Debug napló ---
def debug_row(**kw):
    if not DEBUG_LOG: 
        return
    is_new = not os.path.exists(DEBUG_FILE)
    with open(DEBUG_FILE, "a", newline="", encoding="utf-8") as f:
        fields = ["ts","phase","fixture_id","minute","league","match","reason","metrics"]
        w = csv.DictWriter(f, fieldnames=fields)
        if is_new: w.writeheader()
        w.writerow({
            "ts": now_str(),
            "phase": kw.get("phase",""),
            "fixture_id": kw.get("fixture_id",""),
            "minute": kw.get("minute",""),
            "league": kw.get("league",""),
            "match": kw.get("match",""),
            "reason": kw.get("reason",""),
            "metrics": kw.get("metrics",""),
        })

def ratio(a, b): return (a + 1e-9) / (b + 1e-9)

# --- RED CARD cache, napi limit számláló ---
red_card_cache = {}  # fixture_id -> bool
def red_card_for_fixture(fid):
    if not ENABLE_RED_CARD_FILTER:
        return False
    if fid in red_card_cache:
        return red_card_cache[fid]
    flag = fetch_red_card_flag(fid)
    red_card_cache[fid] = flag
    return flag

def read_today_signal_count():
    try:
        path = f"data/{today_date_str()}/events.csv"
        if not os.path.exists(path):
            return 0
        c = 0
        with open(path, "r", encoding="utf-8") as f:
            first = True
            for line in f:
                if first:
                    first = False
                    continue
                if line.strip():
                    c += 1
        return c
    except Exception:
        return 0

def can_send_more_today(already_sent_local, cap):
    today_count = read_today_signal_count()
    return (today_count + already_sent_local) < cap

# --- Jelgenerálás (xG opcionális) ---
def gen_next_goal(fx, stats):
    if not ENABLE_NEXT_GOAL: return []
    fixture = fx.get("fixture", {})
    teams   = fx.get("teams", {})
    goals   = fx.get("goals", {})
    league  = fx.get("league", {})
    minute  = fixture.get("status",{}).get("elapsed",0) or 0
    fid     = fixture.get("id")

    if ENABLE_RED_CARD_FILTER and red_card_for_fixture(fid):
        debug_row(phase="NGA", fixture_id=fid, minute=minute,
                  league=f"{league.get('country','')} {league.get('name','')}",
                  match=f"{teams.get('home',{}).get('name','Home')} – {teams.get('away',{}).get('name','Away')}",
                  reason="red_card_block", metrics="")
        return []

    home = teams.get("home",{}).get("name","Home")
    away = teams.get("away",{}).get("name","Away")
    hg = goals.get("home",0) or 0
    ag = goals.get("away",0) or 0

    hs_on = extract_stat(stats, home, "Shots on Goal")
    as_on = extract_stat(stats, away, "Shots on Goal")
    hdatt = extract_stat(stats, home, "Dangerous Attacks")
    adatt = extract_stat(stats, away, "Dangerous Attacks")
    hxg   = extract_stat(stats, home, "Expected Goals")
    axg   = extract_stat(stats, away, "Expected Goals")

    dom   = ratio((hdatt or 0), (adatt or 0))
    shots = ratio((hs_on or 0), (as_on or 0))
    xg_ok = (hxg is not None and axg is not None)
    xgr   = ratio((hxg or 0.001), (axg or 0.001)) if xg_ok else None

    cond_home = (dom >= NG_DOM and shots >= NG_SHOTS and ((xgr is None) or (xgr >= NG_XG) or not NG_REQUIRE_XG))
    cond_away = (dom <= 1/NG_DOM and shots <= 1/NG_SHOTS and ((xgr is None) or (xgr <= 1/NG_XG) or not NG_REQUIRE_XG))

    out=[]
    if (xg_ok or not NG_REQUIRE_XG):
        if cond_home:
            side, pick, est_odds = "home","Következő gól – Hazai",1.82
        elif cond_away:
            side, pick, est_odds = "away","Következő gól – Vendég",1.92
        else:
            debug_row(phase="NGA", fixture_id=fid, minute=minute,
                      league=f"{league.get('country','')} {league.get('name','')}",
                      match=f"{home} – {away}",
                      reason="threshold_fail",
                      metrics=f"dom={dom:.2f}, shots={shots:.2f}, xgr={('n/a' if xgr is None else f'{xgr:.2f}')}")
            return []
        prob = 0.58 + 0.25*min(1,(dom-1)/0.6) + 0.25*min(1,(shots-1)/0.6)
        if xgr is not None: prob += 0.20*min(1,(xgr-1)/0.5)
        prob = max(0.58, min(0.9, prob))
        out.append({
            "market":"NEXT_GOAL","league":f"{league.get('country','')} {league.get('name','')}",
            "match":f"{home} – {away}","minute":minute,"score":f"{hg}:{ag}",
            "pick":pick,"prob":round(prob*100,1),"odds":est_odds,
            "fixture_id":fid,"side":side,
            "details":{"dom":round(dom,2),"shots":round(shots,2),"xgr":(None if xgr is None else round(xgr,2))}
        })
    else:
        debug_row(phase="NGA", fixture_id=fid, minute=minute,
                  league=f"{league.get('country','')} {league.get('name','')}",
                  match=f"{home} – {away}",
                  reason="missing_xg_required",
                  metrics=f"dom={dom:.2f}, shots={shots:.2f}")
    return out

def gen_over(fx, stats):
    if not ENABLE_OVER: return []
    fixture = fx.get("fixture", {})
    teams   = fx.get("teams", {})
    goals   = fx.get("goals", {})
    league  = fx.get("league", {})
    status_short = fixture.get("status",{}).get("short")
    minute  = fixture.get("status",{}).get("elapsed",0) or 0

    if status_short == "HT" or minute < OVER_MINUTE_START:
        return []

    fid = fixture.get("id")
    if ENABLE_RED_CARD_FILTER and red_card_for_fixture(fid):
        debug_row(phase="OVER", fixture_id=fid, minute=minute,
                  league=f"{league.get('country','')} {league.get('name','')}",
                  match=f"{teams.get('home',{}).get('name','Home')} – {teams.get('away',{}).get('name','Away')}",
                  reason="red_card_block", metrics="")
        return []

    home = teams.get("home",{}).get("name","Home")
    away = teams.get("away",{}).get("name","Away")
    hg = goals.get("home",0) or 0
    ag = goals.get("away",0) or 0

    hxg = extract_stat(stats, home, "Expected Goals")
    axg = extract_stat(stats, away, "Expected Goals")
    hs_on = extract_stat(stats, home, "Shots on Goal") or 0
    as_on = extract_stat(stats, away, "Shots on Goal") or 0
    hs_off = extract_stat(stats, home, "Shots off Goal") or 0
    as_off = extract_stat(stats, away, "Shots off Goal") or 0

    xg_sum = (hxg or 0) + (axg or 0)
    shots_sum = (hs_on + as_on) + (hs_off + as_off)

    cond_xg = (xg_sum >= OVER_XG_SUM) if (hxg is not None and axg is not None) else True if not OVER_REQUIRE_XG else False
    if cond_xg and shots_sum >= OVER_SHOTS_SUM:
        est_odds = 1.55 if (hg+ag) <= 1 else 1.85
        return [{
            "market":"OVER","league":f"{league.get('country','')} {league.get('name','')}",
            "match":f"{home} – {away}","minute":minute,"score":f"{hg}:{ag}",
            "pick":"Over (live) – gól piacon","prob":69.0,"odds":est_odds,
            "fixture_id":fid,"side":"over",
            "details":{"xg_sum":(None if hxg is None or axg is None else round(xg_sum,2)), "shots_sum":shots_sum}
        }]
    else:
        debug_row(phase="OVER", fixture_id=fid, minute=minute,
                  league=f"{league.get('country','')} {league.get('name','')}",
                  match=f"{home} – {away}",
                  reason="threshold_fail",
                  metrics=f"xg_sum={('n/a' if hxg is None or axg is None else f'{xg_sum:.2f}')}, shots_sum={shots_sum}")
    return []

def gen_dnb(fx, stats):
    if not ENABLE_DNB: return []
    fixture = fx.get("fixture", {})
    teams   = fx.get("teams", {})
    goals   = fx.get("goals", {})
    league  = fx.get("league", {})
    minute  = fixture.get("status",{}).get("elapsed",0) or 0

    home = teams.get("home",{}).get("name","Home")
    away = teams.get("away",{}).get("name","Away")
    hg = goals.get("home",0) or 0
    ag = goals.get("away",0) or 0

    hs_on = extract_stat(stats, home, "Shots on Goal")
    as_on = extract_stat(stats, away, "Shots on Goal")
    hdatt = extract_stat(stats, home, "Dangerous Attacks")
    adatt = extract_stat(stats, away, "Dangerous Attacks")
    hxg   = extract_stat(stats, home, "Expected Goals")
    axg   = extract_stat(stats, away, "Expected Goals")

    dom   = ratio((hdatt or 0), (adatt or 0))
    shots = ratio((hs_on or 0), (as_on or 0))
    xgr   = ratio((hxg or 0.001), (axg or 0.001)) if (hxg is not None and axg is not None) else None

    cond_home = (hg < ag) and (dom >= DNB_DOM and shots >= DNB_SHOTS and ((xgr is None) or (xgr >= DNB_XG) or not DNB_REQUIRE_XG))
    cond_away = (ag < hg) and (dom <= 1/DNB_DOM and shots <= 1/DNB_SHOTS and ((xgr is None) or (xgr <= 1/DNB_XG) or not DNB_REQUIRE_XG))

    out=[]
    if cond_home:
        out.append({
            "market":"DNB","league":f"{league.get('country','')} {league.get('name','')}",
            "match":f"{home} – {away}","minute":minute,"score":f"{hg}:{ag}",
            "pick":"Hazai DNB","prob":68.0,"odds":1.72,"fixture_id":fixture.get("id"),
            "side":"home",
            "details":{"dom":round(dom,2),"shots":round(shots,2),"xgr":(None if xgr is None else round(xgr,2))}
        })
    elif cond_away:
        out.append({
            "market":"DNB","league":f"{league.get('country','')} {league.get('name','')}",
            "match":f"{home} – {away}","minute":minute,"score":f"{hg}:{ag}",
            "pick":"Vendég DNB","prob":68.0,"odds":1.82,"fixture_id":fixture.get("id"),
            "side":"away",
            "details":{"dom":round(1/dom,2),"shots":round(1/shots,2),"xgr":(None if xgr is None else round(1/xgr,2))}
        })
    else:
        debug_row(phase="DNB", fixture_id=fixture.get("id"), minute=minute,
                  league=f"{league.get('country','')} {league.get('name','')}",
                  match=f"{home} – {away}",
                  reason="threshold_fail",
                  metrics=f"dom={dom:.2f}, shots={shots:.2f}, xgr={('n/a' if xgr is None else f'{xgr:.2f}')}")
    return out

def gen_late_goal(fx, stats):
    if not ENABLE_LATE_GOAL: return []
    fixture = fx.get("fixture", {})
    teams   = fx.get("teams", {})
    goals   = fx.get("goals", {})
    league  = fx.get("league", {})
    minute  = fixture.get("status",{}).get("elapsed",0) or 0
    if minute < LATE_MINUTE_START: 
        return []

    home = teams.get("home",{}).get("name","Home")
    away = teams.get("away",{}).get("name","Away")
    hg = goals.get("home",0) or 0
    ag = goals.get("away",0) or 0

    hxg = extract_stat(stats, home, "Expected Goals")
    axg = extract_stat(stats, away, "Expected Goals")
    hs_on = extract_stat(stats, home, "Shots on Goal") or 0
    as_on = extract_stat(stats, away, "Shots on Goal") or 0
    hs_off = extract_stat(stats, home, "Shots off Goal") or 0
    as_off = extract_stat(stats, away, "Shots off Goal") or 0
    hdatt = extract_stat(stats, home, "Dangerous Attacks") or 0
    adatt = extract_stat(stats, away, "Dangerous Attacks") or 0

    xg_sum = (hxg or 0) + (axg or 0)
    shots_sum = (hs_on + as_on) + (hs_off + as_off)
    da_run = (hdatt + adatt)

    cond_xg = (xg_sum >= LATE_XG_SUM) if (hxg is not None and axg is not None) else True if not LATE_REQUIRE_XG else False
    if cond_xg and shots_sum >= LATE_SHOTS_SUM and da_run >= LATE_DA_RUN:
        dom = ratio(hdatt, adatt)
        if dom >= 1.2:
            side = "home"; pick = "Következő gól – Hazai (Late)"
        elif dom <= (1/1.2):
            side = "away"; pick = "Következő gól – Vendég (Late)"
        else:
            side = "over"; pick = "Over 0.5 (Late)"
        est_odds = 1.68 if side != "over" else 1.62

        return [{
            "market":"LATE_GOAL",
            "league":f"{league.get('country','')} {league.get('name','')}",
            "match":f"{home} – {away}",
            "minute":minute,
            "score":f"{hg}:{ag}",
            "pick":pick,
            "prob":74.0,
            "odds":est_odds,
            "fixture_id":fixture.get("id"),
            "side":side,
            "details":{"xg_sum":(None if hxg is None or axg is None else round(xg_sum,2)),
                       "shots_sum":shots_sum,"da_run":da_run,"dom":round(dom,2)}
        }]
    else:
        debug_row(phase="LATE", fixture_id=fixture.get("id"), minute=minute,
                  league=f"{league.get('country','')} {league.get('name','')}",
                  match=f"{home} – {away}",
                  reason="threshold_fail",
                  metrics=f"xg_sum={('n/a' if hxg is None or axg is None else f'{xg_sum:.2f}')}, shots_sum={shots_sum}, da_run={da_run}")
    return []

def merge_signals(fx, stats):
    out = []
    out += gen_next_goal(fx, stats)
    out += gen_over(fx, stats)
    out += gen_dnb(fx, stats)
    out += gen_late_goal(fx, stats)
    return out

# --- Log ---
def log_event(row: dict):
    is_new = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "time","league","match","minute","score","pick","prob","odds","fixture_id","details","market"
        ])
        if is_new:
            w.writeheader()
        w.writerow({
            "time": now_str(),
            "league": row.get("league",""),
            "match": row.get("match",""),
            "minute": row.get("minute",""),
            "score": row.get("score",""),
            "pick": row.get("pick",""),
            "prob": row.get("prob",""),
            "odds": row.get("odds",""),
            "fixture_id": row.get("fixture_id",""),
            "details": row.get("details",""),
            "market": row.get("market","")
        })

def main():
    if SEND_ONLINE_ON_START:
        send_message(f"✅ <b>LiveMesterBot (TEST) online</b>\n🕒 {now_str()}")

    start_time = time.time()
    already_sent_local = 0

    while True:
        if stop_flag:
            break

        poll, max_fx = current_limits()
        if poll == 0:
            time.sleep(60)
            if RUN_MINUTES > 0 and (time.time() - start_time) >= RUN_MINUTES * 60:
                break
            continue

        fixtures, err = fetch_live_fixtures()
        if fixtures is None and err:
            debug_row(phase="FETCH_FIX", fixture_id="", minute="", reason="api_error", metrics=str(err))
        elif fixtures is not None:
            debug_row(phase="FETCH_FIX", fixture_id="", minute="", reason="ok", metrics=f"fixtures={len(fixtures)}")

        fixtures_with_stats = []
        if fixtures:
            chosen = select_top_fixtures(fixtures, max_fx)
            debug_row(phase="SELECT", fixture_id="", minute="", reason="chosen", metrics=f"{len(chosen)}/{len(fixtures)} selected (limit {max_fx})")
            for fx in chosen:
                if stop_flag: break
                fid = fx.get("fixture",{}).get("id")
                if not fid: continue
                if not can_fetch_stats(fid):
                    debug_row(phase="STATS", fixture_id=fid, minute=fx.get("fixture",{}).get("status",{}).get("elapsed",0), reason="cooldown_skip", metrics="")
                    continue
                stats = fetch_statistics(fid)
                if stats is not None:
                    last_stats_fetch[fid] = time.time()
                    fixtures_with_stats.append((fx, stats))
                    debug_row(phase="STATS", fixture_id=fid, minute=fx.get("fixture",{}).get("status",{}).get("elapsed",0), reason="ok", metrics="got_stats")
                else:
                    debug_row(phase="STATS", fixture_id=fid, minute=fx.get("fixture",{}).get("status",{}).get("elapsed",0), reason="stats_none", metrics="")

        signals = []
        for fx, stats in fixtures_with_stats:
            if stop_flag: break
            fixture = fx.get("fixture",{})
            minute = fixture.get("status",{}).get("elapsed",0) or 0
            fid = fixture.get("id")
            for s in merge_signals(fx, stats):
                market = s["market"]
                side_or_kind = s.get("side", market)
                if not can_send_more_today(already_sent_local, MAX_SIGNALS_PER_DAY):
                    debug_row(phase="ALLOW", fixture_id=fid, minute=minute, reason="daily_cap_reached", metrics=f"MAX={MAX_SIGNALS_PER_DAY}")
                    continue
                if allow_signal(fid, market, side_or_kind, minute):
                    signals.append(s)
                else:
                    debug_row(phase="ALLOW", fixture_id=fid, minute=minute, reason="cooldown_dedupe_block", metrics=f"{market}/{side_or_kind}")

        if signals:
            priority = {"LATE_GOAL":1, "NEXT_GOAL":2, "DNB":3, "OVER":4, "UNDER":5}
            signals.sort(key=lambda x: (priority.get(x["market"], 9), -x["prob"]))
            for s in signals:
                if not can_send_more_today(already_sent_local, MAX_SIGNALS_PER_DAY):
                    debug_row(phase="SEND", fixture_id=s["fixture_id"], minute=s["minute"], reason="daily_cap_reached", metrics=f"MAX={MAX_SIGNALS_PER_DAY}")
                    continue
                # --- Üzenet összeállítás (ODDS_MODE-ot figyelembe véve) ---
                odds_line = ""
                if ODDS_MODE == "estimated" and ("odds" in s) and (s["odds"] is not None):
                    odds_line = f"\n💰 <b>Odds</b>: {s['odds']}"
                msg = (
                    f"⚡ <b>{s['market'].replace('_',' ')} ALERT</b>\n"
                    f"🏟️ <b>Meccs</b>: {s['match']} ({s['score']}, {s['minute']}' )\n"
                    f"🏆 <b>Liga</b>: {s['league']}\n"
                    f"🎯 <b>Tipp</b>: {s['pick']}\n"
                    f"📈 <b>Esély</b>: {s['prob']}%{odds_line}\n"
                )
                send_message(msg)
                log_event(s)
                already_sent_local += 1
        else:
            debug_row(phase="SIGNALS", fixture_id="", minute="", reason="none_after_eval", metrics=f"fx_stats={len(fixtures_with_stats)}")

        if RUN_MINUTES > 0 and (time.time() - start_time) >= RUN_MINUTES * 60:
            break

        for _ in range(int(max(1, poll))):
            if stop_flag:
                break
            time.sleep(1)
        if stop_flag:
            break

if __name__ == "__main__":
    main()
