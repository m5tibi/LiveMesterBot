import requests
import time
import os
from datetime import datetime
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot PRO: Szakaszos Multisport Üzemmód"

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

SPORTS_CONFIG = {
    "FOCI": {"url": "https://v3.football.api-sports.io", "min_avg": 2.5, "limit": 999},
    "KOSÁR": {"url": "https://v1.basketball.api-sports.io", "min_avg": 160, "limit": 100},
    "HOKI": {"url": "https://v1.hockey.api-sports.io", "min_avg": 5.0, "limit": 100}
}

HEADERS = {"x-apisports-key": API_KEY}

# Globális tárolók a napközbeni adatokhoz
daily_cache = {"FOCI": [], "KOSÁR": [], "HOKI": []}
daily_football_targets = {}

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

def get_local_time(iso_date):
    try:
        clean_date = iso_date.replace('Z', '').split('+')[0]
        dt = datetime.fromisoformat(clean_date).replace(tzinfo=pytz.utc)
        return dt.astimezone(pytz.timezone(TIMEZONE)).strftime('%H:%M')
    except: return "??:??"

def get_team_avg(sport, url, team_id):
    last_n = 10 if sport == "FOCI" else 8
    try:
        endpoint = "/fixtures" if sport == "FOCI" else "/games"
        r = requests.get(f"{url}{endpoint}?team={team_id}&last={last_n}", headers=HEADERS, timeout=10)
        res = r.json().get("response", [])
        if not res: return 0
        total = sum((g.get('goals', {}).get('home') or 0) + (g.get('goals', {}).get('away') or 0) if sport == "FOCI" else (g.get('scores', {}).get('home', {}).get('total') or 0) + (g.get('scores', {}).get('away', {}).get('total') or 0) for g in res)
        return total / len(res)
    except: return 0

def scan_sport(sport):
    """Egy konkrét sportág szkennelése és excel küldése."""
    global daily_cache, daily_football_targets
    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).strftime('%Y-%m-%d')
    config = SPORTS_CONFIG[sport]
    url = config["url"]
    
    send_telegram(f"🔍 <b>{sport} Szkenner indul...</b>")
    sport_rows = []
    
    try:
        endpoint = "/fixtures" if sport == "FOCI" else "/games"
        r = requests.get(f"{url}{endpoint}?date={today}", headers=HEADERS, timeout=20)
        matches = r.json().get("response", [])
        
        for m in matches[:config["limit"]]:
            try:
                home, away = m['teams']['home'], m['teams']['away']
                mid = m['fixture']['id'] if sport == "FOCI" else m['id']
                start_iso = m['fixture']['date'] if sport == "FOCI" else m['date']
                
                avg_h = get_team_avg(sport, url, home['id'])
                avg_a = get_team_avg(sport, url, away['id'])
                combined_avg = (avg_h + avg_a) / 2
                
                if combined_avg >= config["min_avg"]:
                    favorit_side = "Nincs"
                    if sport == "FOCI":
                        if avg_h > avg_a + 1.2: favorit_side = "HAZAI"
                        elif avg_a > avg_h + 1.2: favorit_side = "VENDÉG"
                        if combined_avg >= 2.8 or favorit_side != "Nincs":
                            daily_football_targets[mid] = favorit_side

                    match_data = {
                        "SPORT": sport, "ID": mid, "IDŐPONT (HU)": get_local_time(start_iso),
                        "BAJNOKSÁG": m['league']['name'].upper(), "HAZAI": home['name'],
                        "VENDÉG": away['name'], "ÁTLAG": round(combined_avg, 2), "FAVORIT": favorit_side
                    }
                    sport_rows.append(match_data)
            except: continue
            
        if sport_rows:
            daily_cache[sport] = sport_rows
            file_name = f"{sport}_lista_{today}.xlsx"
            pd.DataFrame(sport_rows).to_excel(file_name, index=False)
            send_telegram(f"✅ <b>{sport} lista kész!</b>", file_name)
            if os.path.exists(file_name): os.remove(file_name)
    except:
        send_telegram(f"⚠️ Hiba történt a {sport} szkenner közben.")

def report_results(sport):
    """Egy konkrét sportág eredményeinek összegzése este."""
    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).strftime('%Y-%m-%d')
    matches = daily_cache.get(sport, [])
    if not matches: return

    send_telegram(f"📊 <b>{sport} Esti összegző generálása...</b>")
    results_rows = []
    
    for m in matches:
        try:
            url = SPORTS_CONFIG[sport]["url"]
            endpoint = f"/fixtures?id={m['ID']}" if sport == "FOCI" else f"/games?id={m['ID']}"
            r = requests.get(url + endpoint, headers=HEADERS, timeout=10).json().get("response", [])
            
            if r:
                game = r[0]
                h = game['goals']['home'] if sport == "FOCI" else game['scores']['home']['total']
                a = game['goals']['away'] if sport == "FOCI" else game['scores']['away']['total']
                
                if h is not None and a is not None:
                    m["EREDMÉNY"] = f"{h}-{a}"
                    m["STATUSZ"] = "✅" if (h+a) >= (1.5 if sport == "FOCI" else m['ÁTLAG']) else "❌"
                results_rows.append(m)
            time.sleep(1)
        except: continue

    if results_rows:
        file_name = f"{sport}_eredmenyek_{today}.xlsx"
        pd.DataFrame(results_rows).to_excel(file_name, index=False)
        send_telegram(f"📊 <b>{sport} lezárt napi táblázat:</b>", file_name)
        if os.path.exists(file_name): os.remove(file_name)

def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE)
    # Első indításnál (ha nem hajnal van) csak egy gyors szkennert futtathatunk kézzel vagy hagyjuk
    
    while True:
        now = datetime.now(tz)
        
        # --- REGGELI SZAKASZOS SZKENNER ---
        if now.hour == 4 and now.minute == 0: scan_sport("FOCI")
        if now.hour == 4 and now.minute == 30: scan_sport("KOSÁR")
        if now.hour == 5 and now.minute == 0: scan_sport("HOKI")
        
        # --- ESTI SZAKASZOS ÖSSZEGZŐ ---
        if now.hour == 23 and now.minute == 0: report_results("FOCI")
        if now.hour == 23 and now.minute == 20: report_results("KOSÁR")
        if now.hour == 23 and now.minute == 40: report_results("HOKI")

        # --- ÉLŐ FOCI FIGYELÉS ---
        if 6 <= now.hour <= 23:
            try:
                r = requests.get(f"{SPORTS_CONFIG['FOCI']['url']}/fixtures?live=all", headers=HEADERS, timeout=10)
                for fx in r.json().get("response", []):
                    mid = fx["fixture"]["id"]
                    if mid in daily_football_targets and mid not in sent_ids:
                        minute = fx["fixture"]["status"]["elapsed"] or 0
                        h_g, a_g = (fx["goals"]["home"] or 0), (fx["goals"]["away"] or 0)
                        fav = daily_football_targets[mid]
                        
                        if (fav == "HAZAI" and a_g > h_g) or (fav == "VENDÉG" and h_g > a_g):
                            if 25 < minute < 75:
                                send_telegram(f"⭐ <b>FAVORIT HÁTRÁNYBAN!</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\n{h_g}-{a_g} ({minute}. perc)\n🎯 Tipp: {'1X' if fav=='HAZAI' else 'X2'}")
                                sent_ids.add(mid)
            except: pass

        time.sleep(60)

if __name__ == "__main__":
    keep_alive(); main_loop()
