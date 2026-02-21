import requests
import time
import os
import json
from datetime import datetime, timedelta
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot FOCI PRO: Aktív"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server); t.daemon = True; t.start()

# ========= KONFIGURÁCIÓ =========
API_KEY = os.environ.get("FOOTBALL_API_KEY", "IDE_AZ_API_KULCSOT")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "IDE_A_TG_TOKENT")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "IDE_A_CHAT_ID-T")
TIMEZONE = "Europe/Budapest"

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
CACHE_FILE = "foci_daily_cache.json"
LIVE_HISTORY_FILE = "live_tips_history.json"

def save_json(file, data):
    with open(file, 'w') as f: json.dump(data, f)

def load_json(file, default):
    if os.path.exists(file):
        with open(file, 'r') as f: return json.load(f)
    return default

def send_telegram(message, file_path=None):
    try:
        if file_path:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
            with open(file_path, 'rb') as f:
                requests.post(url, data={"chat_id": CHAT_ID, "caption": message, "parse_mode": "HTML"}, files={"document": f}, timeout=30)
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=15)
    except: pass

def get_team_avg(team_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=10)
        res = r.json().get("response", [])
        if not res: return 0
        total = sum((g['goals']['home'] or 0) + (g['goals']['away'] or 0) for g in res)
        return total / len(res)
    except: return 0

def scan_next_day():
    tz = pytz.timezone(TIMEZONE)
    next_day = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    
    send_telegram(f"🔍 <b>Esti szkenner indul a holnapi napra ({next_day})...</b>")
    
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={next_day}", headers=HEADERS, timeout=30)
        matches = r.json().get("response", [])
        
        valid_matches = []
        for m in matches:
            league = m['league']['name'].lower()
            if "friendly" in league: continue
            
            home_id, away_id = m['teams']['home']['id'], m['teams']['away']['id']
            avg = (get_team_avg(home_id) + get_team_avg(away_id)) / 2
            
            if avg >= 3.0:
                favorit = "Nincs"
                # Tabella alapú favorit keresés (egyszerűsítve az API hívások miatt)
                avg_h, avg_a = get_team_avg(home_id), get_team_avg(away_id)
                if avg_h > avg_a + 1.2: favorit = "HAZAI"
                elif avg_a > avg_h + 1.2: favorit = "VENDÉG"

                valid_matches.append({
                    "ID": m['fixture']['id'],
                    "IDŐPONT (HU)": (datetime.fromisoformat(m['fixture']['date'][:19]).replace(tzinfo=pytz.utc)).astimezone(tz).strftime('%H:%M'),
                    "BAJNOKSÁG": m['league']['name'].upper(),
                    "HAZAI": m['teams']['home']['name'],
                    "VENDÉG": m['teams']['away']['name'],
                    "ÁTLAG": round(avg, 2),
                    "FAVORIT": favorit
                })
        
        if valid_matches:
            save_json(CACHE_FILE, {"date": next_day, "matches": valid_matches})
            file_name = f"foci_lista_{next_day}.xlsx"
            pd.DataFrame(valid_matches).to_excel(file_name, index=False)
            send_telegram(f"✅ <b>Holnapi lista kész!</b>\n🎯 {len(valid_matches)} meccs szűrve (Avg > 3.0).", file_name)
            os.remove(file_name)
    except Exception as e:
        send_telegram(f"⚠️ Hiba a szkennerben: {e}")

def get_final_report():
    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).strftime('%Y-%m-%d')
    cache = load_json(CACHE_FILE, {})
    live_history = load_json(LIVE_HISTORY_FILE, [])

    if not cache or cache.get("date") != today:
        send_telegram("📊 Ma nem volt előre tervezett meccs.")
        return

    send_telegram("📊 <b>Napi összegző generálása...</b>")
    
    # 1. Excel frissítése eredményekkel
    final_rows = []
    for m in cache["matches"]:
        try:
            r = requests.get(f"{BASE_URL}/fixtures?id={m['ID']}", headers=HEADERS).json().get("response", [])
            if r:
                h, a = r[0]['goals']['home'], r[0]['goals']['away']
                m["EREDMÉNY"] = f"{h}-{a}" if h is not None else "Elmaradt"
                m["STATUSZ"] = "✅" if (h+a) >= 2 else "❌"
            final_rows.append(m)
            time.sleep(1)
        except: continue

    excel_name = f"eredmenyek_{today}.xlsx"
    pd.DataFrame(final_rows).to_excel(excel_name, index=False)
    
    # 2. Szöveges live statisztika
    wins = sum(1 for x in live_history if x['win'])
    total = len(live_history)
    rate = (wins/total*100) if total > 0 else 0
    
    msg = (f"📈 <b>ÉLŐ TIPPEK MÉRLEGE</b>\n"
           f"✅ Nyert: {wins}\n"
           f"❌ Vesztett: {total-wins}\n"
           f"🎯 Hatékonyság: {rate:.1f}%")
    
    send_telegram(msg, excel_name)
    os.remove(excel_name)
    save_json(LIVE_HISTORY_FILE, []) # Reset live history

def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE)
    while True:
        now = datetime.now(tz)
        
        # 20:00 - Szkenner a holnapi napra
        if now.hour == 20 and now.minute == 0:
            scan_next_day(); time.sleep(60)
            
        # 23:30 - Napi összegző
        if now.hour == 23 and now.minute == 30:
            get_final_report(); time.sleep(60)

        # Élő figyelés (csak a cache-elt meccsekre)
        if 0 <= now.hour <= 23:
            cache = load_json(CACHE_FILE, {})
            if cache.get("date") == now.strftime('%Y-%m-%d'):
                target_ids = [m['ID'] for m in cache['matches']]
                try:
                    r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
                    for fx in r.json().get("response", []):
                        mid = fx["fixture"]["id"]
                        if mid in target_ids and mid not in sent_ids:
                            minute = fx["fixture"]["status"]["elapsed"] or 0
                            h_g, a_g = (fx["goals"]["home"] or 0), (fx["goals"]["away"] or 0)
                            
                            if 25 < minute < 65 and (h_g + a_g) < 2:
                                send_telegram(f"⚽ <b>ÉLŐ TIPP: Over 1.5 gól</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\n{h_g}-{a_g} ({minute}. perc)")
                                sent_ids.add(mid)
                                # Mentés statisztikához (ideiglenes, az esti ellenőrzőhöz)
                                hist = load_json(LIVE_HISTORY_FILE, [])
                                hist.append({"id": mid, "win": False}) # Alapból false, az összegző frissíti
                                save_json(LIVE_HISTORY_FILE, hist)
                except: pass
        time.sleep(60)

if __name__ == "__main__":
    keep_alive(); main_loop()
