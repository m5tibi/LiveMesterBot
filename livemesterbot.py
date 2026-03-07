import subprocess, requests, time, os, json, math
from datetime import datetime, timedelta
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

# ========= RENDER ÉBREN TARTÓ =========
app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot EXPERT v4.4: Strict BTTS & Category Filter"

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
TEAM_STATS_CACHE = {} 

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

# ========= INTELLIGENS STATISZTIKAI MOTOR =========

def get_team_detailed_data(team_id):
    """Lekéri a gól és szöglet statisztikákat az utolsó meccsekből."""
    if team_id in TEAM_STATS_CACHE: return TEAM_STATS_CACHE[team_id]
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=12)
        matches = r.json().get("response", [])
        if not matches: return None
        
        s, c, corn_list, btts_count = 0, 0, [], 0
        last_5 = matches[:5]
        
        for i, m in enumerate(matches):
            is_h = m['teams']['home']['id'] == team_id
            scored = m['goals']['home'] if is_h else m['goals']['away']
            conceded = m['goals']['away'] if is_h else m['goals']['home']
            s += (scored or 0); c += (conceded or 0)
            
            # BTTS trend figyelése az utolsó 5 meccsen
            if i < 5 and (scored or 0) > 0 and (conceded or 0) > 0:
                btts_count += 1
                
            # Szögletek (csak az utolsó 5-nél, hogy spóroljunk a hívással)
            if i < 5:
                fid = m['fixture']['id']
                sr = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={fid}&team={team_id}", headers=HEADERS, timeout=10)
                stats = sr.json().get("response", [])
                if stats:
                    for stat in stats[0].get('statistics', []):
                        if stat['type'] == 'Corner Kicks':
                            corn_list.append(stat['value'] or 0)
        
        res = {
            "avg_scored": s/10,
            "avg_conceded": c/10,
            "btts_trend": btts_count, # Hány meccsen volt BTTS az utolsó 5-ből
            "corner_avg": sum(corn_list)/len(corn_list) if len(corn_list) >= 3 else None
        }
        TEAM_STATS_CACHE[team_id] = res
        return res
    except: return None

# ========= FŐ FELADATOK =========

def scan_next_day():
    tz = pytz.timezone(TIMEZONE)
    target = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    send_telegram(f"🔬 <b>EXPERT v4.4 Analízis: {target}</b>\n(Strict BTTS & Corner Logic)")
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={target}", headers=HEADERS, timeout=30)
        matches = r.json().get("response", [])
        valid = []
        for m in matches:
            league_name = m['league']['name'].lower()
            is_special = "women" in league_name or "u21" in league_name or "reserve" in league_name
            
            h_data = get_team_detailed_data(m['teams']['home']['id'])
            a_data = get_team_detailed_data(m['teams']['away']['id'])
            
            if not h_data or not a_data: continue
            
            total_avg = (h_data['avg_scored'] + h_data['avg_conceded'] + a_data['avg_scored'] + a_data['avg_conceded']) / 2
            over_prob = (1 - (math.exp(-total_avg) * (1 + total_avg + (total_avg**2)/2))) * 100
            
            tips = []
            # Gól logika szigorítva
            if over_prob > 82: tips.append("Over 2.5")
            elif over_prob > 68: tips.append("Over 1.5")
            
            # BTTS Szigorítás: Mindkét csapatnak lőnie kell, és a trendnek jónak kell lennie
            if h_data['avg_scored'] > 1.1 and a_data['avg_scored'] > 1.1:
                if h_data['btts_trend'] >= 2 and a_data['btts_trend'] >= 2:
                    if over_prob > 78: tips.append("Over 2.5 & BTTS")
                    else: tips.append("BTTS")
            
            # Szöglet logika
            corner_info = "N/A"
            if h_data['corner_avg'] is not None and a_data['corner_avg'] is not None:
                exp_corners = h_data['corner_avg'] + a_data['corner_avg']
                corner_info = round(exp_corners, 1)
                if exp_corners >= 10.5: tips.append("Corners Over 8.5")
                elif exp_corners >= 9.2: tips.append("Corners Over 7.5")

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
            send_telegram(f"✅ Szigorított lista kész!", f_name)
            sync_to_github([CACHE_FILE, f_name], f"v4.4 Update: {target}")
    except Exception as e: send_telegram(f"⚠️ Hiba: {e}")

# (get_final_report és main_loop marad a v4.3.1-es állapotban)
