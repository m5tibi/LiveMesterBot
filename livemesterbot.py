import os
import time
import csv
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

POLL_SECONDS = int(os.getenv("POLL_SECONDS","150"))           # 2.5 perc
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
RUN_MINUTES = int(os.getenv("RUN_MINUTES", "10"))

# --- Piacok engedélyezése ---
ENABLE_NEXT_GOAL = os.getenv("ENABLE_NEXT_GOAL","1") == "1"
ENABLE_OVER      = os.getenv("ENABLE_OVER","1") == "1"
ENABLE_DNB       = os.getenv("ENABLE_DNB","1") == "1"
ENABLE_LATE_GOAL = os.getenv("ENABLE_LATE_GOAL","1") == "1"
ENABLE_UNDER     = os.getenv("ENABLE_UNDER","0") == "1"

# --- Küszöbök (ÉRZÉKENYEBBRE VÉVE) ---
NG_DOM   = float(os.getenv("NG_DOM","1.5"))   # 1.6 -> 1.5
NG_SHOTS = float(os.getenv("NG_SHOTS","1.4")) # 1.5 -> 1.4
NG_XG    = float(os.getenv("NG_XG","1.3"))    # 1.4 -> 1.3

OVER_MINUTE_START = int(os.getenv("OVER_MINUTE_START","45"))
OVER_XG_SUM       = float(os.getenv("OVER_XG_SUM","1.4"))  # 1.6 -> 1.4
OVER_SHOTS_SUM    = int(os.getenv("OVER_SHOTS_SUM","5"))   # 6 -> 5

DNB_DOM   = float(os.getenv("DNB_DOM","1.5")) # 1.6 -> 1.5
DNB_SHOTS = float(os.getenv("DNB_SHOTS","1.4"))
DNB_XG    = float(os.getenv("DNB_XG","1.3"))

LATE_MINUTE_START = int(os.getenv("LATE_MINUTE_START","70"))
LATE_XG_SUM       = float(os.getenv("LATE_XG_SUM","1.8"))  # 2.0 -> 1.8
LATE_SHOTS_SUM    = int(os.getenv("LATE_SHOTS_SUM","10"))  # 12 -> 10
LATE_DA_RUN       = int(os.getenv("LATE_DA_RUN","12"))     # 15 -> 12

# Anti-spam
SIGNAL_COOLDOWN_MIN = int(os.getenv("SIGNAL_COOLDOWN_MIN","7"))
MARKET_COOLDOWN_MIN = int(os.getenv("MARKET_COOLDOWN_MIN","10"))

tz = pytz.timezone(TIMEZONE)

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "events.csv")

# --- Helpers ---
def now_str():
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

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
    time.sleep(min(300, 2 ** min(i,6)))

def fetch_live_fixtures():
    if not RAPIDAPI_KEY:
        return None, "Nincs RAPIDAPI_KEY – adatforrás nélkül futunk."
    url = f"{BASE_URL}/fixtures"
    params = {"live":"all"}
    headers = _rapidapi_headers()
    retry = 0
    while True:
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

# --- Stat kulcs-fallback (API név-eltérések kezelése) ---
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

# --- Meccs-szelekció a stat-kérésekhez ---
def select_top_fixtures(fixtures, limit):
    scored = []
    for fx in fixtures or []:
        fix = fx.get("fixture", {})
        goals = fx.get("goals", {})
        status = fix.get("status",{}).get("short")
        if status not in ("1H","HT","2H"):
            continue
        total_goals = (goals.get("home",0) or 0) + (goals.get("away",0) or 0)
        minute = fix.get("status",{}).get("elapsed") or 0
        low_goal_bias = 1 if total_goals <= 2 else 0   # 0–2 gól preferált
        mid_late_bias = 1 if 40 <= minute <= 85 else 0
        scored.append((low_goal_bias + mid_late_bias, fx))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [fx for _, fx in scored[:limit]]

# --- Cooldown, deduplikáció ---
last_stats_fetch = {}      # fixture_id -> ts
last_signal_time = {}      # (fixture_id) -> ts
last_market_time = {}      # (fixture_id, market) -> ts
sent_hashes = set()        # { (fixture_id, market, side_or_kind, minute_bucket) }

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

# --- Jelgenerálás ---
def gen_next_goal(fx, stats, thresholds):
    if not ENABLE_NEXT_GOAL: return []
    fixture = fx.get("fixture", {})
    teams   = fx.get("teams", {})
    goals   = fx.get("goals", {})
    league  = fx.get("league", {})
    minute  = fixture.get("status",{}).get("elapsed",0) or 0

    home = teams.get("home",{}).get("name","Home")
    away = teams.get("away",{}).get("name","Away")
    hg = goals.get("home",0) or 0
    ag = goals.get("away",0) or 0

    hs_on = extract_stat(stats, home, "Shots on Goal") or 0
    as_on = extract_stat(stats, away, "Shots on Goal") or 0
    hdatt = extract_stat(stats, home, "Dangerous Attacks") or 0
    adatt = extract_stat(stats, away, "Dangerous Attacks") or 0
    hxg   = extract_stat(stats, home, "Expected Goals") or 0
    axg   = extract_stat(stats, away, "Expected Goals") or 0

    dom   = (hdatt + 1) / (adatt + 1)
    shots = (hs_on + 1) / (as_on + 1)
    xgr   = (hxg + 0.01) / (axg + 0.01)

    DOM, SHOTS, XG = thresholds
    if minute >= 60:
        DOM += 0.05; SHOTS += 0.05  # kicsit szigorúbb 60' után

    picks = []
    if dom >= DOM and shots >= SHOTS and xgr >= XG:
        side = "home"; pick = "Következő gól – Hazai"; est_odds = 1.85
    elif dom <= 1/DOM and shots <= 1/SHOTS and xgr <= 1/XG:
        side = "away"; pick = "Következő gól – Vendég"; est_odds = 1.95
    else:
        return []

    # egyszerűsített prob
    prob = 0.57 + 0.33 * min(1.0, (dom-1)/1) * 0.4 \
                 + 0.33 * min(1.0, (shots-1)/1) * 0.3 \
                 + 0.33 * min(1.0, (xgr-1)/1) * 0.3
    prob = max(0.57, min(0.9, prob))

    picks.append({
        "market": "NEXT_GOAL",
        "league": f"{league.get('country','')} {league.get('name','')}",
        "match": f"{home} – {away}",
        "minute": minute,
        "score": f"{hg}:{ag}",
        "pick": pick,
        "prob": round(prob*100,1),
        "odds": est_odds,
        "fixture_id": fixture.get("id"),
        "side": side,
        "details": {
            "dominance": round(dom,2),
            "shots_ratio": round(shots,2),
            "xg_ratio": round(xgr,2),
            "home_xg": hxg, "away_xg": axg,
            "home_shots_on": hs_on, "away_shots_on": as_on,
            "home_datt": hdatt, "away_datt": adatt
        }
    })
    return picks

def gen_over(fx, stats):
    if not ENABLE_OVER: return []
    fixture = fx.get("fixture", {})
    teams   = fx.get("teams", {})
    goals   = fx.get("goals", {})
    league  = fx.get("league", {})
    minute  = fixture.get("status",{}).get("elapsed",0) or 0
    if minute < OVER_MINUTE_START: return []

    home = teams.get("home",{}).get("name","Home")
    away = teams.get("away",{}).get("name","Away")
    hg = goals.get("home",0) or 0
    ag = goals.get("away",0) or 0

    hxg = extract_stat(stats, home, "Expected Goals") or 0
    axg = extract_stat(stats, away, "Expected Goals") or 0
    hs_on = extract_stat(stats, home, "Shots on Goal") or 0
    as_on = extract_stat(stats, away, "Shots on Goal") or 0
    hs_off = extract_stat(stats, home, "Shots off Goal") or 0
    as_off = extract_stat(stats, away, "Shots off Goal") or 0

    xg_sum = hxg + axg
    shots_sum = (hs_on + as_on) + (hs_off + as_off)

    if xg_sum >= OVER_XG_SUM and shots_sum >= OVER_SHOTS_SUM:
        est_odds = 1.60 if (hg+ag) <= 1 else 1.90
        return [{
            "market": "OVER",
            "league": f"{league.get('country','')} {league.get('name','')}",
            "match": f"{home} – {away}",
            "minute": minute,
            "score": f"{hg}:{ag}",
            "pick": "Over (live) – gól piacon",
            "prob": 70.0,
            "odds": est_odds,
            "fixture_id": fixture.get("id"),
            "side": "over",
            "details": {"xg_sum": round(xg_sum,2), "shots_sum": shots_sum}
        }]
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

    hs_on = extract_stat(stats, home, "Shots on Goal") or 0
    as_on = extract_stat(stats, away, "Shots on Goal") or 0
    hdatt = extract_stat(stats, home, "Dangerous Attacks") or 0
    adatt = extract_stat(stats, away, "Dangerous Attacks") or 0
    hxg   = extract_stat(stats, home, "Expected Goals") or 0
    axg   = extract_stat(stats, away, "Expected Goals") or 0

    dom   = (hdatt + 1) / (adatt + 1)
    shots = (hs_on + 1) / (as_on + 1)
    xgr   = (hxg + 0.01) / (axg + 0.01)

    picks=[]
    # hátrányból domináló oldal DNB
    if (hg < ag) and (dom >= DNB_DOM and shots >= DNB_SHOTS and xgr >= DNB_XG):
        picks.append({
            "market":"DNB","league":f"{league.get('country','')} {league.get('name','')}",
            "match":f"{home} – {away}","minute":minute,"score":f"{hg}:{ag}",
            "pick":"Hazai DNB","prob":68.0,"odds":1.75,"fixture_id":fixture.get("id"),
            "side":"home",
            "details":{"dominance":round(dom,2),"shots_ratio":round(shots,2),"xg_ratio":round(xgr,2)}
        })
    if (ag < hg) and (1/dom >= DNB_DOM and 1/shots >= DNB_SHOTS and 1/xgr >= DNB_XG):
        picks.append({
            "market":"DNB","league":f"{league.get('country','')} {league.get('name','')}",
            "match":f"{home} – {away}","minute":minute,"score":f"{hg}:{ag}",
            "pick":"Vendég DNB","prob":68.0,"odds":1.85,"fixture_id":fixture.get("id"),
            "side":"away",
            "details":{"dominance":round(1/dom,2),"shots_ratio":round(1/shots,2),"xg_ratio":round(1/xgr,2)}
        })
    return picks

def gen_late_goal(fx, stats):
    if not ENABLE_LATE_GOAL: return []
    fixture = fx.get("fixture", {})
    teams   = fx.get("teams", {})
    goals   = fx.get("goals", {})
    league  = fx.get("league", {})
    minute  = fixture.get("status",{}).get("elapsed",0) or 0
    if minute < LATE_MINUTE_START: return []

    home = teams.get("home",{}).get("name","Home")
    away = teams.get("away",{}).get("name","Away")
    hg = goals.get("home",0) or 0
    ag = goals.get("away",0) or 0

    hxg = extract_stat(stats, home, "Expected Goals") or 0
    axg = extract_stat(stats, away, "Expected Goals") or 0
    hs_on = extract_stat(stats, home, "Shots on Goal") or 0
    as_on = extract_stat(stats, away, "Shots on Goal") or 0
    hs_off = extract_stat(stats, home, "Shots off Goal") or 0
    as_off = extract_stat(stats, away, "Shots off Goal") or 0
    hdatt = extract_stat(stats, home, "Dangerous Attacks") or 0
    adatt = extract_stat(stats, away, "Dangerous Attacks") or 0

    xg_sum = hxg + axg
    shots_sum = (hs_on + as_on) + (hs_off + as_off)
    da_run = (hdatt + adatt)

    if xg_sum >= LATE_XG_SUM and shots_sum >= LATE_SHOTS_SUM and da_run >= LATE_DA_RUN:
        dom = (hdatt + 1) / (adatt + 1)
        if dom >= 1.2:
            side = "home"; pick = "Következő gól – Hazai (Late)"
        elif dom <= (1/1.2):
            side = "away"; pick = "Következő gól – Vendég (Late)"
        else:
            side = "over"; pick = "Over 0.5 (Late)"
        est_odds = 1.70 if side != "over" else 1.65

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
            "details":{"xg_sum":round(xg_sum,2),"shots_sum":shots_sum,"da_run":da_run,"dom":round(dom,2)}
        }]
    return []

def merge_signals(fx, stats):
    out = []
    out += gen_next_goal(fx, stats, (NG_DOM, NG_SHOTS, NG_XG))
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

    while True:
        poll, max_fx = current_limits()
        if poll == 0:
            time.sleep(60)
            if RUN_MINUTES > 0 and (time.time() - start_time) >= RUN_MINUTES * 60:
                break
            continue

        fixtures, err = fetch_live_fixtures()
        if err and not MUTE_NO_SIGNAL:
            send_message(f"ℹ️ <b>Info</b>: {err}")

        fixtures_with_stats = []
        if fixtures:
            chosen = select_top_fixtures(fixtures, max_fx)
            for fx in chosen:
                fid = fx.get("fixture",{}).get("id")
                if not fid: 
                    continue
                if not can_fetch_stats(fid):
                    continue
                stats = fetch_statistics(fid)
                if stats is not None:
                    last_stats_fetch[fid] = time.time()
                    fixtures_with_stats.append((fx, stats))

        signals = []
        for fx, stats in fixtures_with_stats:
            fixture = fx.get("fixture",{})
            minute = fixture.get("status",{}).get("elapsed",0) or 0
            fid = fixture.get("id")
            for s in merge_signals(fx, stats):
                market = s["market"]
                side_or_kind = s.get("side", market)
                if allow_signal(fid, market, side_or_kind, minute):
                    signals.append(s)

        if signals:
            priority = {"LATE_GOAL":1, "NEXT_GOAL":2, "DNB":3, "OVER":4, "UNDER":5}
            signals.sort(key=lambda x: (priority.get(x["market"], 9), -x["prob"]))
            for s in signals:
                msg = (
                    f"⚡ <b>{s['market'].replace('_',' ')} ALERT</b>\n"
                    f"🏟️ <b>Meccs</b>: {s['match']} ({s['score']}, {s['minute']}' )\n"
                    f"🏆 <b>Liga</b>: {s['league']}\n"
                    f"🎯 <b>Tipp</b>: {s['pick']}\n"
                    f"📈 <b>Esély</b>: {s['prob']}%\n"
                    f"💰 <b>Odds</b>: {s['odds']}\n"
                )
                send_message(msg)
                log_event(s)
        else:
            if not MUTE_NO_SIGNAL:
                send_message(f"💤 Nincs erős jel ebben a ciklusban. ({now_str()})")

        if RUN_MINUTES > 0 and (time.time() - start_time) >= RUN_MINUTES * 60:
            break

        time.sleep(poll)

if __name__ == "__main__":
    main()
