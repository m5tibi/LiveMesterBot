import subprocess, requests, time, os, json, math
from datetime import datetime, timedelta
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

# ========= RENDER ÉBREN TARTÓ =========
app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot EXPERT v4.3.1: Syntax & Logic Fix"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server); t.daemon = True; t.start()

# ========= KONFIGURÁCIÓ =========
API_KEY = os.environ.get("FOOTBALL_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
REPO_URL = "https://github.com/m5tibi/LiveMesterBot.git"
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
TIMEZONE = "Europe/Budapest"

CACHE_FILE = "foci_master_cache.json"
LIVE_HISTORY_FILE = "live_history.json"
TEAM_CORNERS_CACHE = {} 

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

def sync_to_github(file_list, commit_message):
    if not GITHUB_TOKEN: return
    try:
        subprocess.run(["git", "config", "--global", "user.email", "bot@livemester.com"])
        subprocess.run(["git", "config", "--global", "user.name", "LiveMesterBot"])
        auth_url = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")
        subprocess.run(["git", "remote", "remove", "origin"], stderr=subprocess.DEVNULL)
        subprocess.run(["git", "remote", "add", "origin", auth_url])
        for f in file_list:
            if os.path.exists(f): subprocess.run(["git", "add", f])
        subprocess.run(["git", "commit", "-m", commit_message])
        subprocess.run(["git", "push", "origin", "HEAD:main", "--force"])
    except: pass

# ========= STATISZTIKAI MOTOR =========

def get_team_last_5_corners(team_id):
    if team_id in TEAM_CORNERS_CACHE: return TEAM_CORNERS_CACHE[team_id]
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=5", headers=HEADERS, timeout=12)
        matches = r.json().get("response", [])
        if not matches: return None
        
        corners_list = []
        for m in matches:
            fid = m['fixture']['id']
            sr = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={fid}&team={team_id}", headers=HEADERS, timeout=10)
            stats = sr.json().get("response", [])
            if stats:
                for s in stats[0].get('statistics', []):
                    if s['type'] == 'Corner Kicks':
                        corners_list.append(s['value'] or 0)
        
        if len(corners_list) >= 3:
            avg = sum(corners_list) / len(corners_list)
            TEAM_CORNERS_CACHE[team_id] = avg
            return avg
        return None
    except: return None

def get_basic_stats(team_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=12)
        res = r.json().get("response", [])
        if not res: return 0, 0, 0, 0
        s, c, cs, p = 0, 0, 0, 0
        for g in res:
            is_h = g['teams']['home']['id'] == team_id
            scored = g['goals']['home'] if is_h else g['goals']['away']
            conceded = g['goals']['away'] if is_h else g['goals']['home']
            s += (scored or 0); c += (conceded or 0)
            if (conceded or 0) == 0: cs += 1
            if (scored or 0) > (conceded or 0): p += 3
            elif (scored or 0) == (conceded or 0): p += 1
        return s/10, c/10, cs, p
    except: return 0, 0, 0, 0

# ========= FŐ FELADATOK =========

def scan_next_day():
    tz = pytz.timezone(TIMEZONE)
    target = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    send_telegram(f"🔬 <b>EXPERT v4.3.1 Analízis: {target}</b>")
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={target}", headers=HEADERS, timeout=30)
        matches = r.json().get("response", [])
        valid = []
        for m in matches:
            if "friendly" in m['league']['name'].lower(): continue
            h_id, a_id = m['teams']['home']['id'], m['teams']['away']['id']
            
            h_s, h_c, h_cs, h_p = get_basic_stats(h_id)
            a_s, a_c, a_cs, a_p = get_basic_stats(a_id)
            
            total_avg = (h_s + h_c + a_s + a_c) / 2
            over_prob = (1 - (math.exp(-total_avg) * (1 + total_avg + (total_avg**2)/2))) * 100
            
            h_corn = get_team_last_5_corners(h_id)
            a_corn = get_team_last_5_corners(a_id)
            
            tips = []
            if over_prob > 78: tips.append("Over 2.5")
            elif over_prob > 65: tips.append("Over 1.5")
            
            if h_s > 1.2 and a_s > 1.2:
                if over_prob > 75: tips.append("Over 2.5 & BTTS")
                elif total_avg > 2.8: tips.append("BTTS")
            
            corner_info = "N/A"
            if h_corn is not None and a_corn is not None:
                exp_corners = h_corn + a_corn
                corner_info = round(exp_corners, 1)
                if exp_corners >= 10.5: tips.append("Corners Over 8.5")
                elif exp_corners >= 9.0: tips.append("Corners Over 7.5")

            if tips:
                valid.append({
                    "ID": m['fixture']['id'],
                    "IDŐPONT": (datetime.fromisoformat(m['fixture']['date'][:19]).replace(tzinfo=pytz.utc)).astimezone(tz).strftime('%H:%M'),
                    "BAJNOKSÁG": m['league']['name'].upper(),
                    "MECCS": f"{m['teams']['home']['name']} - {m['teams']['away']['name']}",
                    "OVER 2.5 ESÉLY": f"{round(over_prob, 1)}%",
                    "VÁRHATÓ SZÖGLET": corner_info,
                    "TIPP JAVASLAT": " | ".join(tips)
                })
        
        if valid:
            cache = load_json(CACHE_FILE, {})
            cache[target] = valid; save_json(CACHE_FILE, cache)
            f_name = f"expert_lista_{target}.xlsx"
            pd.DataFrame(valid).to_excel(f_name, index=False)
            send_telegram(f"✅ Lista kész!", f_name)
            sync_to_github([CACHE_FILE, f_name], f"v4.3.1 Update: {target}")
    except Exception as e: send_telegram(f"⚠️ Hiba: {e}")

def get_final_report():
    tz = pytz.timezone(TIMEZONE)
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
                c_total = 0
                if 'statistics' in res:
                    for s_set in res['statistics']:
                        for it in s_set['statistics']:
                            if it['type'] == 'Corner Kicks': c_total += (it['value'] or 0)
                m["EREDMÉNY"] = f"{h}-{a}"
                m["GÓL SIKER"] = "✅" if (h+a) >= 2.5 else "❌"
                m["BTTS SIKER"] = "✅" if (h or 0) > 0 and (a or 0) > 0 else "❌"
                m["SZÖGLET ÖSSZ"] = c_total
            final.append(m); time.sleep(1)
        except: continue
    f_name = f"expert_report_{yest}.xlsx"
    pd.DataFrame(final).to_excel(f_name, index=False)
    send_telegram(f"📈 Kiértékelés lezárva.", f_name)
    sync_to_github([f_name], f"Final: {yest}")

def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE)
    while True:
        now = datetime.now(tz)
        # Tesztelés 17:30-kor (javítva a SyntaxError)
        if now.hour == 17 and now.minute == 00:
            scan_next_day()
            time.sleep(61)
            
        if now.hour == 0 and now.minute == 10:
            get_final_report(); sent_ids.clear(); time.sleep(61)
            
        try:
            today_m = load_json(CACHE_FILE, {}).get(now.strftime('%Y-%m-%d'), [])
            if today_m:
                t_ids = [m['ID'] for m in today_m]
                r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
                for fx in r.json().get("response", []):
                    mid = fx["fixture"]["id"]
                    if mid in t_ids and mid not in sent_ids:
                        min_ = fx["fixture"]["status"]["elapsed"] or 0
                        h, a = (fx["goals"]["home"] or 0), (fx["goals"]["away"] or 0)
                        if 30 < min_ < 60 and (h+a) < 2:
                            send_telegram(f"⚽ <b>LIVE: Over 1.5</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\n{h}-{a} ({min_}. perc)")
                            sent_ids.add(mid)
                            hst = load_json(LIVE_HISTORY_FILE, []); hst.append({"id": mid}); save_json(LIVE_HISTORY_FILE, hst)
        except: pass
        time.sleep(40)

if __name__ == "__main__":
    keep_alive(); main_loop()
