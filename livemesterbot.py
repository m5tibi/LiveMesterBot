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
def home(): return "LiveMesterBot PRO: Multisport + Esti Eredményjelentő"

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
    "FOCI": {"url": "https://v3.football.api-sports.io", "min_avg": 2.5, "limit": 200},
    "KOSÁR": {"url": "https://v1.basketball.api-sports.io", "min_avg": 160, "limit": 30},
    "HOKI": {"url": "https://v1.hockey.api-sports.io", "min_avg": 5.0, "limit": 30}
}

HEADERS = {"x-apisports-key": API_KEY}
SCAN_LOG_FILE = "last_scan.txt"
DAILY_DATA_FILE = "daily_matches.json"

daily_matches_cache = [] # Ebben tároljuk a napi meccseket az eredményekhez
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
    try:
        endpoint = "/fixtures" if sport == "FOCI" else "/games"
        r = requests.get(f"{url}{endpoint}?team={team_id}&last=8", headers=HEADERS, timeout=8)
        res = r.json().get("response", [])
        if not res: return 0
        total = sum((g.get('goals', {}).get('home') or 0) + (g.get('goals', {}).get('away') or 0) if sport == "FOCI" else (g.get('scores', {}).get('home', {}).get('total') or 0) + (g.get('scores', {}).get('away', {}).get('total') or 0) for g in res)
        return total / len(res)
    except: return 0

def get_daily_fixtures(force=False):
    global daily_football_targets, daily_matches_cache
    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).strftime('%Y-%m-%d')
    
    if not force and os.path.exists(SCAN_LOG_FILE):
        with open(SCAN_LOG_FILE, "r") as f:
            if f.read().strip() == today: return

    with open(SCAN_LOG_FILE, "w") as f: f.write(today)
    daily_matches_cache = []
    send_telegram(f"🔍 <b>Multisport Szkenner indul...</b>")

    for sport, config in SPORTS_CONFIG.items():
        try:
            url = config["url"]
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

                        match_data = {
                            "SPORT": sport,
                            "ID": mid,
                            "IDŐPONT (HU)": get_local_time(start_iso),
                            "BAJNOKSÁG": m['league']['name'].upper(),
                            "HAZAI": home['name'],
                            "VENDÉG": away['name'],
                            "ÁTLAG": round(combined_avg, 2),
                            "FAVORIT": favorit_side
                        }
                        daily_matches_cache.append(match_data)
                        if sport == "FOCI" and (combined_avg >= 2.8 or favorit_side != "Nincs"):
                            daily_football_targets[mid] = favorit_side
                except: continue
        except: continue

    if daily_matches_cache:
        file_name = f"reggeli_lista_{today}.xlsx"
        pd.DataFrame(daily_matches_cache).to_excel(file_name, index=False)
        send_telegram(f"✅ <b>Reggeli lista kész!</b>", file_name)
        if os.path.exists(file_name): os.remove(file_name)

def get_final_results():
    """Éjfélkor lekéri a végeredményeket a cache-ben lévő meccsekhez."""
    global daily_matches_cache
    if not daily_matches_cache: return

    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).strftime('%Y-%m-%d')
    send_telegram("📊 <b>Esti összegző generálása, eredmények lekérése...</b>")
    
    results_rows = []
    for m in daily_matches_cache:
        try:
            sport = m["SPORT"]
            url = SPORTS_CONFIG[sport]["url"]
            endpoint = f"/fixtures?id={m['ID']}" if sport == "FOCI" else f"/games?id={m['ID']}"
            r = requests.get(url + endpoint, headers=HEADERS, timeout=10).json().get("response", [])
            
            final_score = "N/A"
            status = "⌛"
            
            if r:
                game = r[0]
                if sport == "FOCI":
                    h, a = game['goals']['home'], game['goals']['away']
                    if h is not None and a is not None:
                        final_score = f"{h}-{a}"
                        # Egyszerű ellenőrzés: ha gólátlag felett volt (Over 1.5-öt nézünk alapnak)
                        status = "✅" if (h+a) >= 2 else "❌"
                else:
                    h, a = game['scores']['home']['total'], game['scores']['away']['total']
                    if h is not None and a is not None:
                        final_score = f"{h}-{a}"
                        status = "✅" if (h+a) >= m['ÁTLAG'] else "❌"

            m["EREDMÉNY"] = final_score
            m["STATUSZ"] = status
            results_rows.append(m)
            time.sleep(1) # API kímélése
        except: continue

    if results_rows:
        file_name = f"esti_eredmenyek_{today}.xlsx"
        pd.DataFrame(results_rows).to_excel(file_name, index=False)
        send_telegram(f"📊 <b>Napi összegző táblázat elkészült!</b>", file_name)
        if os.path.exists(file_name): os.remove(file_name)

def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE)
    get_daily_fixtures()
    while True:
        now = datetime.now(tz)
        
        # 23:50-kor eredményellenőrzés
        if now.hour == 23 and now.minute == 50:
            get_final_results()
            time.sleep(65)

        if now.hour == 4 and now.minute == 0:
            if os.path.exists(SCAN_LOG_FILE): os.remove(SCAN_LOG_FILE)
            sent_ids.clear()
            time.sleep(65)

        if now.hour == 4 and now.minute == 5:
            get_daily_fixtures()
            time.sleep(60)
        
        # Élő figyelés (csak focira)
        if 5 <= now.hour <= 23:
            try:
                r = requests.get(f"{SPORTS_CONFIG['FOCI']['url']}/fixtures?live=all", headers=HEADERS, timeout=15)
                for fx in r.json().get("response", []):
                    mid = fx["fixture"]["id"]
                    if mid in daily_football_targets and mid not in sent_ids:
                        # ... (Élő szűrő logika változatlan) ...
                        minute = fx["fixture"]["status"]["elapsed"] or 0
                        h_g, a_g = (fx["goals"]["home"] or 0), (fx["goals"]["away"] or 0)
                        fav = daily_football_targets[mid]
                        if (fav == "HAZAI" and a_g > h_g) or (fav == "VENDÉG" and h_g > a_g):
                            if 25 < minute < 75:
                                send_telegram(f"⭐ <b>FAVORIT HÁTRÁNYBAN!</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\n{h_g}-{a_g} ({minute}. perc)\n🎯 Tipp: {'1X' if fav == 'HAZAI' else 'X2'}"); sent_ids.add(mid)
            except: pass
        time.sleep(60)

if __name__ == "__main__":
    keep_alive(); main_loop()
