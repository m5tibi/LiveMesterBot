import subprocess, requests, time, os, json, math
from datetime import datetime, timedelta
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

# ========= RENDER ÉBREN TARTÓ =========
app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot EXPERT v5.4: Restart-Safe"

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

# ========= SEGÉDFÜGGVÉNYEK =========

def send_telegram(message, file_path=None):
    try:
        if file_path:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
            with open(file_path, 'rb') as f:
                requests.post(url, data={"chat_id": CHAT_ID, "caption": message, "parse_mode": "HTML"}, files={"document": f}, timeout=45)
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=20)
    except: pass

def load_json(file, default):
    if os.path.exists(file):
        try:
            with open(file, 'r') as f: return json.load(f)
        except: return default
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
                subprocess.run(["git", "rm", df], stderr=subprocess.DEVNULL)
        for f in file_list:
            if os.path.exists(f): subprocess.run(["git", "add", f])
        subprocess.run(["git", "commit", "-m", commit_message])
        subprocess.run(["git", "push", "origin", "HEAD:main", "--force"])
    except: pass

# ========= SENT ALERTS – RESTART-BIZTOS DE-DUPLIKÁCIÓ =========
#
# A sent_alerts.json struktúrája:
# { "2026-03-31": ["fixture_id_1", "fixture_id_2", ...], "2026-04-01": [...] }
#
# Kulcs: dátum (YYYY-MM-DD) + fixture_id string
# Előny: Render restart után a fájl GitHub-ról visszatöltődik → nem küld dupla riasztást
# A get_final_report() csak a tegnapelőtti és régebbi bejegyzéseket törli → a mai napot őrzi

def load_sent_alerts(date_str):
    """Visszaadja a mai napra már elküldött fixture_id-k listáját."""
    data = load_json(SENT_ALERTS_FILE, {})
    return data.get(date_str, [])

def save_sent_alert(date_str, fixture_id):
    """Hozzáadja a fixture_id-t a mai nap listájához, majd elmenti és pushol."""
    data = load_json(SENT_ALERTS_FILE, {})
    day_list = data.get(date_str, [])
    fid_str = str(fixture_id)
    if fid_str not in day_list:
        day_list.append(fid_str)
        data[date_str] = day_list
        save_json(SENT_ALERTS_FILE, data)
        # Azonnal szinkronizál GitHub-ra – restart után is megmarad
        sync_to_github([SENT_ALERTS_FILE], f"sent_alert: {date_str}/{fid_str}")

def cleanup_sent_alerts(today_str):
    """
    Törli a 2 napnál régebbi bejegyzéseket a sent_alerts.json-ből.
    A mai és tegnapi napot megtartja (biztonsági puffer).
    """
    data = load_json(SENT_ALERTS_FILE, {})
    tz = pytz.timezone(TIMEZONE)
    cutoff = (datetime.now(tz) - timedelta(days=2)).strftime('%Y-%m-%d')
    cleaned = {k: v for k, v in data.items() if k >= cutoff}
    if cleaned != data:
        save_json(SENT_ALERTS_FILE, cleaned)

# ========= TAKARÍTÁS (7 NAPNÁL RÉGEBBI EXCEL + TIPS JSON) =========

def cleanup_old_files():
    files_to_delete = []
    now = datetime.now()
    cutoff = now - timedelta(days=7)
    for f in os.listdir('.'):
        if (f.startswith("expert_lista_") or f.startswith("report_")) and f.endswith(".xlsx"):
            try:
                date_str = f.split('_')[-1].split('.xlsx')[0]
                file_date = datetime.strptime(date_str, '%Y-%m-%d')
                if file_date < cutoff:
                    os.remove(f)
                    files_to_delete.append(f)
            except: pass
        if f.startswith(MASTER_TIPS_PREFIX) and f.endswith(".json"):
            try:
                date_str = f[len(MASTER_TIPS_PREFIX):].split('.json')[0]
                file_date = datetime.strptime(date_str, '%Y-%m-%d')
                if file_date < cutoff:
                    os.remove(f)
                    files_to_delete.append(f)
            except: pass
    return files_to_delete

# ========= MASTER BUILDER CACHE BETÖLTÉSE =========

def load_master_tips_for_today(date_str):
    fname = f"{MASTER_TIPS_PREFIX}{date_str}.json"
    data = load_json(fname, None)
    if data is None:
        return {}
    tips_by_id = {}
    for tip in data.get("tips", []):
        fid = tip.get("fixture_id")
        if fid is not None:
            tips_by_id[int(fid)] = tip
    return tips_by_id

def get_ev_for_fixture(master_tips, fixture_id):
    tip = master_tips.get(int(fixture_id))
    if tip is None:
        return None, None
    return tip.get("ev"), tip.get("model_p")

def get_prematch_odds_for_fixture(master_tips, fixture_id):
    tip = master_tips.get(int(fixture_id))
    if tip is None:
        return None
    odds = tip.get("odds") or {}
    return odds.get("over15")

# ========= LIVE ODDS LEKÉRÉSE =========

def fetch_live_odds(fixture_id):
    try:
        r = requests.get(
            f"{BASE_URL}/odds/live",
            headers=HEADERS,
            params={"fixture": fixture_id},
            timeout=10
        )
        if r.status_code != 200:
            return None
        resp = r.json().get("response", [])
        for item in resp:
            for bookmaker in item.get("odds", []):
                for bet in bookmaker.get("bets", []):
                    bet_name = (bet.get("name") or "").lower()
                    if "total" not in bet_name and "goals" not in bet_name:
                        continue
                    for val in bet.get("values", []):
                        label = str(val.get("value") or "").lower()
                        if label in ("over 1.5", "o 1.5", "over1.5"):
                            try:
                                return float(val["odd"])
                            except (ValueError, TypeError):
                                pass
    except Exception as e:
        print(f"[fetch_live_odds] Hiba ({fixture_id}): {e}")
    return None

def build_odds_line(live_odds, prematch_odds, model_p, drift_info=None):
    fair_odds = round(1.0 / model_p, 2) if model_p and model_p > 0 else None
    lines = []
    if live_odds is not None:
        value_ok  = fair_odds is not None and live_odds >= fair_odds
        value_str = "✅ VALUE" if value_ok else "⚠️ alacsony"
        fair_str  = f" | fair: {fair_odds}" if fair_odds else ""
        lines.append(f"💰 Live odds: <b>{live_odds}</b>{fair_str} → {value_str}")
    elif prematch_odds is not None:
        fair_str = f" | fair: {fair_odds}" if fair_odds else ""
        lines.append(f"💰 Pre-match odds: {prematch_odds}{fair_str} (live nem elérhető)")
    if drift_info:
        pct = drift_info["pct"]
        prev = drift_info["prev"]
        if drift_info["direction"] == "drop":
            lines.append(f"📉 <b>Odds esett:</b> {prev} → {live_odds} (-{pct:.1f}%) — smart money jel!")
        else:
            lines.append(f"📈 Odds emelkedett: {prev} → {live_odds} (+{pct:.1f}%) — gyengülő piac")
    return "\n".join(lines)

# ========= ODDS DRIFT KÖVETÉS =========

def check_odds_drift(fixture_id, current_odds, now_str):
    if current_odds is None:
        return None
    drift_cache = load_json(ODDS_DRIFT_FILE, {})
    key = str(fixture_id)
    prev_entry = drift_cache.get(key)
    drift_cache[key] = {"last_odds": current_odds, "ts": now_str}
    save_json(ODDS_DRIFT_FILE, drift_cache)
    if prev_entry is None:
        return None
    prev_odds = prev_entry.get("last_odds")
    if prev_odds is None or prev_odds <= 0:
        return None
    change_pct = (prev_odds - current_odds) / prev_odds
    if change_pct >= DRIFT_DROP_THRESHOLD:
        return {"prev": prev_odds, "pct": change_pct * 100, "direction": "drop"}
    elif change_pct <= -DRIFT_RISE_THRESHOLD:
        return {"prev": prev_odds, "pct": abs(change_pct) * 100, "direction": "rise"}
    return None

# ========= STATISZTIKAI MOTOR =========

def get_team_detailed_data(team_id):
    cache = load_json(TEAM_STATS_CACHE_FILE, {})
    if str(team_id) in cache: return cache[str(team_id)]
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=12)
        matches = r.json().get("response", [])
        if not matches: return None
        s, c, corn_list, btts_count = 0, 0, [], 0
        for i, m in enumerate(matches):
            is_h = m['teams']['home']['id'] == team_id
            scored = m['goals']['home'] if is_h else m['goals']['away']
            conceded = m['goals']['away'] if is_h else m['goals']['home']
            s += (scored or 0); c += (conceded or 0)
            if i < 5 and (scored or 0) > 0 and (conceded or 0) > 0: btts_count += 1
            if i < 5:
                fid = m['fixture']['id']
                sr = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={fid}&team={team_id}", headers=HEADERS, timeout=10)
                stats = sr.json().get("response", [])
                if stats:
                    for stat in stats[0].get('statistics', []):
                        if stat['type'] == 'Corner Kicks': corn_list.append(stat['value'] or 0)
        res = {
            "avg_scored": s/10, "avg_conceded": c/10, "btts_trend": btts_count,
            "corner_avg": sum(corn_list)/len(corn_list) if len(corn_list) >= 3 else None
        }
        cache[str(team_id)] = res
        save_json(TEAM_STATS_CACHE_FILE, cache)
        return res
    except: return None

# ========= LÖVÉS STATISZTIKA =========

def get_live_shot_stats(fixture_id):
    try:
        sr = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={fixture_id}", headers=HEADERS, timeout=10)
        stats_resp = sr.json().get("response", [])
        shots_on_goal = shots_off_goal = dangerous_att = corners = 0
        for team_stats in stats_resp:
            for stat in team_stats.get("statistics", []):
                t = stat.get("type", "")
                v = stat.get("value") or 0
                if t == "Shots on Goal":       shots_on_goal  += int(v)
                elif t == "Shots off Goal":    shots_off_goal += int(v)
                elif t == "Dangerous Attacks": dangerous_att  += int(v)
                elif t == "Corner Kicks":      corners        += int(v)
        return {"shots_on_goal": shots_on_goal, "shots_total": shots_on_goal + shots_off_goal,
                "dangerous_att": dangerous_att, "corner_total": corners}
    except:
        return {"shots_on_goal": 0, "shots_total": 0, "dangerous_att": 0, "corner_total": 0}

def is_active_game(shot_stats):
    sog = shot_stats.get("shots_on_goal", 0)
    st  = shot_stats.get("shots_total",  0)
    da  = shot_stats.get("dangerous_att", 0)
    return (sog >= SHOTS_ON_GOAL_MIN) or (st >= SHOTS_TOTAL_MIN) or ((da >= DANGEROUS_ATT_MIN) and (sog >= 1))

def in_live_window(elapsed):
    return any(start <= elapsed <= end for start, end in LIVE_WINDOWS)

# ========= LIVE FIXTURES RETRY =========

def fetch_live_fixtures(max_retries=3, backoff=5):
    for attempt in range(max_retries):
        try:
            r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
            if r.status_code == 429:
                time.sleep(backoff * (2 ** attempt)); continue
            r.raise_for_status()
            return r.json().get("response", [])
        except requests.exceptions.Timeout:
            print(f"[fetch_live] Timeout (kísérlet {attempt+1}/{max_retries})")
        except requests.exceptions.RequestException as e:
            print(f"[fetch_live] Hiba: {e}")
        if attempt < max_retries - 1:
            time.sleep(backoff * (attempt + 1))
    return []

# ========= SZKENNER =========

def scan_next_day():
    tz = pytz.timezone(TIMEZONE)
    target = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    send_telegram(f"🔬 <b>EXPERT v5.4 Deep Scan: {target}</b>")
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={target}", headers=HEADERS, timeout=30)
        matches = r.json().get("response", [])
        valid = []
        for m in matches:
            h_data = get_team_detailed_data(m['teams']['home']['id'])
            a_data = get_team_detailed_data(m['teams']['away']['id'])
            if not h_data or not a_data: continue
            total_avg = (h_data['avg_scored'] + h_data['avg_conceded'] + a_data['avg_scored'] + a_data['avg_conceded']) / 2
            over_prob = (1 - (math.exp(-total_avg) * (1 + total_avg + (total_avg**2)/2))) * 100
            tips = []
            if over_prob > 82: tips.append("Over 2.5")
            elif over_prob > 68: tips.append("Over 1.5")
            if h_data['avg_scored'] > 1.1 and a_data['avg_scored'] > 1.1 and h_data['btts_trend'] >= 2 and a_data['btts_trend'] >= 2:
                tips.append("Over 2.5 & BTTS" if over_prob > 80 else "BTTS")
            corner_info = "N/A"
            if h_data['corner_avg'] is not None and a_data['corner_avg'] is not None:
                exp_corners = h_data['corner_avg'] + a_data['corner_avg']
                corner_info = round(exp_corners, 1)
                if exp_corners >= 10.5: tips.append("Corners Over 8.5")
                elif exp_corners >= 9.2: tips.append("Corners Over 7.5")
            if tips:
                valid.append({"ID": m['fixture']['id'],
                               "IDŐPONT": (datetime.fromisoformat(m['fixture']['date'][:19]).replace(tzinfo=pytz.utc)).astimezone(tz).strftime('%H:%M'),
                               "BAJNOKSÁG": m['league']['name'].upper(),
                               "MECCS": f"{m['teams']['home']['name']} - {m['teams']['away']['name']}",
                               "OVER 2.5 ESÉLY": f"{round(over_prob, 1)}%",
                               "VÁRHATÓ SZÖGLET": corner_info,
                               "TIPP JAVASLAT": " | ".join(tips)})
        if valid:
            cache = load_json(CACHE_FILE, {})
            cache[target] = valid; save_json(CACHE_FILE, cache)
            f_name = f"expert_lista_{target}.xlsx"
            pd.DataFrame(valid).to_excel(f_name, index=False)
            send_telegram(f"✅ Deep Scan kész!", f_name)
            sync_to_github([CACHE_FILE, f_name, TEAM_STATS_CACHE_FILE], f"v5.4 Update: {target}")
    except Exception as e: send_telegram(f"⚠️ Hiba: {e}")

# ========= JELENTÉS ÉS TAKARÍTÁS =========

def get_final_report():
    tz = pytz.timezone(TIMEZONE)
    today_str = datetime.now(tz).strftime('%Y-%m-%d')
    yest = (datetime.now(tz) - timedelta(days=1)).strftime('%Y-%m-%d')
    cache = load_json(CACHE_FILE, {})
    matches = cache.get(yest, [])
    if not matches: return
    send_telegram(f"📊 <b>Összetett jelentés ({yest})</b>")
    final = []
    for m in matches:
        try:
            r = requests.get(f"{BASE_URL}/fixtures?id={m['ID']}", headers=HEADERS).json().get("response", [])
            if r:
                res = r[0]; h, a = res['goals']['home'], res['goals']['away']
                total_goals = (h or 0) + (a or 0)
                c_total = 0
                if 'statistics' in res:
                    for s_set in res['statistics']:
                        for it in s_set['statistics']:
                            if it['type'] == 'Corner Kicks': c_total += (it['value'] or 0)
                m["EREDMÉNY"] = f"{h}-{a}"
                tipp = m.get("TIPP JAVASLAT", "")
                if "Over 2.5" in tipp:
                    m["GÓL SIKER"] = "✅" if total_goals > 2.5 else "❌"
                elif "Over 1.5" in tipp:
                    m["GÓL SIKER"] = "✅" if total_goals > 1.5 else "❌"
                else:
                    m["GÓL SIKER"] = "✅" if total_goals > 1.5 else "❌"
                m["BTTS SIKER"] = "✅" if (h or 0) > 0 and (a or 0) > 0 else "❌"
                m["SZÖGLET ÖSSZ"] = c_total
            final.append(m); time.sleep(1)
        except: continue
    live_history = load_json(LIVE_HISTORY_FILE, [])
    live_wins = 0
    if live_history:
        for lt in live_history:
            try:
                r = requests.get(f"{BASE_URL}/fixtures?id={lt['id']}", headers=HEADERS).json().get("response", [])
                if r:
                    res = r[0]
                    if (res['goals']['home'] or 0) + (res['goals']['away'] or 0) > 1.5:
                        live_wins += 1
            except: continue
        live_msg = f"📱 <b>LIVE ÖSSZESÍTŐ:</b>\n🎯 Küldött: {len(live_history)}\n✅ Nyert (O1.5): {live_wins}"
    else:
        live_msg = "📱 <b>LIVE ÖSSZESÍTŐ:</b>\nMa nem volt élő tipp."
    f_name = f"report_{yest}.xlsx"
    pd.DataFrame(final).to_excel(f_name, index=False)
    send_telegram(live_msg, f_name)
    deleted_files = cleanup_old_files()
    save_json(LIVE_HISTORY_FILE, [])
    save_json(ODDS_DRIFT_FILE, {})
    # sent_alerts takarítás: régi napok törlése (mai nap megmarad)
    cleanup_sent_alerts(today_str)
    sync_to_github([f_name, LIVE_HISTORY_FILE, SENT_ALERTS_FILE, ODDS_DRIFT_FILE],
                   f"Final Report: {yest}", delete_files=deleted_files)

# ========= FŐ CIKLUS =========

def main_loop():
    tz = pytz.timezone(TIMEZONE)
    print("Bot v5.4 elindult (Restart-Safe)...")
    while True:
        now = datetime.now(tz)
        if now.hour == 19 and now.minute == 0:  scan_next_day();    time.sleep(61)
        if now.hour == 0  and now.minute == 10: get_final_report(); time.sleep(61)

        try:
            today_str = now.strftime('%Y-%m-%d')
            now_str   = now.strftime('%H:%M')
            today_m   = load_json(CACHE_FILE, {}).get(today_str, [])

            # Restart-biztos de-duplikáció: dátum+fixture_id alapon
            sent_today = load_sent_alerts(today_str)
            master_tips = load_master_tips_for_today(today_str)

            if today_m:
                t_ids = [m['ID'] for m in today_m]
                live_fixtures = fetch_live_fixtures()

                for fx in live_fixtures:
                    mid  = fx["fixture"]["id"]
                    min_ = fx["fixture"]["status"]["elapsed"] or 0
                    h, a = (fx["goals"]["home"] or 0), (fx["goals"]["away"] or 0)

                    if mid not in t_ids: continue
                    if (h + a) > 1:     continue

                    current_live_odds = fetch_live_odds(mid)
                    drift_info = check_odds_drift(mid, current_live_odds, now_str)

                    # Drift riasztás már elküldött meccsre
                    if str(mid) in sent_today and drift_info is not None:
                        match_label = f"{fx['teams']['home']['name']} – {fx['teams']['away']['name']}"
                        if drift_info["direction"] == "drop":
                            drift_msg = (f"📉 <b>ODDS DRIFT — {match_label}</b>\n"
                                         f"{drift_info['prev']} → {current_live_odds} (-{drift_info['pct']:.1f}%)\n"
                                         f"🟢 Smart money mozgás — erősödő piac!")
                        else:
                            drift_msg = (f"📈 <b>ODDS DRIFT — {match_label}</b>\n"
                                         f"{drift_info['prev']} → {current_live_odds} (+{drift_info['pct']:.1f}%)\n"
                                         f"🟡 Gyengülő piac — óvatosság!")
                        send_telegram(drift_msg)
                        continue

                    if str(mid) in sent_today:  continue
                    if not in_live_window(min_): continue

                    shot_stats = get_live_shot_stats(mid)
                    if not is_active_game(shot_stats): continue

                    if not master_tips: continue
                    ev, model_p = get_ev_for_fixture(master_tips, mid)
                    if ev is None or ev < LIVE_MIN_EV: continue

                    prematch_odds = get_prematch_odds_for_fixture(master_tips, mid)
                    odds_line     = build_odds_line(current_live_odds, prematch_odds, model_p, drift_info)

                    msg = (
                        f"⚽ <b>LIVE: Over 1.5 🔥</b>\n"
                        f"{fx['teams']['home']['name']} – {fx['teams']['away']['name']}\n"
                        f"📍 {h}–{a} ({min_}. perc)\n"
                        f"🎯 Kapura tartó: {shot_stats.get('shots_on_goal',0)} | Összes lövés: {shot_stats.get('shots_total',0)}\n"
                        f"⚡ Veszélyes tám.: {shot_stats.get('dangerous_att',0)}\n"
                        f"📊 EV: {ev*100:.1f}% | P: {f'{model_p*100:.1f}%' if model_p else 'N/A'}"
                    )
                    if odds_line:
                        msg += f"\n{odds_line}"

                    send_telegram(msg)

                    # Restart-biztos mentés: azonnal GitHub-ra szinkronizál
                    save_sent_alert(today_str, mid)

                    hst = load_json(LIVE_HISTORY_FILE, [])
                    hst.append({"id": mid, "time": now_str, "ev": ev, "model_p": model_p,
                                 "shots_on": shot_stats.get('shots_on_goal'),
                                 "shots_tot": shot_stats.get('shots_total'),
                                 "score_live": f"{h}-{a}", "minute": min_,
                                 "live_odds": current_live_odds, "prematch_odds": prematch_odds})
                    save_json(LIVE_HISTORY_FILE, hst)

        except Exception as e:
            print(f"[main_loop] Váratlan hiba: {e}")
        time.sleep(40)

if __name__ == "__main__":
    keep_alive(); main_loop()
