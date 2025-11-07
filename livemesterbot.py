import os
import time
import math
import csv
import requests
from dotenv import load_dotenv
from datetime import datetime, time as dtime
import pytz

load_dotenv()

# --- ENV ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN","").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID","").strip()

RAPIDAPI_KEY  = os.getenv("RAPIDAPI_KEY","").strip()
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST","api-football-v1.p.rapidapi.com").strip()

POLL_SECONDS = int(os.getenv("POLL_SECONDS","180"))
ACTIVE_HOURS_START = os.getenv("ACTIVE_HOURS_START","10:00")
ACTIVE_HOURS_END   = os.getenv("ACTIVE_HOURS_END","23:00")
PEAK_HOURS_START   = os.getenv("PEAK_HOURS_START","18:00")
PEAK_HOURS_END     = os.getenv("PEAK_HOURS_END","22:00")
PEAK_POLL_SECONDS  = int(os.getenv("PEAK_POLL_SECONDS","90"))
MAX_FIXTURES_PER_CYCLE = int(os.getenv("MAX_FIXTURES_PER_CYCLE","10"))
PEAK_MAX_FIXTURES_PER_CYCLE = int(os.getenv("PEAK_MAX_FIXTURES_PER_CYCLE","15"))
STATS_COOLDOWN_MIN = int(os.getenv("STATS_COOLDOWN_MIN","6"))

TIMEZONE = os.getenv("TIMEZONE","Europe/Budapest")

# ÚJ: némítás és időzített kilépés
MUTE_NO_SIGNAL = os.getenv("MUTE_NO_SIGNAL", "1") == "1"       # 1 = ne küldjön 'nincs jel'/'nincs adat' üzenetet
SEND_ONLINE_ON_START = os.getenv("SEND_ONLINE_ON_START", "1") == "1"
RUN_MINUTES = int(os.getenv("RUN_MINUTES", "10"))              # 0 = végtelen; Actions-hez javasolt 10

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
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
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
    return {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }

def fetch_live_fixtures():
    if not RAPIDAPI_KEY:
        return None, "Nincs RAPIDAPI_KEY – adatforrás nélkül futunk (demo/heartbeat mód)."
    url = f"{BASE_URL}/fixtures"
    params = {"live":"all"}
    headers = _rapidapi_headers()
    retry_count = 0
    while True:
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 429:
                retry_count += 1
                backoff_sleep(retry_count)
                continue
            if r.status_code != 200:
                return None, f"API hiba: {r.status_code} {r.text}"
            data = r.json()
            return data.get("response", []), None
        except Exception as e:
            return None, f"API kivétel: {e}"

def fetch_statistics(fixture_id: int):
    if not RAPIDAPI_KEY:
        return None
    url = f"{BASE_URL}/fixtures/statistics"
    params = {"fixture": fixture_id}
    headers = _rapidapi_headers()
    retry_count = 0
    while True:
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 429:
                retry_count += 1
                backoff_sleep(retry_count)
                continue
            if r.status_code != 200:
                return None
            return r.json().get("response", [])
        except Exception:
            return None

def backoff_sleep(i):
    time.sleep(min(300, 2 ** min(i,6)))

def extract_stat(stats_list, team_name: str, stat_key: str):
    if not stats_list: 
        return None
    for team_block in stats_list:
        team = team_block.get("team", {}).get("name", "")
        if team_name and team != team_name:
            continue
        for item in team_block.get("statistics", []):
            if item.get("type") == stat_key:
                val = item.get("value")
                if isinstance(val, str) and val.endswith("%"):
                    try:
                        return float(val.strip("%"))
                    except:
                        return None
                try:
                    return float(val)
                except:
                    return None
    return None

def select_top_fixtures(fixtures, limit):
    scored = []
    for fx in fixtures or []:
        fix = fx.get("fixture", {})
        teams = fx.get("teams", {})
        goals = fx.get("goals", {})
        status = fix.get("status",{}).get("short")
        if status not in ("1H","HT","2H"):
            continue
        total_goals = (goals.get("home",0) or 0) + (goals.get("away",0) or 0)
        # alacsony gólszám preferencia
        score_pref = 1 if total_goals <= 1 else 0
        scored.append((score_pref, fx))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [fx for _, fx in scored[:limit]]

last_stats_fetch = {}  # fixture_id -> timestamp

def can_fetch_stats(fid):
    last = last_stats_fetch.get(fid, 0)
    return (time.time() - last) >= STATS_COOLDOWN_MIN*60

def generate_signals(fixtures_with_stats):
    signals = []
    for fx, stats in fixtures_with_stats:
        fixture = fx.get("fixture", {})
        teams = fx.get("teams", {})
        goals = fx.get("goals", {})
        league = fx.get("league", {})

        status = fixture.get("status", {}).get("short", "")
        minute = fixture.get("status", {}).get("elapsed", 0) or 0
        if status not in ("1H", "HT", "2H"):
            continue

        home = teams.get("home", {}).get("name", "Home")
        away = teams.get("away", {}).get("name", "Away")
        home_goals = goals.get("home", 0) or 0
        away_goals = goals.get("away", 0) or 0

        home_shots_on = extract_stat(stats, home, "Shots on Goal") or 0
        away_shots_on = extract_stat(stats, away, "Shots on Goal") or 0
        home_dattacks = extract_stat(stats, home, "Dangerous Attacks") or 0
        away_dattacks = extract_stat(stats, away, "Dangerous Attacks") or 0
        home_xg = extract_stat(stats, home, "Expected Goals") or 0
        away_xg = extract_stat(stats, away, "Expected Goals") or 0
        possession_home = extract_stat(stats, home, "Ball Possession") or 0
        possession_away = extract_stat(stats, away, "Ball Possession") or 0

        dominance = (home_dattacks + 1) / (away_dattacks + 1)
        shots_ratio = (home_shots_on + 1) / (away_shots_on + 1)
        xg_ratio = (home_xg + 0.01) / (away_xg + 0.01)

        pick = None
        side = None
        if dominance >= 1.6 and shots_ratio >= 1.5 and xg_ratio >= 1.4:
            pick = "Következő gól – Hazai"
            side = "home"
        elif dominance <= (1/1.6) and shots_ratio <= (1/1.5) and xg_ratio <= (1/1.4):
            pick = "Következő gól – Vendég"
            side = "away"
        else:
            continue

        est_odds = 1.85 if side == "home" else 1.95

        score = 0
        score += min(1.0, (dominance - 1.0) / 1.0) * 0.4
        score += min(1.0, (shots_ratio - 1.0) / 1.0) * 0.3
        score += min(1.0, (xg_ratio - 1.0) / 1.0) * 0.3
        prob = 0.55 + 0.35 * score  # 55–90%

        signals.append({
            "league": f"{league.get('country','')} {league.get('name','')}",
            "match": f"{home} – {away}",
            "minute": minute,
            "score": f"{home_goals}:{away_goals}",
            "pick": pick,
            "prob": round(prob*100, 1),
            "odds": est_odds,
            "fixture_id": fixture.get("id"),
            "details": {
                "dominance": round(dominance,2),
                "shots_ratio": round(shots_ratio,2),
                "xg_ratio": round(xg_ratio,2),
                "possession_home": possession_home,
                "possession_away": possession_away,
                "home_shots_on": home_shots_on,
                "away_shots_on": away_shots_on,
                "home_xg": home_xg,
                "away_xg": away_xg
            }
        })
    return signals

def log_event(row: dict):
    is_new = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "time","league","match","minute","score","pick","prob","odds","fixture_id","details"
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
            "details": row.get("details","")
        })

def main():
    if SEND_ONLINE_ON_START:
        send_message(f"✅ <b>LiveMesterBot (TEST) online</b>\n🕒 {now_str()}")

    start_time = time.time()

    while True:
        poll, max_fx = current_limits()
        if poll == 0:
            # inaktív sávban ritkábban ébredünk fel
            time.sleep(60)
            # időzített kilépés ellenőrzés (Actions)
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

        if fixtures_with_stats:
            signals = generate_signals(fixtures_with_stats)
            if signals:
                for s in signals:
                    msg = (
                        "⚡ <b>LIVE VALUE ALERT</b> ⚡\n"
                        f"🏟️ <b>Meccs</b>: {s['match']} ({s['score']}, {s['minute']}' )\n"
                        f"🏆 <b>Liga</b>: {s['league']}\n"
                        f"🎯 <b>Tipp</b>: {s['pick']}\n"
                        f"📈 <b>Esély</b>: {s['prob']}%\n"
                        f"💰 <b>Odds</b>: {s['odds']}\n"
                        f"🧠 <b>Indoklás</b>: dom {s['details']['dominance']} | sokl {s['details']['shots_ratio']} | xG {s['details']['xg_ratio']}\n"
                    )
                    send_message(msg)
                    log_event(s)
            else:
                if not MUTE_NO_SIGNAL:
                    send_message(f"💤 Nincs erős jel ebben a ciklusban. ({now_str()})")
        else:
            if not MUTE_NO_SIGNAL:
                send_message(f"📭 Nincs elég stat/élő adat ebben a ciklusban. ({now_str()})")

        # Időzített kilépés (Actions): pl. 10 perc után zárjuk a futást, cron indítja újra
        if RUN_MINUTES > 0 and (time.time() - start_time) >= RUN_MINUTES * 60:
            break

        time.sleep(poll)

if __name__ == "__main__":
    main()
