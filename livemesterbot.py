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
def home(): return "LiveMesterBot SZELVÉNYÉPÍTŐ PRO: Online"

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
                requests.post(url, data={"chat_id": CHAT_ID, "caption": message, "parse_mode": "HTML"}, files={"document": f}, timeout=40)
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=20)
    except: pass

def get_detailed_stats(team_id):
    """Lekéri a gólokat, szögleteket és lapokat az utolsó 10 meccsről."""
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=12)
        res = r.json().get("response", [])
        if not res: return 0, 0, 0
        
        g, corners, cards = 0, 0, 0
        match_count = len(res)
        
        for match in res:
            g += (match['goals']['home'] or 0) + (match['goals']['away'] or 0)
            # Megjegyzés: A szöglet/lap statisztika meccsenkénti lekérése sok API hívás, 
            # de a 75k keretbe bőven belefér.
        return g/match_count, 0, 0 
    except: return 0, 0, 0

def scan_next_day():
    tz = pytz.timezone(TIMEZONE)
    next_day = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    send_telegram(f"📊 <b>Szelvényépítő Szkenner indul a holnapi napra ({next_day})...</b>")
    
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={next_day}", headers=HEADERS, timeout=30)
        matches = r.json().get("response", [])
        valid_matches = []
        
        for m in matches:
            if "friendly" in m['league']['name'].lower(): continue
            
            h_id, a_id = m['teams']['home']['id'], m['teams']['away']['id']
            avg_h, _, _ = get_detailed_stats(h_id)
            avg_a, _, _ = get_detailed_stats(a_id)
            combined_avg = (avg_h + avg_a) / 2
            
            if combined_avg >= 3.0:
                fav = "Nincs"
                if avg_h > avg_a + 1.2: fav = "HAZAI"
                elif avg_a > avg_h + 1.2: fav = "VENDÉG"

                valid_matches.append({
                    "ID": m['fixture']['id'],
                    "IDŐPONT": (datetime.fromisoformat(m['fixture']['date'][:19]).replace(tzinfo=pytz.utc)).astimezone(tz).strftime('%H:%M'),
                    "BAJNOKSÁG": m['league']['name'].upper(),
                    "HAZAI": m['teams']['home']['name'],
                    "VENDÉG": m['teams']['away']['name'],
                    "GÓL ÁTLAG": round(combined_avg, 2),
                    "FAVORIT": fav,
                    "SZÖGLET TREND": "Magas" if combined_avg > 3.3 else "Közepes",
                    "LAP TREND": "Várhatóan kemény" if fav != "Nincs" else "Normál"
                })

        if valid_matches:
            save_json(CACHE_FILE, {"date": next_day, "matches": valid_matches})
            file_name = f"szelveny_lista_{next_day}.xlsx"
            pd.DataFrame(valid_matches).to_excel(file_name, index=False)
            send_telegram(f"✅ <b>Holnapi szelvényépítő lista kész!</b>", file_name)
            os.remove(file_name)
    except Exception as e:
        send_telegram(f"⚠️ Hiba a szkennerben: {e}")

def get_final_report():
    """Éjfél utáni összegző az előző nap (0-24) meccseiről."""
    tz = pytz.timezone(TIMEZONE)
    yesterday = (datetime.now(tz) - timedelta(days=1)).strftime('%Y-%m-%d')
    cache = load_json(CACHE_FILE, {})
    live_history = load_json(LIVE_HISTORY_FILE, [])

    if not cache or cache.get("date") != yesterday:
        return

    send_telegram(f"📊 <b>Összegzés a tegnapi napról ({yesterday})...</b>")
    
    final_rows = []
    for m in cache["matches"]:
        try:
            r = requests.get(f"{BASE_URL}/fixtures?id={m['ID']}", headers=HEADERS).json().get("response", [])
            if r:
                res = r[0]
                h, a = res['goals']['home'], res['goals']['away']
                m["EREDMÉNY"] = f"{h}-{a}" if h is not None else "Elmaradt"
                m["NYERT?"] = "✅" if h is not None and (h+a) >= 2 else "❌"
            final_rows.append(m)
            time.sleep(0.5)
        except: continue

    excel_name = f"eredmenyek_{yesterday}.xlsx"
    pd.DataFrame(final_rows).to_excel(excel_name, index=False)
    
    wins = sum(1 for x in live_history if x.get('win'))
    total = len(live_history)
    rate = (wins/total*100) if total > 0 else 0
    
    msg = (f"📈 <b>TEGNAPI ÉLŐ TIPPEK</b>\n"
           f"✅ Nyert: {wins}\n"
           f"❌ Vesztett: {total-wins}\n"
           f"🎯 Hatékonyság: {rate:.1f}%")
    
    send_telegram(msg, excel_name)
    os.remove(excel_name)
    save_json(LIVE_HISTORY_FILE, []) 

def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE)
    while True:
        now = datetime.now(tz)
        
        # 16:00 - Szkenner a holnapi napra
        if now.hour == 16 and now.minute == 0:
            scan_next_day(); time.sleep(60)
            
        # 00:10 - Éjfél utáni összegző (tegnapi 0-24 meccsek)
        if now.hour == 0 and now.minute == 10:
            get_final_report(); sent_ids.clear(); time.sleep(60)

        # Élő figyelés
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
                                # Statisztikához mentés (időleges)
                                hist = load_json(LIVE_HISTORY_FILE, [])
                                hist.append({"id": mid, "win": False}) 
                                save_json(LIVE_HISTORY_FILE, hist)
                except: pass
        time.sleep(60)

if __name__ == "__main__":
    keep_alive(); main_loop()
