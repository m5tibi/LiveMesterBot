import subprocess, requests, time, os, json, math
from datetime import datetime, timedelta
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

# ========= RENDER ÉBREN TARTÓ =========
app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot EXPERT v4.0.1: Order Fix Aktív"

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

# ========= ALAP SEGÉDFÜGGVÉNYEK (ELŐRE HOZVA) =========

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

# ========= ANALITIKA ÉS STATISZTIKA =========

def get_detailed_stats(team_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=12)
        res = r.json().get("response", [])
        if not res: return 0, 0, 0, 0, 5.0
        s, c, cs, p, corn = 0, 0, 0, 0, 0
        for g in res:
            is_h = g['teams']['home']['id'] == team_id
            scored = g['goals']['home'] if is_h else g['goals']['away']
            conceded = g['goals']['away'] if is_h else g['goals']['home']
            s += (scored or 0); c += (conceded or 0)
            if (conceded or 0) == 0: cs += 1
            if (scored or 0) > (conceded or 0): p += 3
            elif (scored or 0) == (conceded or 0): p += 1
        # Szöglet átlag fix 5.0, amíg nincs mélyebb statisztika
        return s/10, c/10, cs, p, 5.0
    except: return 0, 0, 0, 0, 5.0

# ========= FŐ FELADATOK =========

def scan_next_day():
    tz = pytz.timezone(TIMEZONE)
    target = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    send_telegram(f"🔬 <b>EXPERT v4.0.1 Analízis: {target}</b>")
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={target}", headers=HEADERS, timeout=30)
        matches = r.json().get("response", [])
        valid = []
        for m in matches:
            if "friendly" in m['league']['name'].lower(): continue
            h_id, a_id = m['teams']['home']['id'], m['teams']['away']['id']
            h_s, h_c, h_cs, h_p, h_corn = get_detailed_stats(h_id)
            a_s, a_c, a_cs, a_p, a_corn = get_detailed_stats(a_id)
            
            total_avg = (h_s + h_c + a_s + a_c) / 2
            # Poisson-alapú valószínűség (Over 2.5)
            over_prob = (1 - (math.exp(-total_avg) * (1 + total_avg + (total_avg**2)/2))) * 100
            
            exp_corners = h_corn + a_corn
            corner_limit = math.floor(exp_corners) - 0.5 if exp_corners > 8 else 8.5

            tips = []
            if over_prob > 78: tips.append("Over 2.5")
            elif over_prob > 65: tips.append("Over 1.5")
            
            if h_s > 1.2 and a_s > 1.2:
                if over_prob > 75: tips.append("Over 2.5 & BTTS")
                elif total_avg > 2.8: tips.append("BTTS")
            
            if exp_corners >= 9.0: tips.append(f"Corners Over {corner_limit}")

            if tips:
                valid.append({
                    "ID": m['fixture']['id'],
                    "IDŐPONT": (datetime.fromisoformat(m['fixture']['date'][:19]).replace(tzinfo=pytz.utc)).astimezone(tz).strftime('%H:%M'),
                    "BAJNOKSÁG": m['league']['name'].upper(),
                    "MECCS": f"{m['teams']['home']['name']} - {m['teams']['away']['name']}",
                    "OVER 2.5 ESÉLY": f"{round(over_prob, 1)}%",
                    "VÁRHATÓ SZÖGLET": exp_corners,
                    "TIPP JAVASLAT": " | ".join(tips)
                })
        
        if valid:
            cache = load_json(CACHE_FILE, {})
            cache[target] = valid
            save_json(CACHE_FILE, cache)
            f_name = f"expert_lista_{target}.xlsx"
            pd.DataFrame(valid).to_excel(f_name, index=False)
            send_telegram(f"✅ Lista kész!", f_name)
            sync_to_github([CACHE_FILE, f_name], f"Expert Update: {target}")
    except Exception as e: 
        send_telegram(f"⚠️ Hiba a szkennerben: {e}")

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
                m["EREDMÉNY"] = f"{h}-{a}"
                m["GÓL SIKER"] = "✅" if (h+a) >= 2.5 else "❌"
                m["BTTS SIKER"] = "✅" if (h or 0) > 0 and (a or 0) > 0 else "❌"
            final.append(m); time.sleep(1)
        except: continue

    f_name = f"expert_report_{yest}.xlsx"
    pd.DataFrame(final).to_excel(f_name, index=False)
    
    live_history = load_json(LIVE_HISTORY_FILE, [])
    total = len(live_history)
    live_msg = f"📱 <b>LIVE TIPPEK ÖSSZESÍTŐ:</b>\n🎯 Ma küldött élő tippek száma: {total}\n(Részletek az Excelben)"
    send_telegram(live_msg, f_name)
    save_json(LIVE_HISTORY_FILE, [])
    sync_to_github([f_name], f"Final Report: {yest}")

# ========= IDŐZÍTŐ =========

def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE)
    print("Bot eseményhurok elindult...")
    while True:
        now = datetime.now(tz)
        # 18:00 - Szkenner (A teszt kedvéért most 18-ra hagyva)
        if now.hour == 18 and now.minute == 15:
            scan_next_day()
            time.sleep(61)
            
        if now.hour == 0 and now.minute == 10:
            get_final_report()
            sent_ids.clear()
            time.sleep(61)
        
        # Élő figyelés
        try:
            cache = load_json(CACHE_FILE, {})
            today_m = cache.get(now.strftime('%Y-%m-%d'), [])
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
                            hst = load_json(LIVE_HISTORY_FILE, [])
                            hst.append({"id": mid, "time": now.strftime('%H:%M')})
                            save_json(LIVE_HISTORY_FILE, hst)
        except: pass
        time.sleep(40)

if __name__ == "__main__":
    keep_alive(); main_loop()
