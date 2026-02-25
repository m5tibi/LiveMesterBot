import subprocess
import requests
import time
import os
import json
from datetime import datetime, timedelta
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

# ========= RENDER ÉBREN TARTÓ =========
app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot PRO: GitHub Szinkronizáció Aktív"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server); t.daemon = True; t.start()

# ========= KONFIGURÁCIÓ =========
API_KEY = os.environ.get("FOOTBALL_API_KEY", "IDE_AZ_API_KULCSOT")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "IDE_A_TG_TOKENT")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "IDE_A_CHAT_ID-T")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
TIMEZONE = "Europe/Budapest"

REPO_URL = "https://github.com/m5tibi/LiveMesterBot.git"
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

CACHE_FILE = "foci_master_cache.json"
LIVE_HISTORY_FILE = "live_history.json"

# ========= GITHUB SZINKRONIZÁCIÓ =========
def sync_to_github(file_list, commit_message):
    if not GITHUB_TOKEN:
        print("Nincs GITHUB_TOKEN, mentés csak lokálisan.")
        return
    try:
        subprocess.run(["git", "config", "--global", "user.email", "bot@livemester.com"])
        subprocess.run(["git", "config", "--global", "user.name", "LiveMesterBot"])
        
        authenticated_url = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")
        
        for file in file_list:
            if os.path.exists(file):
                subprocess.run(["git", "add", file])
        
        subprocess.run(["git", "commit", "-m", commit_message])
        subprocess.run(["git", "push", authenticated_url])
        print(f"GitHub szinkronizáció kész: {commit_message}")
    except Exception as e:
        print(f"GitHub hiba: {e}")

# ========= SEGÉDFÜGGVÉNYEK =========
def save_json(file, data):
    with open(file, 'w') as f: json.dump(data, f)

def load_json(file, default):
    if os.path.exists(file):
        try:
            with open(file, 'r') as f: return json.load(f)
        except: return default
    return default

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

def get_team_detailed_stats(team_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=12)
        res = r.json().get("response", [])
        if not res: return 0, 0, 0
        
        total_g, scored_g, pts = 0, 0, 0
        for g in res:
            is_h = g['teams']['home']['id'] == team_id
            f = g['goals']['home'] if is_h else g['goals']['away']
            a = g['goals']['away'] if is_h else g['goals']['home']
            total_g += (g['goals']['home'] or 0) + (g['goals']['away'] or 0)
            scored_g += (f or 0)
            if (f or 0) > (a or 0): pts += 3
            elif (f or 0) == (a or 0): pts += 1
        return total_g/len(res), scored_g/len(res), pts
    except: return 0, 0, 0

def generate_suggestions(avg, fav, s_h, s_a):
    t = []
    if avg >= 3.0: t.append("Over 2.5 gól")
    if avg >= 3.6: t.append("Over 3.5 gól")
    if fav == "HAZAI":
        t.append("1X + Over 1.5")
        if s_h > 1.8: t.append("Hazai csapat > 1.5 gól")
    elif fav == "VENDÉG":
        t.append("X2 + Over 1.5")
        if s_a > 1.8: t.append("Vendég csapat > 1.5 gól")
    return " | ".join(t) if t else "Over 1.5 gól"

# ========= FŐ FELADATOK =========
def scan_next_day():
    tz = pytz.timezone(TIMEZONE)
    target = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    send_telegram(f"📊 <b>Szelvényépítő Szkenner: {target}</b>")
    
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={target}", headers=HEADERS, timeout=30)
        matches = r.json().get("response", [])
        valid = []
        for m in matches:
            if "friendly" in m['league']['name'].lower(): continue
            h_id, a_id = m['teams']['home']['id'], m['teams']['away']['id']
            avg_h, sc_h, p_h = get_team_detailed_stats(h_id)
            avg_a, sc_a, p_a = get_team_detailed_stats(a_id)
            comb_avg = (avg_h + avg_a) / 2
            
            if comb_avg >= 3.0:
                fav = "Nincs"
                if p_h >= p_a + 8 and sc_h > sc_a: fav = "HAZAI"
                elif p_a >= p_h + 8 and sc_a > sc_h: fav = "VENDÉG"
                
                valid.append({
                    "ID": m['fixture']['id'],
                    "IDŐPONT": (datetime.fromisoformat(m['fixture']['date'][:19]).replace(tzinfo=pytz.utc)).astimezone(tz).strftime('%H:%M'),
                    "BAJNOKSÁG": m['league']['name'].upper(),
                    "HAZAI": m['teams']['home']['name'], "VENDÉG": m['teams']['away']['name'],
                    "ÖSSZ. GÓL ÁTLAG": round(comb_avg, 2), "FAVORIT": fav,
                    "JAVASOLT TIPPEK": generate_suggestions(comb_avg, fav, sc_h, sc_a),
                    "FORMA (H-V)": f"{p_h}-{p_a} pont"
                })

        if valid:
            cache = load_json(CACHE_FILE, {})
            cache[target] = valid
            save_json(CACHE_FILE, cache)
            f_name = f"szelveny_lista_{target}.xlsx"
            pd.DataFrame(valid).to_excel(f_name, index=False)
            send_telegram(f"✅ Holnapi lista kész!", f_name)
            sync_to_github([CACHE_FILE, f_name], f"Napi lista: {target}")
            if os.path.exists(f_name): os.remove(f_name)
    except Exception as e: send_telegram(f"⚠️ Hiba: {e}")

def get_final_report():
    tz = pytz.timezone(TIMEZONE)
    today_str = (datetime.now(tz) - timedelta(days=1)).strftime('%Y-%m-%d')
    cache = load_json(CACHE_FILE, {})
    matches = cache.get(today_str, [])
    live_hist = load_json(LIVE_HISTORY_FILE, [])
    if not matches: return

    send_telegram(f"📊 <b>Napi jelentés ({today_str})</b>")
    final = []
    for m in matches:
        try:
            r = requests.get(f"{BASE_URL}/fixtures?id={m['ID']}", headers=HEADERS).json().get("response", [])
            if r:
                res = r[0]
                h, a = res['goals']['home'], res['goals']['away']
                m["VÉGEREDMÉNY"] = f"{h}-{a}" if h is not None else "N/A"
                m["NYERT?"] = "✅" if h is not None and (h+a) >= 2 else "❌"
            final.append(m)
            time.sleep(1)
        except: continue

    f_name = f"eredmenyek_{today_str}.xlsx"
    pd.DataFrame(final).to_excel(f_name, index=False)
    w = sum(1 for x in live_hist if x.get('win'))
    t = len(live_hist)
    msg = f"📈 <b>LIVE MÉRLEG</b>\n✅ Nyert: {w}\n❌ Vesztett: {t-w}\n🎯 {(w/t*100) if t>0 else 0:.1f}%"
    send_telegram(msg, f_name)
    sync_to_github([f_name, LIVE_HISTORY_FILE], f"Összegzés: {today_str}")
    save_json(LIVE_HISTORY_FILE, [])

# ========= IDŐZÍTŐ CIKLUS =========
def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE)
    while True:
        now = datetime.now(tz)
        if now.hour == 16 and now.minute == 0:
            scan_next_day(); time.sleep(60)
        if now.hour == 0 and now.minute == 10:
            get_final_report(); sent_ids.clear(); time.sleep(60)
        
        # Élő figyelés a napi szűrt listából
        if 0 <= now.hour <= 23:
            cache = load_json(CACHE_FILE, {})
            today_m = cache.get(now.strftime('%Y-%m-%d'), [])
            if today_m:
                t_ids = [m['ID'] for m in today_m]
                try:
                    r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
                    for fx in r.json().get("response", []):
                        mid = fx["fixture"]["id"]
                        if mid in t_ids and mid not in sent_ids:
                            min_ = fx["fixture"]["status"]["elapsed"] or 0
                            h, a = (fx["goals"]["home"] or 0), (fx["goals"]["away"] or 0)
                            if 25 < min_ < 65 and (h + a) < 2:
                                send_telegram(f"⚽ <b>ÉLŐ TIPP: Over 1.5</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\n{h}-{a} ({min_}. perc)")
                                sent_ids.add(mid)
                                hst = load_json(LIVE_HISTORY_FILE, [])
                                hst.append({"id": mid, "win": False}); save_json(LIVE_HISTORY_FILE, hst)
                except: pass
        time.sleep(60)

if __name__ == "__main__":
    keep_alive(); main_loop()
