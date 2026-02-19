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
def home(): return "LiveMesterBot PRO: Stabilizálva"

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
    "KOSÁR": {"url": "https://v1.basketball.api-sports.io", "min_avg": 160, "limit": 30},
    "HOKI": {"url": "https://v1.hockey.api-sports.io", "min_avg": 5.0, "limit": 30}
}

HEADERS = {"x-apisports-key": API_KEY}
TG_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
SCAN_LOG_FILE = "last_scan.txt"

daily_football_targets = {}

def send_telegram(message: str, file_path=None):
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
    last_n = 10 if sport == "FOCI" else 5
    try:
        endpoint = "/fixtures" if sport == "FOCI" else "/games"
        r = requests.get(f"{url}{endpoint}?team={team_id}&last={last_n}", headers=HEADERS, timeout=10)
        games = r.json().get("response", [])
        if not games: return 0
        total = 0
        for g in games:
            if sport == "FOCI":
                total += (g.get('goals', {}).get('home') or 0) + (g.get('goals', {}).get('away') or 0)
            else:
                s = g.get('scores', {})
                total += (s.get('home', {}).get('total') or 0) + (s.get('away', {}).get('total') or 0)
        return total / len(games)
    except: return 0

def get_daily_fixtures(force=False):
    global daily_football_targets
    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).strftime('%Y-%m-%d')
    
    # Ellenőrzés, hogy ma futott-e már
    if not force and os.path.exists(SCAN_LOG_FILE):
        with open(SCAN_LOG_FILE, "r") as f:
            if f.read().strip() == today:
                print(f"[{today}] A mai szkenner már lefutott, átugrás.", flush=True)
                return

    excel_rows = []
    send_telegram(f"🔍 <b>PRO Szkenner indul: Foci + Kosár + Hoki</b>")

    for sport, config in SPORTS_CONFIG.items():
        try:
            print(f"Szkennelés: {sport}...", flush=True)
            url = config["url"]
            endpoint = "/fixtures" if sport == "FOCI" else "/games"
            r = requests.get(f"{url}{endpoint}?date={today}", headers=HEADERS, timeout=20)
            matches = r.json().get("response", [])
            
            for m in matches[:config["limit"]]:
                try:
                    home, away = m['teams']['home'], m['teams']['away']
                    start_iso = m['fixture']['date'] if sport == "FOCI" else m['date']
                    
                    avg_h = get_team_avg(sport, url, home['id'])
                    avg_a = get_team_avg(sport, url, away['id'])
                    combined_avg = (avg_h + avg_a) / 2
                    
                    if combined_avg >= config["min_avg"]:
                        favorit_side = "Nincs"
                        if sport == "FOCI":
                            if avg_h > avg_a + 1.2: favorit_side = "HAZAI"
                            elif avg_a > avg_h + 1.2: favorit_side = "VENDÉG"

                        excel_rows.append({
                            "SPORT": sport,
                            "IDŐPONT (HU)": get_local_time(start_iso),
                            "BAJNOKSÁG": m['league']['name'].upper(),
                            "HAZAI": home['name'],
                            "VENDÉG": away['name'],
                            "ÁTLAG": round(combined_avg, 2),
                            "FAVORIT": favorit_side
                        })
                        
                        if sport == "FOCI" and (combined_avg >= 2.8 or favorit_side != "Nincs"):
                            daily_football_targets[m['fixture']['id']] = favorit_side
                except: continue
        except: continue

    if excel_rows:
        file_name = f"lista_{today}.xlsx"
        pd.DataFrame(excel_rows).sort_values(by=["SPORT", "IDŐPONT (HU)"]).to_excel(file_name, index=False)
        send_telegram(f"✅ <b>Napi lista elkészült!</b>", file_name)
        if os.path.exists(file_name): os.remove(file_name)
        
        # Mentés, hogy ma már ne fusson le többször
        with open(SCAN_LOG_FILE, "w") as f:
            f.write(today)

def get_match_stats(match_id):
    try:
        r = requests.get(f"{SPORTS_CONFIG['FOCI']['url']}/fixtures/statistics?fixture={match_id}", headers=HEADERS, timeout=10)
        data = r.json().get("response", [])
        shots = 0
        if data:
            for team in data:
                for s in team.get('statistics', []):
                    if s['type'] in ["Total Shots", "Shots on Goal"]:
                        shots += int(s['value']) if s['value'] else 0
        return shots
    except: return 0

def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE)
    get_daily_fixtures()
    while True:
        now = datetime.now(tz)
        if now.hour == 4 and now.minute == 1:
            get_daily_fixtures(force=True); sent_ids.clear(); time.sleep(60)
        
        if 5 <= now.hour <= 23:
            try:
                r = requests.get(f"{SPORTS_CONFIG['FOCI']['url']}/fixtures?live=all", headers=HEADERS, timeout=15)
                for fx in r.json().get("response", []):
                    mid = fx["fixture"]["id"]
                    if mid in daily_football_targets and mid not in sent_ids:
                        minute = fx["fixture"]["status"]["elapsed"] or 0
                        h_g, a_g = (fx["goals"]["home"] or 0), (fx["goals"]["away"] or 0)
                        fav = daily_football_targets[mid]
                        
                        if (fav == "HAZAI" and a_g > h_g) or (fav == "VENDÉG" and h_g > a_g):
                            if 25 < minute < 75:
                                tipp = "1X" if fav == "HAZAI" else "X2"
                                send_telegram(f"⭐ <b>FAVORIT HÁTRÁNYBAN!</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\nÁllás: {h_g}-{a_g} ({minute}. perc)\n🎯 Tipp: {tipp}"); sent_ids.add(mid); continue

                        if 25 < minute < 65 and (h_g + a_g) < 2:
                            if get_match_stats(mid) >= 3:
                                send_telegram(f"⚽ <b>ÉLŐ TIPP: Over 1.5 gól</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\nÁllás: {h_g}-{a_g} ({minute}. perc)"); sent_ids.add(mid)
            except: pass
        time.sleep(60)

if __name__ == "__main__":
    keep_alive(); main_loop()
