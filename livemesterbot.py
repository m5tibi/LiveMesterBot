import subprocess, requests, time, os, json, math, logging
from datetime import datetime, timedelta
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

# ========= RENDER ÉBREN TARTÓ =========
app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot EXPERT v5.6: Dashboard"

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
    """
    JSON fájl biztonságos betöltése.
    Ha expected_type meg van adva (pl. dict vagy list), ellenőrzi a típust.
    Hibás típus vagy olvasási hiba esetén a default értéket adja vissza és logol.
    """
    if os.path.exists(file):
        try:
            with open(file, 'r') as f:
                data = json.load(f)
            if expected_type is not None and not isinstance(data, expected_type):
                log.error(f"[load_json] {file} hibás típus: várt={expected_type.__name__}, kapott={type(data).__name__} → default visszaadva")
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
            for df in delete_files: subprocess.run(["git", "rm", df], stderr=subprocess.DEVNULL)
        for f in file_list:
            if os.path.exists(f): subprocess.run(["git", "add", f])
        subprocess.run(["git", "commit", "-m", commit_message])
        subprocess.run(["git", "push", "origin", "HEAD:main", "--force"])
        log.debug(f"[github] Szinkronizálva: {commit_message}")
    except Exception as e:
        log.error(f"[github] Hiba: {e}")

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
    summary = (f"📝 <b>Bot.log — {yest}</b>\n🚨 Hibák: {error_count}\n"
               f"⚠️ Figyelmeztetések: {warning_count}\n📲 Riasztások: {alert_count}\n"
               f"📉 Drift jelzések: {drift_count}")
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
#
# backtest.json struktúrája:
# {
#   "entries": [
#     { "date": "2026-03-31", "id": 123, "minute": 38, "ev": 0.042,
#       "live_odds": 1.38, "value_bet": true, "won": true },
#     ...
#   ]
# }
#
# value_bet = True ha live_odds >= fair_odds (1/model_p)
# won       = True ha végső gól > 1.5

def update_backtest(live_history_entries, date_str):
    """
    A get_final_report() hívja meg: lekérdezi a végső eredményt
    minden live tipp-re, és bejegyzi a backtest.json-ba.
    Visszatér a napi összefoglaló dict-tel.
    """
    bt = load_json(BACKTEST_FILE, {"entries": []}, dict)
    if not isinstance(bt.get("entries"), list):
        log.error(f"[backtest] Hibás backtest struktúra, alaphelyzetbe állítva.")
        bt = {"entries": []}
    new_entries = []

    for lt in live_history_entries:
        fid     = lt.get("id")
        ev      = lt.get("ev") or 0
        model_p = lt.get("model_p")
        lo      = lt.get("live_odds")
        minute  = lt.get("minute", 0)

        # Végső eredmény lekérdezése
        won = False
        try:
            r = requests.get(f"{BASE_URL}/fixtures?id={fid}", headers=HEADERS, timeout=10)\
                         .json().get("response", [])
            if r:
                gh = r[0]["goals"]["home"] or 0
                ga = r[0]["goals"]["away"] or 0
                won = (gh + ga) > 1.5
        except Exception as e:
            log.warning(f"[backtest] Eredmény lekérdezés hiba ({fid}): {e}")
            continue

        # VALUE bet meghatározás
        fair_odds = round(1.0 / model_p, 4) if model_p and model_p > 0 else None
        value_bet = (lo is not None and fair_odds is not None and lo >= fair_odds)

        entry = {
            "date":      date_str,
            "id":        fid,
            "minute":    minute,
            "ev":        round(ev, 4),
            "live_odds": lo,
            "fair_odds": fair_odds,
            "value_bet": value_bet,
            "won":       won,
        }
        new_entries.append(entry)
        log.info(f"[backtest] {fid} | ev={ev*100:.1f}% | value={value_bet} | won={won}")

    bt["entries"].extend(new_entries)
    save_json(BACKTEST_FILE, bt)
    return new_entries

def build_dashboard_message(new_entries):
    """
    Kumulatív dashboard üzenet építése a teljes backtest.json alapján.
    """
    bt = load_json(BACKTEST_FILE, {"entries": []}, dict)
    all_e = bt.get("entries", [])
    if not isinstance(all_e, list):
        all_e = []

    if not all_e:
        return "📊 <b>Dashboard</b>\nNincs elég adat még."

    total   = len(all_e)
    wins    = sum(1 for e in all_e if e.get("won"))
    hit_all = wins / total * 100 if total else 0

    # VALUE vs nem-VALUE
    value_e    = [e for e in all_e if e.get("value_bet")]
    no_value_e = [e for e in all_e if not e.get("value_bet")]
    v_wins  = sum(1 for e in value_e    if e.get("won"))
    nv_wins = sum(1 for e in no_value_e if e.get("won"))
    v_hit   = v_wins  / len(value_e)    * 100 if value_e    else 0
    nv_hit  = nv_wins / len(no_value_e) * 100 if no_value_e else 0

    # Időablak bontás (33-43 vs 50-65)
    w1_e = [e for e in all_e if 33 <= (e.get("minute") or 0) <= 43]
    w2_e = [e for e in all_e if 50 <= (e.get("minute") or 0) <= 65]
    w1_hit = sum(1 for e in w1_e if e.get("won")) / len(w1_e) * 100 if w1_e else 0
    w2_hit = sum(1 for e in w2_e if e.get("won")) / len(w2_e) * 100 if w2_e else 0

    # EV kalibráció: EV sávok szerinti bontás
    def ev_bucket(e):
        ev = e.get("ev", 0)
        if ev < 0.03:  return "2-3%"
        if ev < 0.05:  return "3-5%"
        if ev < 0.10:  return "5-10%"
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

    # Mai nap rövid összefoglaló
    today_won  = sum(1 for e in new_entries if e.get("won"))
    today_tot  = len(new_entries)
    today_line = f"Ma: {today_won}/{today_tot}" if today_tot else "Ma: nincs tipp"

    msg = (
        f"📊 <b>VISSZAMÉRÉS DASHBOARD</b>\n"
        f"━" * 22 + "\n"
        f"🎯 Összes: {wins}/{total} ({hit_all:.1f}%)\n"
        f"📅 {today_line}\n"
        f"\n"
        f"✅ VALUE tipek:    {v_wins}/{len(value_e)} ({v_hit:.1f}%)\n"
        f"⚠️ Nem-VALUE:       {nv_wins}/{len(no_value_e)} ({nv_hit:.1f}%)\n"
        f"\n"
        f"⏱ Ablak 33-43':   {sum(1 for e in w1_e if e.get('won'))}/{len(w1_e)} ({w1_hit:.1f}%)\n"
        f"⏱ Ablak 50-65':   {sum(1 for e in w2_e if e.get('won'))}/{len(w2_e)} ({w2_hit:.1f}%)\n"
        f"\n"
        f"🔬 EV kalibráció:\n{ev_lines}"
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

# ========= LIVE ODDS =========

def fetch_live_odds(fixture_id):
    try:
        r = requests.get(f"{BASE_URL}/odds/live", headers=HEADERS,
                         params={"fixture": fixture_id}, timeout=10)
        if r.status_code != 200: return None
        for item in r.json().get("response", []):
            for bm in item.get("odds", []):
                for bet in bm.get("bets", []):
                    if "total" not in (bet.get("name") or "").lower() and \
                       "goals" not in (bet.get("name") or "").lower(): continue
                    for val in bet.get("values", []):
                        if str(val.get("value") or "").lower() in ("over 1.5", "o 1.5", "over1.5"):
                            try: return float(val["odd"])
                            except: pass
    except Exception as e:
        log.warning(f"[fetch_live_odds] ({fixture_id}): {e}")
    return None

def build_odds_line(live_odds, prematch_odds, model_p, drift_info=None):
    fair_odds = round(1.0 / model_p, 2) if model_p and model_p > 0 else None
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

# ========= STATISZTIKAI MOTOR =========

def get_team_detailed_data(team_id):
    cache = load_json(TEAM_STATS_CACHE_FILE, {}, dict)
    if str(team_id) in cache: return cache[str(team_id)]
    try:
        matches = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10",
                                headers=HEADERS, timeout=12).json().get("response", [])
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
                st = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={m['fixture']['id']}&team={team_id}",
                                   headers=HEADERS, timeout=10).json().get("response", [])
                if st:
                    for stat in st[0].get('statistics', []):
                        if stat['type'] == 'Corner Kicks': corn_list.append(stat['value'] or 0)
        res = {"avg_scored": s/10, "avg_conceded": c/10, "btts_trend": btts_count,
               "corner_avg": sum(corn_list)/len(corn_list) if len(corn_list) >= 3 else None}
        cache[str(team_id)] = res
        save_json(TEAM_STATS_CACHE_FILE, cache)
        return res
    except Exception as e:
        log.warning(f"[team_data] ({team_id}): {e}"); return None

def get_live_shot_stats(fixture_id):
    try:
        sr = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={fixture_id}",
                          headers=HEADERS, timeout=10).json().get("response", [])
        sog = soff = da = corn = 0
        for ts in sr:
            for stat in ts.get("statistics", []):
                t = stat.get("type", ""); v = int(stat.get("value") or 0)
                if t == "Shots on Goal":       sog  += v
                elif t == "Shots off Goal":    soff += v
                elif t == "Dangerous Attacks": da   += v
                elif t == "Corner Kicks":      corn += v
        return {"shots_on_goal": sog, "shots_total": sog+soff, "dangerous_att": da, "corner_total": corn}
    except Exception as e:
        log.warning(f"[shot_stats] ({fixture_id}): {e}")
        return {"shots_on_goal": 0, "shots_total": 0, "dangerous_att": 0, "corner_total": 0}

def is_active_game(s):
    return (s["shots_on_goal"] >= SHOTS_ON_GOAL_MIN or
            s["shots_total"]   >= SHOTS_TOTAL_MIN or
            (s["dangerous_att"] >= DANGEROUS_ATT_MIN and s["shots_on_goal"] >= 1))

def in_live_window(e):
    return any(s <= e <= en for s, en in LIVE_WINDOWS)

def fetch_live_fixtures(max_retries=3, backoff=5):
    for attempt in range(max_retries):
        try:
            r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
            if r.status_code == 429:
                wait = backoff * (2 ** attempt)
                log.warning(f"[fetch_live] 429 – vár {wait}s"); time.sleep(wait); continue
            r.raise_for_status()
            return r.json().get("response", [])
        except requests.exceptions.Timeout:
            log.warning(f"[fetch_live] Timeout ({attempt+1}/{max_retries})")
        except requests.exceptions.RequestException as e:
            log.warning(f"[fetch_live] Hiba: {e}")
        if attempt < max_retries - 1: time.sleep(backoff * (attempt + 1))
    log.error("[fetch_live] Minden próbálkozás sikertelen.")
    return []

# ========= SZKENNER =========

def scan_next_day():
    tz = pytz.timezone(TIMEZONE)
    target = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    log.info(f"[scan] Deep Scan: {target}")
    send_telegram(f"🔬 <b>EXPERT v5.6 Deep Scan: {target}</b>")
    try:
        matches = requests.get(f"{BASE_URL}/fixtures?date={target}",
                                headers=HEADERS, timeout=30).json().get("response", [])
        log.info(f"[scan] {len(matches)} meccs")
        valid = []
        for m in matches:
            hd = get_team_detailed_data(m['teams']['home']['id'])
            ad = get_team_detailed_data(m['teams']['away']['id'])
            if not hd or not ad: continue
            ta = (hd['avg_scored'] + hd['avg_conceded'] + ad['avg_scored'] + ad['avg_conceded']) / 2
            op = (1 - (math.exp(-ta) * (1 + ta + (ta**2)/2))) * 100
            tips = []
            if op > 82: tips.append("Over 2.5")
            elif op > 68: tips.append("Over 1.5")
            if hd['avg_scored'] > 1.1 and ad['avg_scored'] > 1.1 and hd['btts_trend'] >= 2 and ad['btts_trend'] >= 2:
                tips.append("Over 2.5 & BTTS" if op > 80 else "BTTS")
            ci = "N/A"
            if hd['corner_avg'] and ad['corner_avg']:
                ec = hd['corner_avg'] + ad['corner_avg']; ci = round(ec, 1)
                if ec >= 10.5: tips.append("Corners Over 8.5")
                elif ec >= 9.2: tips.append("Corners Over 7.5")
            if tips:
                valid.append({"ID": m['fixture']['id'],
                               "IDŐPONT": datetime.fromisoformat(m['fixture']['date'][:19])
                                           .replace(tzinfo=pytz.utc).astimezone(tz).strftime('%H:%M'),
                               "BAJNOKSÁG": m['league']['name'].upper(),
                               "MECCS": f"{m['teams']['home']['name']} - {m['teams']['away']['name']}",
                               "OVER 2.5 ESÉLY": f"{round(op, 1)}%",
                               "VÁRHATÓ SZÖGLET": ci,
                               "TIPP JAVASLAT": " | ".join(tips)})
        log.info(f"[scan] {len(valid)} tipp: {target}")
        if valid:
            cache = load_json(CACHE_FILE, {}, dict)
            cache[target] = valid
            save_json(CACHE_FILE, cache)
            fn = f"expert_lista_{target}.xlsx"; pd.DataFrame(valid).to_excel(fn, index=False)
            send_telegram(f"✅ Deep Scan kész! ({len(valid)} tipp)", fn)
            sync_to_github([CACHE_FILE, fn, TEAM_STATS_CACHE_FILE], f"v5.6 Update: {target}")
    except Exception as e:
        log.error(f"[scan] Hiba: {e}"); send_telegram(f"⚠️ Scan hiba: {e}")

# ========= JELENTÉS ÉS TAKARÍTÁS =========

def get_final_report():
    tz = pytz.timezone(TIMEZONE)
    today_str = datetime.now(tz).strftime('%Y-%m-%d')
    yest      = (datetime.now(tz) - timedelta(days=1)).strftime('%Y-%m-%d')
    log.info(f"[report] Napi zárás: {yest}")
    cache = load_json(CACHE_FILE, {}, dict)
    matches = cache.get(yest, [])
    if not isinstance(matches, list):
        log.error(f"[report] Hibás matches típus a cache-ben: {type(matches).__name__}")
        matches = []
    if not matches:
        log.info("[report] Nincs adat tegnap.")
        send_daily_log_summary(); return

    send_telegram(f"📊 <b>Összetett jelentés ({yest})</b>")
    final = []
    for m in matches:
        try:
            r = requests.get(f"{BASE_URL}/fixtures?id={m['ID']}", headers=HEADERS)\
                         .json().get("response", [])
            if r:
                res = r[0]; h = res['goals']['home'] or 0; a = res['goals']['away'] or 0
                total_goals = h + a
                c_total = 0
                if 'statistics' in res:
                    for ss in res['statistics']:
                        for it in ss['statistics']:
                            if it['type'] == 'Corner Kicks': c_total += (it['value'] or 0)
                m["EREDMÉNY"] = f"{h}-{a}"
                tipp = m.get("TIPP JAVASLAT", "")
                if "Over 2.5" in tipp: m["GÓL SIKER"] = "✅" if total_goals > 2.5 else "❌"
                else:                  m["GÓL SIKER"] = "✅" if total_goals > 1.5 else "❌"
                m["BTTS SIKER"]  = "✅" if h > 0 and a > 0 else "❌"
                m["SZÖGLET ÖSSZ"] = c_total
                log.info(f"[report] {m['MECCS']}: {h}-{a} | {m['GÓL SIKER']}")
            final.append(m); time.sleep(1)
        except Exception as e:
            log.error(f"[report] Meccs hiba: {e}"); continue

    # Live history kigyűjtése + backtest frissítés
    live_history = load_json(LIVE_HISTORY_FILE, [], list)
    live_wins = 0
    if live_history:
        for lt in live_history:
            try:
                r = requests.get(f"{BASE_URL}/fixtures?id={lt['id']}", headers=HEADERS)\
                             .json().get("response", [])
                if r and (r[0]['goals']['home'] or 0) + (r[0]['goals']['away'] or 0) > 1.5:
                    live_wins += 1
            except: continue
        live_msg = f"📱 <b>LIVE ÖSSZESÍTŐ:</b>\n🎯 Küldött: {len(live_history)}\n✅ Nyert (O1.5): {live_wins}"
        log.info(f"[report] Live: {len(live_history)} tipp, {live_wins} nyert")
    else:
        live_msg = "📱 <b>LIVE ÖSSZESÍTŐ:</b>\nMa nem volt élő tipp."
        log.info("[report] Nincs live tipp.")

    fn = f"report_{yest}.xlsx"
    pd.DataFrame(final).to_excel(fn, index=False)
    send_telegram(live_msg, fn)

    # VISSZAMÉRÉS DASHBOARD
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
    log.info("Bot v5.6 elindult (Dashboard).")
    log.info(f"LIVE_MIN_EV={LIVE_MIN_EV} | WINDOWS={LIVE_WINDOWS}")
    log.info("=" * 50)
    while True:
        now = datetime.now(tz)
        if now.hour == 19 and now.minute == 0: scan_next_day();    time.sleep(61)
        if now.hour == 0  and now.minute == 10: get_final_report(); time.sleep(61)

        try:
            today_str   = now.strftime('%Y-%m-%d')
            now_str     = now.strftime('%H:%M')

            # ── Védett cache betöltés ──────────────────────────────────────
            cache_data = load_json(CACHE_FILE, {}, dict)
            today_m    = cache_data.get(today_str, [])
            if not isinstance(today_m, list):
                log.error(f"[main_loop] today_m hibás típus ({type(today_m).__name__}), kiürítve.")
                today_m = []

            sent_today  = load_sent_alerts(today_str)   # load_sent_alerts már védett
            master_tips = load_master_tips_for_today(today_str)  # load_master_tips_for_today már védett
            # ──────────────────────────────────────────────────────────────

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
                            send_telegram(f"📉 <b>ODDS DRIFT — {label}</b>\n"
                                          f"{di['prev']} → {lo} (-{di['pct']:.1f}%)\n"
                                          f"🟢 Smart money — erősödő piac!")
                        else:
                            send_telegram(f"📈 <b>ODDS DRIFT — {label}</b>\n"
                                          f"{di['prev']} → {lo} (+{di['pct']:.1f}%)\n"
                                          f"🟡 Gyengülő piac — óvatosság!")
                        continue

                    if str(mid) in sent_today:   continue
                    if not in_live_window(min_):
                        log.debug(f"[main_loop] {label} – {min_}' – ablakból kiesett"); continue

                    ss = get_live_shot_stats(mid)
                    if not is_active_game(ss):
                        log.debug(f"[main_loop] {label} – low activity, skip"); continue

                    if not master_tips:
                        log.warning(f"[main_loop] Nincs master tips – {today_str}"); continue
                    ev, model_p = get_ev_for_fixture(master_tips, mid)
                    if ev is None or ev < LIVE_MIN_EV:
                        log.debug(f"[main_loop] {label} – EV={ev}, skip"); continue

                    po = get_prematch_odds_for_fixture(master_tips, mid)
                    ol = build_odds_line(lo, po, model_p, di)

                    msg = (f"⚽ <b>LIVE: Over 1.5 🔥</b>\n{label}\n"
                           f"📍 {h}–{a} ({min_}. perc)\n"
                           f"🎯 Kapura tartó: {ss['shots_on_goal']} | Összes lövés: {ss['shots_total']}\n"
                           f"⚡ Veszélyes tám.: {ss['dangerous_att']}\n"
                           f"📊 EV: {ev*100:.1f}% | P: {f'{model_p*100:.1f}%' if model_p else 'N/A'}")
                    if ol: msg += f"\n{ol}"

                    send_telegram(msg)
                    log.info(f"[ALERT] {label} | {min_}' | EV={ev*100:.1f}% | odds={lo}")
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
    keep_alive(); main_loop()
