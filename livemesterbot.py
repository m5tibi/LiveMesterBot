import subprocess, requests, time, os, json, math, logging
from datetime import datetime, timedelta
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

# ========= RENDER ÉBREN TARTÓ =========
app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot EXPERT v5.9: Dashboard"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server); t.daemon = True; t.start()

# ========= KONFIGURÁCIÓ =========
API_KEY           = os.environ.get("FOOTBALL_API_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID           = os.environ.get("TELEGRAM_CHAT_ID")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN")
REPO_URL          = "https://github.com/m5tibi/LiveMesterBot.git"
BASE_URL          = "https://v3.football.api-sports.io"
HEADERS           = {"x-apisports-key": API_KEY}
TIMEZONE          = "Europe/Budapest"

CACHE_FILE            = "foci_master_cache.json"
MASTER_TIPS_PREFIX    = "tips_"
LIVE_HISTORY_FILE     = "live_history.json"
SENT_ALERTS_FILE      = "sent_alerts.json"
TEAM_STATS_CACHE_FILE = "team_stats_cache.json"
ODDS_DRIFT_FILE       = "odds_drift.json"
LOG_FILE              = "bot.log"
BACKTEST_FILE         = "backtest.json"

# ========= LIVE LÖVÉS-SZŰRÉS KÜSZÖBÖK =========
SHOTS_ON_GOAL_MIN   = 3
SHOTS_TOTAL_MIN     = 6
DANGEROUS_ATT_MIN   = 20

LIVE_WINDOWS = [
    (33, 43),
    (50, 65),
]

LIVE_MIN_EV = 0.02

DRIFT_DROP_THRESHOLD = 0.05
DRIFT_RISE_THRESHOLD = 0.05

# ========= RETRY KONFIGURÁCIÓ =========
RETRY_MAX     = 3
RETRY_BACKOFF = 4
RETRY_TIMEOUT = 10


# ========= LOGGER =========

def setup_logger():
    logger = logging.getLogger("livemester")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger
    fmt = logging.Formatter(fmt="%(asctime)s | %(levelname)-7s | %(message)s",
                             datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a")
    fh.setLevel(logging.DEBUG); fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO); ch.setFormatter(fmt)
    logger.addHandler(fh); logger.addHandler(ch)
    return logger

log = setup_logger()


# =========================================================
# ÁLTALÁNOS RETRY
# =========================================================
def api_get_with_retry(url, params=None, max_retries=RETRY_MAX, backoff=RETRY_BACKOFF, timeout=RETRY_TIMEOUT):
    attempt = 0
    while attempt < max_retries:
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", backoff * (2 ** attempt)))
                log.warning(f"[api_retry] 429 Rate limit — vár {retry_after}s | {url}")
                time.sleep(retry_after)
                continue
            if resp.status_code >= 500:
                wait = backoff * (2 ** attempt)
                log.warning(f"[api_retry] {resp.status_code} szerver hiba — vár {wait}s | {url}")
                time.sleep(wait); attempt += 1; continue
            if resp.status_code >= 400:
                log.warning(f"[api_retry] {resp.status_code} kliens hiba — nincs retry | {url}")
                return None
            return resp
        except requests.exceptions.Timeout:
            wait = backoff * (2 ** attempt)
            log.warning(f"[api_retry] Timeout ({attempt+1}/{max_retries}) — vár {wait}s | {url}")
            time.sleep(wait); attempt += 1
        except requests.exceptions.RequestException as e:
            wait = backoff * (2 ** attempt)
            log.warning(f"[api_retry] Hálózati hiba ({attempt+1}/{max_retries}): {e} — vár {wait}s")
            time.sleep(wait); attempt += 1
    log.error(f"[api_retry] Minden próbálkozás sikertelen: {url}")
    return None


# =========================================================
# POISSON MODEL — közös számítási segédfüggvények
# =========================================================
def poisson_over_prob(lam, threshold):
    k = int(threshold)
    cumulative = sum(math.exp(-lam) * (lam ** i) / math.factorial(i) for i in range(k + 1))
    return max(0.0, min(1.0, 1.0 - cumulative))

def calc_ev(model_p, market_odds):
    if model_p is None or market_odds is None or market_odds <= 0:
        return None
    return round(model_p * market_odds - 1.0, 4)

def calc_fair_odds(model_p):
    if model_p and model_p > 0:
        return round(1.0 / model_p, 2)
    return None


# =========================================================
# FÁJL INICIALIZÁCIÓ
# =========================================================
def init_state_files():
    fixes = [
        (SENT_ALERTS_FILE,  {},  dict),
        (ODDS_DRIFT_FILE,   {},  dict),
        (LIVE_HISTORY_FILE, [],  list),
        (BACKTEST_FILE,     {"entries": []}, dict),
    ]
    fixed_files = []
    for fname, default, expected_type in fixes:
        needs_fix = False
        if not os.path.exists(fname):
            needs_fix = True; reason = "hiányzik"
        else:
            try:
                with open(fname, 'r') as f: data = json.load(f)
                if not isinstance(data, expected_type):
                    needs_fix = True
                    reason = f"hibás típus ({type(data).__name__} helyett {expected_type.__name__} kell)"
            except Exception as e:
                needs_fix = True; reason = f"parse hiba: {e}"
        if needs_fix:
            with open(fname, 'w') as f: json.dump(default, f)
            log.warning(f"[init] {fname} → {reason}, alapértékre állítva")
            fixed_files.append(fname)
        else:
            log.debug(f"[init] {fname} OK")
    if fixed_files:
        log.info(f"[init] Javított fájlok GitHub-ra szinkronizálva: {fixed_files}")
        sync_to_github(fixed_files, f"[init] state files migrated: {', '.join(fixed_files)}")


# ========= SEGÉDFÜGGVÉNYEK =========

def send_telegram(message, file_path=None):
    try:
        if file_path:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
            with open(file_path, 'rb') as f:
                requests.post(url, data={"chat_id": CHAT_ID, "caption": message, "parse_mode": "HTML"},
                              files={"document": f}, timeout=45)
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=20)
    except Exception as e:
        log.error(f"[send_telegram] Hiba: {e}")

def load_json(file, default, expected_type=None):
    if os.path.exists(file):
        try:
            with open(file, 'r') as f: data = json.load(f)
            if expected_type is not None and not isinstance(data, expected_type):
                log.error(f"[load_json] {file} hibás típus: várt={expected_type.__name__}, "
                          f"kapott={type(data).__name__} → felülírva default értékkel")
                with open(file, 'w') as fw: json.dump(default, fw)
                return default
            return data
        except Exception as e:
            log.error(f"[load_json] {file} olvasási hiba: {e} → default visszaadva")
            return default
    return default

def save_json(file, data):
    with open(file, 'w') as f: json.dump(data, f)

def sync_to_github(file_list, commit_message, delete_files=None):
    if not GITHUB_TOKEN: return
    try:
        subprocess.run(["git", "config", "--global", "user.email", "bot@livemester.com"])
        subprocess.run(["git", "config", "--global", "user.name", "LiveMesterBot"])
        auth_url = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")
        subprocess.run(["git", "remote", "remove", "origin"], stderr=subprocess.DEVNULL)
        subprocess.run(["git", "remote", "add", "origin", auth_url])
        if delete_files:
            for df in delete_files:
                subprocess.run(["git", "rm", "--cached", df], stderr=subprocess.DEVNULL)
        for f in file_list:
            if os.path.exists(f):
                subprocess.run(["git", "add", "-f", f])
        result = subprocess.run(["git", "commit", "-m", commit_message], capture_output=True, text=True)
        if "nothing to commit" in result.stdout:
            log.debug(f"[github] Nincs változás, commit átugorva: {commit_message}")
            return
        subprocess.run(["git", "push", "origin", "HEAD:main", "--force"])
        log.debug(f"[github] Szinkronizálva: {commit_message}")
    except Exception as e:
        log.error(f"[github] Hiba: {e}")

def clean_int(val):
    """Eltávolítja a % jelet és egyéb karaktereket a számmá alakítás előtt."""
    if val is None: return 0
    try:
        if isinstance(val, str):
            val = val.replace('%', '').strip()
        return int(float(val)) # float-on keresztül biztosabb, ha tizedespont is lenne
    except (ValueError, TypeError):
        return 0

# ========= SENT ALERTS =========

def load_sent_alerts(date_str):
    data = load_json(SENT_ALERTS_FILE, {}, dict)
    return data.get(date_str, [])

def save_sent_alert(date_str, fixture_id):
    data = load_json(SENT_ALERTS_FILE, {}, dict)
    day_list = data.get(date_str, [])
    fid_str = str(fixture_id)
    if fid_str not in day_list:
        day_list.append(fid_str)
        data[date_str] = day_list
        save_json(SENT_ALERTS_FILE, data)
        sync_to_github([SENT_ALERTS_FILE], f"sent_alert: {date_str}/{fid_str}")

def cleanup_sent_alerts(today_str):
    data = load_json(SENT_ALERTS_FILE, {}, dict)
    tz = pytz.timezone(TIMEZONE)
    cutoff = (datetime.now(tz) - timedelta(days=2)).strftime('%Y-%m-%d')
    cleaned = {k: v for k, v in data.items() if k >= cutoff}
    if cleaned != data: save_json(SENT_ALERTS_FILE, cleaned)

# ========= LOG ÖSSZEFOGLALÓ =========

def send_daily_log_summary():
    tz = pytz.timezone(TIMEZONE)
    yest = (datetime.now(tz) - timedelta(days=1)).strftime('%Y-%m-%d')
    if not os.path.exists(LOG_FILE): return
    error_count = warning_count = alert_count = drift_count = 0
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        for line in lines:
            if '| ERROR   |' in line: error_count   += 1
            if '| WARNING |' in line: warning_count += 1
            if '[ALERT]'  in line:    alert_count   += 1
            if '[DRIFT]'  in line:    drift_count   += 1
    except Exception as e:
        log.error(f"[log_summary] Olvasási hiba: {e}"); return
    summary = (
        f"📝 <b>Bot.log — {yest}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🚨 Hibák: <b>{error_count}</b>\n"
        f"⚠️ Figyelmeztetések: {warning_count}\n"
        f"📲 Előriadók: <b>{alert_count}</b>\n"
        f"📉 Drift jelzések: {drift_count}"
    )
    send_telegram(summary, LOG_FILE) if lines else send_telegram(summary)
    archive_name = f"bot_{yest}.log"
    try: os.rename(LOG_FILE, archive_name)
    except Exception as e: log.error(f"[log_summary] Archiválás hiba: {e}")
    cutoff_dt = datetime.now() - timedelta(days=7)
    for f in os.listdir('.'):
        if f.startswith("bot_") and f.endswith(".log"):
            try:
                if datetime.strptime(f[4:14], '%Y-%m-%d') < cutoff_dt:
                    os.remove(f)
            except: pass

# ========= VISSZAMÉRÉS DASHBOARD =========

def update_backtest(live_history_entries, date_str):
    bt = load_json(BACKTEST_FILE, {"entries": []}, dict)
    if not isinstance(bt.get("entries"), list):
        log.error("[backtest] Hibás struktúra, alaphelyzetbe állítva.")
        bt = {"entries": []}
    new_entries = []
    for lt in live_history_entries:
        fid     = lt.get("id")
        ev      = lt.get("ev") or 0
        model_p = lt.get("model_p")
        lo      = lt.get("live_odds")
        minute  = lt.get("minute", 0)
        won = False
        resp = api_get_with_retry(f"{BASE_URL}/fixtures", params={"id": fid})
        if resp is None:
            log.warning(f"[backtest] Eredmény lekérdezés sikertelen ({fid}), kihagyva.")
            continue
        try:
            r = resp.json().get("response", [])
            if r:
                gh = r[0]["goals"]["home"] or 0
                ga = r[0]["goals"]["away"] or 0
                won = (gh + ga) > 1.5
        except Exception as e:
            log.warning(f"[backtest] JSON parse hiba ({fid}): {e}"); continue
        fair_odds = calc_fair_odds(model_p)
        value_bet = (lo is not None and fair_odds is not None and lo >= fair_odds)
        entry = {"date": date_str, "id": fid, "minute": minute, "ev": round(ev, 4),
                 "live_odds": lo, "fair_odds": fair_odds, "value_bet": value_bet, "won": won}
        new_entries.append(entry)
        log.info(f"[backtest] {fid} | ev={ev*100:.1f}% | value={value_bet} | won={won}")
    bt["entries"].extend(new_entries)
    save_json(BACKTEST_FILE, bt)
    return new_entries

def build_dashboard_message(new_entries):
    bt = load_json(BACKTEST_FILE, {"entries": []}, dict)
    all_e = bt.get("entries", [])
    if not isinstance(all_e, list): all_e = []
    if not all_e:
        return "📊 <b>Dashboard</b>\nNincs elég adat még."
    total   = len(all_e)
    wins    = sum(1 for e in all_e if e.get("won"))
    hit_all = wins / total * 100 if total else 0
    value_e    = [e for e in all_e if e.get("value_bet")]
    no_value_e = [e for e in all_e if not e.get("value_bet")]
    v_wins  = sum(1 for e in value_e    if e.get("won"))
    nv_wins = sum(1 for e in no_value_e if e.get("won"))
    v_hit   = v_wins  / len(value_e)    * 100 if value_e    else 0
    nv_hit  = nv_wins / len(no_value_e) * 100 if no_value_e else 0
    w1_e = [e for e in all_e if 33 <= (e.get("minute") or 0) <= 43]
    w2_e = [e for e in all_e if 50 <= (e.get("minute") or 0) <= 65]
    w1_hit = sum(1 for e in w1_e if e.get("won")) / len(w1_e) * 100 if w1_e else 0
    w2_hit = sum(1 for e in w2_e if e.get("won")) / len(w2_e) * 100 if w2_e else 0
    def ev_bucket(e):
        ev = e.get("ev", 0)
        if ev < 0.03: return "2-3%"
        if ev < 0.05: return "3-5%"
        if ev < 0.10: return "5-10%"
        return ">10%"
    buckets = {}
    for e in all_e:
        b = ev_bucket(e)
        buckets.setdefault(b, {"total": 0, "won": 0})
        buckets[b]["total"] += 1
        if e.get("won"): buckets[b]["won"] += 1
    ev_lines = ""
    for bname in ["2-3%", "3-5%", "5-10%", ">10%"]:
        bd = buckets.get(bname)
        if bd and bd["total"] > 0:
            pct = bd["won"] / bd["total"] * 100
            ev_lines += f"  EV {bname}: {bd['won']}/{bd['total']} ({pct:.0f}%)\n"
    today_won  = sum(1 for e in new_entries if e.get("won"))
    today_tot  = len(new_entries)
    today_line = f"Ma: {today_won}/{today_tot}" if today_tot else "Ma: nincs tipp"
    msg = (
        f"📊 <b>VISSZAMÉRÉS DASHBOARD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Összes: <b>{wins}/{total}</b> ({hit_all:.1f}%)\n"
        f"📅 {today_line}\n\n"
        f"✅ VALUE tipek:   <b>{v_wins}/{len(value_e)}</b> ({v_hit:.1f}%)\n"
        f"⚠️ Nem-VALUE:      {nv_wins}/{len(no_value_e)} ({nv_hit:.1f}%)\n\n"
        f"⏱ Ablak 33–43\u2019:  {sum(1 for e in w1_e if e.get('won'))}/{len(w1_e)} ({w1_hit:.1f}%)\n"
        f"⏱ Ablak 50–65\u2019:  {sum(1 for e in w2_e if e.get('won'))}/{len(w2_e)} ({w2_hit:.1f}%)\n\n"
        f"🔬 EV kalibáció:\n{ev_lines}"
    )
    return msg.strip()

# ========= TAKARÍTÁS =========

def cleanup_old_files():
    files_to_delete = []
    cutoff = datetime.now() - timedelta(days=7)
    for f in os.listdir('.'):
        if (f.startswith("expert_lista_") or f.startswith("report_")) and f.endswith(".xlsx"):
            try:
                if datetime.strptime(f.split('_')[-1].split('.xlsx')[0], '%Y-%m-%d') < cutoff:
                    os.remove(f); files_to_delete.append(f)
                    log.info(f"[cleanup] Törölve: {f}")
            except: pass
        if f.startswith(MASTER_TIPS_PREFIX) and f.endswith(".json"):
            try:
                if datetime.strptime(f[len(MASTER_TIPS_PREFIX):].split('.json')[0], '%Y-%m-%d') < cutoff:
                    os.remove(f); files_to_delete.append(f)
                    log.info(f"[cleanup] Törölve: {f}")
            except: pass
    return files_to_delete

# ========= MASTER BUILDER CACHE =========

def load_master_tips_for_today(date_str):
    fname = f"{MASTER_TIPS_PREFIX}{date_str}.json"
    data = load_json(fname, None)
    if data is None: return {}
    if not isinstance(data, dict):
        log.error(f"[load_master_tips] {fname} hibás típus: {type(data).__name__} → üres dict")
        return {}
    return {int(t["fixture_id"]): t for t in data.get("tips", []) if t.get("fixture_id") is not None}

def get_ev_for_fixture(master_tips, fixture_id):
    tip = master_tips.get(int(fixture_id))
    return (tip.get("ev"), tip.get("model_p")) if tip else (None, None)

def get_prematch_odds_for_fixture(master_tips, fixture_id):
    tip = master_tips.get(int(fixture_id))
    return (tip.get("odds") or {}).get("over15") if tip else None

# =========================================================
# LIVE API HÍVÁSOK
# =========================================================

def fetch_live_fixtures():
    resp = api_get_with_retry(f"{BASE_URL}/fixtures", params={"live": "all"})
    if resp is None:
        log.error("[fetch_live] Minden próbálkozás sikertelen.")
        return []
    try:
        return resp.json().get("response", [])
    except Exception as e:
        log.error(f"[fetch_live] JSON parse hiba: {e}")
        return []

def fetch_live_odds(mid):
    params = {"fixture": mid, "bet": 11} # Over/Under
    for _ in range(2):
        try:
            r = requests.get(f"{BASE_URL}/odds", headers=HEADERS, params=params, timeout=10)
            res = r.json().get("response", [])
            if res:
                # Először próbáljuk a Bet365-öt (ID: 8)
                for bm in res[0].get('bookmakers', []):
                    if bm['id'] == 8:
                        for bet in bm.get('bets', []):
                            for val in bet.get('values', []):
                                if val['value'] == 'Over 1.5': return float(val['odd'])
                
                # Ha nincs Bet365, jó bármelyik másik iroda (pl. 1xBet, Marathonbet stb.)
                for bm in res[0].get('bookmakers', []):
                    for bet in bm.get('bets', []):
                        for val in bet.get('values', []):
                            if val['value'] == 'Over 1.5': return float(val['odd'])
            time.sleep(1)
        except: continue
    return None

def get_live_shot_stats(mid):
    try:
        r = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={mid}", headers=HEADERS, timeout=12)
        res = r.json().get("response", [])
        stats = {"shots_on_goal": 0, "shots_total": 0, "dangerous_att": 0}
        if not res: return stats
        for team_data in res:
            for s in team_data.get('statistics', []):
                t = s.get('type')
                v = clean_int(s.get('value')) # Itt használjuk a javított függvényt
                if t == 'Shots on Goal': stats["shots_on_goal"] += v
                if t in ['Shots on Goal', 'Shots off Goal']: stats["shots_total"] += v
                if t == 'Dangerous Attacks': stats["dangerous_att"] += v
        return stats
    except Exception as e:
        log.warning(f"[shot_stats] Hiba ({mid}): {e}")
        return {"shots_on_goal": 0, "shots_total": 0, "dangerous_att": 0}

def fetch_fixture_corners(fixture_id):
    """Lezárt meccs szögletszámát kéri le az /fixtures/statistics endpointról."""
    resp = api_get_with_retry(f"{BASE_URL}/fixtures/statistics", params={"fixture": fixture_id}, max_retries=2)
    if resp is None:
        log.warning(f"[corners] Stat nem elérhető ({fixture_id})")
        return 0
    try:
        total = 0
        for team_stat in resp.json().get("response", []):
            for stat in team_stat.get("statistics", []):
                if stat.get("type") == "Corner Kicks":
                    total += int(stat.get("value") or 0)
        return total
    except Exception as e:
        log.warning(f"[corners] Parse hiba ({fixture_id}): {e}")
        return 0

def build_odds_line(live_odds, prematch_odds, model_p, drift_info=None):
    fair_odds = calc_fair_odds(model_p)
    lines = []
    if live_odds is not None:
        value_ok = fair_odds is not None and live_odds >= fair_odds
        fair_str = f" | fair: {fair_odds}" if fair_odds else ""
        lines.append(f"💰 Live odds: <b>{live_odds}</b>{fair_str} → {'✅ VALUE' if value_ok else '⚠️ alacsony'}")
    elif prematch_odds is not None:
        fair_str = f" | fair: {fair_odds}" if fair_odds else ""
        lines.append(f"💰 Pre-match odds: {prematch_odds}{fair_str} (live nem elérhető)")
    if drift_info:
        if drift_info["direction"] == "drop":
            lines.append(f"📉 <b>Odds esett:</b> {drift_info['prev']} → {live_odds} (-{drift_info['pct']:.1f}%) — smart money!")
        else:
            lines.append(f"📈 Odds nőtt: {drift_info['prev']} → {live_odds} (+{drift_info['pct']:.1f}%) — gyengülő piac")
    return "\n".join(lines)

# ========= ODDS DRIFT =========

def check_odds_drift(fixture_id, current_odds, now_str):
    if current_odds is None: return None
    dc = load_json(ODDS_DRIFT_FILE, {}, dict)
    key = str(fixture_id)
    prev = dc.get(key)
    dc[key] = {"last_odds": current_odds, "ts": now_str}
    save_json(ODDS_DRIFT_FILE, dc)
    if not prev: return None
    po = prev.get("last_odds")
    if not po or po <= 0: return None
    chg = (po - current_odds) / po
    if chg >= DRIFT_DROP_THRESHOLD:  return {"prev": po, "pct": chg * 100, "direction": "drop"}
    if chg <= -DRIFT_RISE_THRESHOLD: return {"prev": po, "pct": abs(chg) * 100, "direction": "rise"}
    return None

# ========= CSAPAT ADATOK =========

def get_team_detailed_data(team_id):
    cache = load_json(TEAM_STATS_CACHE_FILE, {}, dict)
    if str(team_id) in cache:
        return cache[str(team_id)]
    resp = api_get_with_retry(f"{BASE_URL}/fixtures", params={"team": team_id, "last": 10})
    if resp is None:
        log.warning(f"[team_data] Meccs adat nem elérhető ({team_id}).")
        return None
    try:
        matches = resp.json().get("response", [])
    except Exception as e:
        log.warning(f"[team_data] JSON parse hiba ({team_id}): {e}")
        return None
    if not matches: return None
    s = c = btts_count = 0
    corn_list = []
    for i, m in enumerate(matches):
        is_h = m['teams']['home']['id'] == team_id
        scored   = (m['goals']['home'] if is_h else m['goals']['away']) or 0
        conceded = (m['goals']['away'] if is_h else m['goals']['home']) or 0
        s += scored; c += conceded
        if i < 5 and scored > 0 and conceded > 0: btts_count += 1
        if i < 5:
            st_resp = api_get_with_retry(
                f"{BASE_URL}/fixtures/statistics",
                params={"fixture": m['fixture']['id'], "team": team_id},
                max_retries=2,
            )
            if st_resp is not None:
                try:
                    st = st_resp.json().get("response", [])
                    if st:
                        for stat in st[0].get('statistics', []):
                            if stat['type'] == 'Corner Kicks':
                                corn_list.append(stat['value'] or 0)
                except Exception as e:
                    log.debug(f"[team_data] Corner parse hiba ({team_id}): {e}")
    res = {
        "avg_scored":   s / 10,
        "avg_conceded": c / 10,
        "btts_trend":   btts_count,
        "corner_avg":   sum(corn_list) / len(corn_list) if len(corn_list) >= 3 else None,
    }
    cache[str(team_id)] = res
    save_json(TEAM_STATS_CACHE_FILE, cache)
    return res

def is_active_game(s):
    return (s["shots_on_goal"] >= SHOTS_ON_GOAL_MIN or
            s["shots_total"]   >= SHOTS_TOTAL_MIN or
            (s["dangerous_att"] >= DANGEROUS_ATT_MIN and s["shots_on_goal"] >= 1))

def in_live_window(e):
    return any(s <= e <= en for s, en in LIVE_WINDOWS)

# =========================================================
# SZKENNER
# =========================================================

def scan_next_day():
    tz = pytz.timezone(TIMEZONE)
    target = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    log.info(f"[scan] Deep Scan: {target}")
    send_telegram(
        f"🔬 <b>EXPERT v5.9 — Deep Scan indul</b>\n"
        f"📅 Dátum: <b>{target}</b>"
    )
    try:
        resp = api_get_with_retry(f"{BASE_URL}/fixtures", params={"date": target})
        if resp is None:
            send_telegram("⚠️ Scan sikertelen — API nem válaszolt.")
            return
        matches = resp.json().get("response", [])
        log.info(f"[scan] {len(matches)} meccs")
        valid = []
        tips_entries = []
        for m in matches:
            hd = get_team_detailed_data(m['teams']['home']['id'])
            ad = get_team_detailed_data(m['teams']['away']['id'])
            if not hd or not ad: continue
            lam_h = (hd['avg_scored'] + ad['avg_conceded']) / 2
            lam_a = (ad['avg_scored'] + hd['avg_conceded']) / 2
            lam   = lam_h + lam_a
            p_over15 = poisson_over_prob(lam, 1.5)
            p_over25 = poisson_over_prob(lam, 2.5)
            fair_o15 = calc_fair_odds(p_over15)
            fair_o25 = calc_fair_odds(p_over25)
            prematch_o15 = None
            odds_resp = api_get_with_retry(
                f"{BASE_URL}/odds",
                params={"fixture": m['fixture']['id'], "bookmaker": 1},
                max_retries=2,
            )
            if odds_resp is not None:
                try:
                    for bk in (odds_resp.json().get("response") or [{}])[0].get("bookmakers", []):
                        for bet in bk.get("bets", []):
                            name = (bet.get("name") or "").lower()
                            if "total" not in name and "goals" not in name: continue
                            for val in bet.get("values", []):
                                if str(val.get("value") or "").lower() in ("over 1.5", "o 1.5", "over1.5"):
                                    try: prematch_o15 = float(val["odd"])
                                    except (ValueError, TypeError): pass
                except Exception as e:
                    log.debug(f"[scan] Pre-match odds parse hiba ({m['fixture']['id']}): {e}")
            ev_o15 = calc_ev(p_over15, prematch_o15)
            tips = []
            op = p_over25 * 100
            if p_over25 > 0.82:   tips.append("Over 2.5")
            elif p_over15 > 0.68: tips.append("Over 1.5")
            if (hd['avg_scored'] > 1.1 and ad['avg_scored'] > 1.1
                    and hd['btts_trend'] >= 2 and ad['btts_trend'] >= 2):
                tips.append("Over 2.5 & BTTS" if p_over25 > 0.80 else "BTTS")
            ci = "N/A"
            if hd['corner_avg'] and ad['corner_avg']:
                ec = hd['corner_avg'] + ad['corner_avg']; ci = round(ec, 1)
                if ec >= 10.5: tips.append("Corners Over 8.5")
                elif ec >= 9.2: tips.append("Corners Over 7.5")
            if not tips: continue
            ev_str = f"+{ev_o15*100:.1f}%" if ev_o15 is not None and ev_o15 > 0 else ""
            kick   = (datetime.fromisoformat(m['fixture']['date'][:19])
                      .replace(tzinfo=pytz.utc).astimezone(tz).strftime('%H:%M'))
            valid.append({
                "ID":              m['fixture']['id'],
                "ÍDŐPONT":          kick,
                "BAJNOKSÁG":       m['league']['name'].upper(),
                "MECCS":           f"{m['teams']['home']['name']} - {m['teams']['away']['name']}",
                "OVER 2.5 ESÉLY":  f"{round(op, 1)}%",
                "VÁRHATÓ SZÖGLET": ci,
                "TIPP JAVASLAT":   " | ".join(tips),
                "EV":              ev_str,
            })
            tips_entries.append({
                "fixture_id": m['fixture']['id'],
                "model_p":    round(p_over15, 4),
                "ev":         ev_o15,
                "odds":       {"over15": prematch_o15, "over25": calc_fair_odds(p_over25)},
                "fair_odds":  {"over15": fair_o15, "over25": fair_o25},
                "lambda":     round(lam, 3),
            })
        log.info(f"[scan] {len(valid)} tipp: {target}")
        if valid:
            cache = load_json(CACHE_FILE, {}, dict)
            cache[target] = valid
            save_json(CACHE_FILE, cache)
            tips_fname = f"{MASTER_TIPS_PREFIX}{target}.json"
            save_json(tips_fname, {"date": target, "tips": tips_entries})
            log.info(f"[scan] Tips JSON mentve: {tips_fname} ({len(tips_entries)} bejegyzés)")
            lines = []
            for v in valid[:5]:
                ev_badge = f" 💹 EV {v['EV']}" if v.get("EV") else ""
                lines.append(
                    f"⏰ {v['ÍDŐPONT']} | {v['MECCS']}\n"
                    f"   🎯 {v['TIPP JAVASLAT']}{ev_badge}\n"
                    f"   📊 O2.5: {v['OVER 2.5 ESÉLY']} | Szöglet: {v['VÁRHATÓ SZÖGLET']}"
                )
            extra = f"\n<i>...+{len(valid)-5} még</i>" if len(valid) > 5 else ""
            msg = (
                f"✅ <b>Deep Scan kész — {target}</b>\n"
                f"📊 Összes tipp: <b>{len(valid)}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                + "\n".join(lines) + extra
            )
            fn = f"expert_lista_{target}.xlsx"
            pd.DataFrame(valid).to_excel(fn, index=False)
            send_telegram(msg, fn)
            sync_to_github(
                [CACHE_FILE, fn, TEAM_STATS_CACHE_FILE, tips_fname],
                f"v5.9 Scan: {target}"
            )
        else:
            send_telegram(f"📅 <b>Deep Scan — {target}</b>\nℹ️ Nincs megfelelő meccs ma.")
    except Exception as e:
        log.error(f"[scan] Hiba: {e}")
        send_telegram(f"⚠️ <b>Scan hiba — {target}</b>\n<code>{e}</code>")

# ========= JELENTÉS =========

def get_final_report():
    tz = pytz.timezone(TIMEZONE)
    today_str = datetime.now(tz).strftime('%Y-%m-%d')
    yest      = (datetime.now(tz) - timedelta(days=1)).strftime('%Y-%m-%d')
    log.info(f"[report] Napi zárás: {yest}")
    cache = load_json(CACHE_FILE, {}, dict)
    matches = cache.get(yest, [])
    if not isinstance(matches, list):
        log.error(f"[report] Hibás matches típus: {type(matches).__name__}")
        matches = []
    if not matches:
        log.info("[report] Nincs adat tegnap.")
        send_daily_log_summary(); return
    send_telegram(
        f"📊 <b>Összetett jelentés</b>\n"
        f"📅 Dátum: <b>{yest}</b>"
    )
    final = []
    gol_ok = gol_fail = 0
    for m in matches:
        try:
            resp = api_get_with_retry(f"{BASE_URL}/fixtures", params={"id": m['ID']})
            if resp is None:
                log.warning(f"[report] Fixtures lekérés sikertelen: {m['ID']}"); continue
            r = resp.json().get("response", [])
            if r:
                res = r[0]
                h = clean_int(fx['goals']['home'])
                a = clean_int(fx['goals']['away'])
                total_goals = h + a

                # FIX 1: szöglet külön API hívással
                c_total = fetch_fixture_corners(m['ID'])

                m["EREDMÉNY"]    = f"{h}-{a}"
                tipp = m.get("TIPP JAVASLAT", "")
                if "Over 2.5" in tipp:
                    m["GÓL SIKER"] = "✅" if total_goals > 2.5 else "❌"
                else:
                    m["GÓL SIKER"] = "✅" if total_goals > 1.5 else "❌"
                m["BTTS SIKER"]   = "✅" if h > 0 and a > 0 else "❌"
                m["SZÖGLET ÖSSZ"] = c_total

                if m["GÓL SIKER"] == "✅": gol_ok   += 1
                else:                      gol_fail += 1

                log.info(f"[report] {m['MECCS']}: {h}-{a} | {m['GÓL SIKER']} | szöglet={c_total}")
            final.append(m); time.sleep(1)
        except Exception as e:
            log.error(f"[report] Meccs hiba: {e}"); continue

    # FIX 2: napi tipp összesítő üzenet
    total_tips = gol_ok + gol_fail
    hit_rate   = gol_ok / total_tips * 100 if total_tips else 0
    emoji      = "🔥" if hit_rate >= 60 else ("✅" if hit_rate >= 45 else "⚠️")
    summary_msg = (
        f"📋 <b>NAPI TIPP KIÉRTÉKELÉS — {yest}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} Gól tippek: <b>{gol_ok}/{total_tips}</b> ({hit_rate:.1f}%)\n"
        f"✅ Sikeres: {gol_ok}  ❌ Sikertelen: {gol_fail}\n"
    )
    # Top 5 eredmény sorban
    result_lines = []
    for m in final[:5]:
        ikon = "✅" if m.get("GÓL SIKER") == "✅" else "❌"
        result_lines.append(
            f"{ikon} {m['MECCS']} → <b>{m.get('EREDMÉNY','?')}</b> "
            f"({m.get('TIPP JAVASLAT','?')})"
        )
    if result_lines:
        summary_msg += "\n" + "\n".join(result_lines)
    if len(final) > 5:
        summary_msg += f"\n<i>...+{len(final)-5} meccs az xlsx-ben</i>"
    send_telegram(summary_msg)

    live_history = load_json(LIVE_HISTORY_FILE, [], list)
    live_wins = 0
    if live_history:
        for lt in live_history:
            try:
                resp = api_get_with_retry(f"{BASE_URL}/fixtures", params={"id": lt['id']})
                if resp and (resp.json().get("response") or [{}])[0].get("goals", {}):
                    r0 = resp.json()["response"][0]
                    if (r0['goals']['home'] or 0) + (r0['goals']['away'] or 0) > 1.5:
                        live_wins += 1
            except: continue
        live_msg = (
            f"📱 <b>LIVE ÖSSZESITŐ</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 Küldött: <b>{len(live_history)}</b>\n"
            f"✅ Nyert (O1.5): <b>{live_wins}</b>\n"
            f"📋 Hit rate: {live_wins/len(live_history)*100:.1f}%"
        )
        log.info(f"[report] Live: {len(live_history)} tipp, {live_wins} nyert")
    else:
        live_msg = "📱 <b>LIVE ÖSSZESITŐ</b>\nMa nem volt élő tipp."
        log.info("[report] Nincs live tipp.")

    fn = f"report_{yest}.xlsx"
    pd.DataFrame(final).to_excel(fn, index=False)
    send_telegram(live_msg, fn)

    if live_history:
        new_entries = update_backtest(live_history, yest)
        dashboard_msg = build_dashboard_message(new_entries)
        send_telegram(dashboard_msg)
        log.info(f"[backtest] Dashboard elküldve ({len(new_entries)} új bejegyzés)")

    deleted_files = cleanup_old_files()
    save_json(LIVE_HISTORY_FILE, [])
    save_json(ODDS_DRIFT_FILE, {})
    cleanup_sent_alerts(today_str)
    send_daily_log_summary()
    sync_to_github([fn, LIVE_HISTORY_FILE, SENT_ALERTS_FILE, ODDS_DRIFT_FILE, BACKTEST_FILE],
                   f"Final Report: {yest}", delete_files=deleted_files)

# ========= FŐ CIKLUS =========

def main_loop():
    tz = pytz.timezone(TIMEZONE)
    log.info("=" * 50)
    log.info("Bot v5.9 elindult (Poisson EV + jobb Telegram formátum).")
    log.info(f"LIVE_MIN_EV={LIVE_MIN_EV} | WINDOWS={LIVE_WINDOWS}")
    log.info(f"RETRY_MAX={RETRY_MAX} | RETRY_BACKOFF={RETRY_BACKOFF}s | RETRY_TIMEOUT={RETRY_TIMEOUT}s")
    log.info("=" * 50)
    while True:
        now = datetime.now(tz)
        if now.hour == 21 and now.minute ==40: scan_next_day();     time.sleep(61)
        if now.hour == 0  and now.minute == 10: get_final_report(); time.sleep(61)
        try:
            today_str   = now.strftime('%Y-%m-%d')
            now_str     = now.strftime('%H:%M')
            cache_data  = load_json(CACHE_FILE, {}, dict)
            today_m     = cache_data.get(today_str, [])
            if not isinstance(today_m, list):
                log.error(f"[main_loop] today_m hibás típus ({type(today_m).__name__}), kiürítve.")
                today_m = []
            sent_today  = load_sent_alerts(today_str)
            master_tips = load_master_tips_for_today(today_str)
            if today_m:
                t_ids = [m['ID'] for m in today_m]
                live_fixtures = fetch_live_fixtures()
                log.debug(f"[main_loop] {len(live_fixtures)} élő meccs | {now_str}")
                for fx in live_fixtures:
                    mid   = fx["fixture"]["id"]
                    min_  = fx["fixture"]["status"]["elapsed"] or 0
                    h, a  = (fx["goals"]["home"] or 0), (fx["goals"]["away"] or 0)
                    label = f"{fx['teams']['home']['name']} – {fx['teams']['away']['name']}"
                    if mid not in t_ids: continue
                    if (h + a) > 1:     continue
                    lo = fetch_live_odds(mid)
                    di = check_odds_drift(mid, lo, now_str)
                    if str(mid) in sent_today and di is not None:
                        log.info(f"[DRIFT] {label} | {di['direction']} {di['pct']:.1f}%")
                        if di["direction"] == "drop":
                            send_telegram(
                                f"📉 <b>ODDS DRIFT — Smart money!</b>\n"
                                f"⚽ {label}\n"
                                f"💰 {di['prev']} → <b>{lo}</b> (-{di['pct']:.1f}%)\n"
                                f"✅ A piac az Over javulását árazhatja"
                            )
                        else:
                            send_telegram(
                                f"📈 <b>ODDS DRIFT — Gyengülő piac</b>\n"
                                f"⚽ {label}\n"
                                f"💰 {di['prev']} → <b>{lo}</b> (+{di['pct']:.1f}%)\n"
                                f"⚠️ Csilli-villi esemény eshet nélkül"
                            )
                        continue
                    if str(mid) in sent_today: continue
                    if not in_live_window(min_):
                        log.debug(f"[main_loop] {label} – {min_}' – ablakból kiesett"); continue
                    ss = get_live_shot_stats(mid)
                    if not is_active_game(ss):
                        log.debug(f"[main_loop] {label} – low activity, skip"); continue
                    if not master_tips:
                        log.warning(f"[main_loop] Nincs master tips – {today_str}"); continue
                    ev, model_p = get_ev_for_fixture(master_tips, mid)
                    if ev is None or ev < LIVE_MIN_EV:
                        log.debug(f"[main_loop] {label} – EV={ev}, skip (min={LIVE_MIN_EV*100:.0f}%)"); continue
                    fair_odds = calc_fair_odds(model_p)
                    if lo is not None and fair_odds is not None and lo < fair_odds:
                        log.info(f"[main_loop] {label} – odds alacsony (live={lo} < fair={fair_odds}), VALUE SZŰRÉS")
                        continue
                    po = get_prematch_odds_for_fixture(master_tips, mid)
                    ol = build_odds_line(lo, po, model_p, di)
                    activity_bar = "🟢" if ss['shots_on_goal'] >= 5 else ("🟡" if ss['shots_on_goal'] >= 3 else "🔴")
                    msg = (
                        f"⚽ <b>LIVE ALERT — Over 1.5 🔥</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏟 {label}\n"
                        f"📍 {h}–{a} — <b>{min_}. perc</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"{activity_bar} Kapura: <b>{ss['shots_on_goal']}</b> | Össz: {ss['shots_total']} | Veszélyes: {ss['dangerous_att']}\n"
                        f"📊 EV: <b>+{ev*100:.1f}%</b> | P(O1.5): {f'{model_p*100:.1f}%' if model_p else 'N/A'}\n"
                    )
                    if ol: msg += ol
                    send_telegram(msg)
                    log.info(f"[ALERT] {label} | {min_}' | EV={ev*100:.1f}% | odds={lo} | fair={fair_odds}")
                    save_sent_alert(today_str, mid)
                    hst = load_json(LIVE_HISTORY_FILE, [], list)
                    hst.append({"id": mid, "time": now_str, "ev": ev, "model_p": model_p,
                                 "shots_on": ss["shots_on_goal"], "shots_tot": ss["shots_total"],
                                 "score_live": f"{h}-{a}", "minute": min_,
                                 "live_odds": lo, "prematch_odds": po})
                    save_json(LIVE_HISTORY_FILE, hst)
        except Exception as e:
            log.error(f"[main_loop] Váratlan hiba: {e}")
        time.sleep(40)

if __name__ == "__main__":
    keep_alive()
    init_state_files()
    main_loop()
