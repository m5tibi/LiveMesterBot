import requests
import time
import os
from datetime import datetime
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

# ========= RENDER ÉBREN TARTÓ =========
app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot MULTISPORT: Foci, Kosár, Hoki üzemmód!"

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

# Sportágankénti végpontok
SPORTS_CONFIG = {
    "FOCI": "https://v3.football.api-sports.io",
    "KOSÁR": "https://v1.basketball.api-sports.io",
    "HOKI": "https://v1.hockey.api-sports.io"
}

HEADERS = {"x-apisports-key": API_KEY}
TG_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# GLOBÁLIS TÁROLÓK
daily_football_targets = {}      
sent_tips_history = [] 

def send_telegram(message: str, file_path=None):
    try:
        if file_path:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
            with open(file_path, 'rb') as f:
                requests.post(url, data={"chat_id": CHAT_ID, "caption": message, "parse_mode": "HTML"}, files={"document": f}, timeout=20)
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except: pass

def get_local_time(iso_date):
    """Konvertálja az API időpontokat magyar időre."""
    try:
        clean_date = iso_date.replace('Z', '').split('+')[0]
        utc_dt = datetime.fromisoformat(clean_date).replace(tzinfo=pytz.utc)
        local_tz = pytz.timezone(TIMEZONE)
        return utc_dt.astimezone(local_tz).strftime('%H:%M')
    except: return "??:??"

def get_team_avg(sport, url, team_id):
    """Lekéri a csapat utolsó 10 meccsének átlagát."""
    try:
        endpoint = "/fixtures" if sport == "FOCI" else "/games"
        r = requests.get(f"{url}{endpoint}?team={team_id}&last=10", headers=HEADERS, timeout=10)
        games = r.json().get("response", [])
        if not games: return 0
        
        total = 0
        for g in games:
            if sport == "FOCI":
                total += (g.get('goals', {}).get('home') or 0) + (g.get('goals', {}).get('away') or 0)
            else:
                total += (g.get('scores', {}).get('home', {}).get('total') or 0) + (g.get('scores', {}).get('away', {}).get('total') or 0)
        return total / len(games)
    except: return 0

def get_daily_fixtures():
    """Összetett szkenner minden sportágra Excel generálással."""
    global daily_football_targets
    tz = pytz.timezone(TIMEZONE)
    today_str = datetime.now(tz).strftime('%Y-%m-%d')
    new_football_targets = {}
    excel_rows = []
    
    send_telegram(f"🔍 <b>Multisport Szkenner indul...</b> ({today_str})")

    for sport, url in SPORTS_CONFIG.items():
        try:
            endpoint = "/fixtures" if sport == "FOCI" else "/games"
            r = requests.get(f"{url}{endpoint}?date={today_str}", headers=HEADERS, timeout=15)
            matches = r.json().get("response", [])
            
            for m in matches:
                league_name = m['league']['name'].upper()
                home_team = m['teams']['home']
                away_team = m['teams']['away']
                start_iso = m['fixture']['date'] if sport == "FOCI" else m['date']
                
                avg_h = get_team_avg(sport, url, home_team['id'])
                avg_a = get_team_avg(sport, url, away_team['id'])
                combined_avg = (avg_h + avg_a) / 2 if (avg_h and avg_a) else (avg_h or avg_a)
                
                if combined_avg > 0:
                    excel_rows.append({
                        "SPORT": sport,
                        "IDŐPONT (HU)": get_local_time(start_iso),
                        "BAJNOKSÁG": league_name,
                        "HAZAI": home_team['name'],
                        "VENDÉG": away_team['name'],
                        "ÁTLAG (GÓL/PONT)": round(combined_avg, 2)
                    })
                    
                    if sport == "FOCI" and combined_avg >= 2.8:
                        new_football_targets[m['fixture']['id']] = {"avg": combined_avg}
        except: continue

    daily_football_targets = new_football_targets
    
    if excel_rows:
        file_name = f"sport_lista_{today_str}.xlsx"
        df = pd.DataFrame(excel_rows).sort_values(by=["SPORT", "IDŐPONT (HU)"])
        df.to_excel(file_name, index=False)
        send_telegram(f"✅ <b>Napi lista kész!</b>\n⚽ Foci célpontok: {len(new_football_targets)}\n🏀/🏒 Egyéb meccsek a fájlban.", file_name)
        if os.path.exists(file_name): os.remove(file_name)

def get_match_stats(match_id):
    """Élő statisztika lekérése focira."""
    try:
        r = requests.get(f"{SPORTS_CONFIG['FOCI']}/fixtures/statistics?fixture={match_id}", headers=HEADERS, timeout=10)
        data = r.json().get("response", [])
        shots = 0
        if data:
            for team in data:
                for s in team['statistics']:
                    if s['type'] in ["Total Shots", "Shots on Goal"]:
                        shots += int(s['value']) if s['value'] else 0
        return shots
    except: return 0

def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE); get_daily_fixtures()
    while True:
        now = datetime.now(tz)
        
        # Hajnali 4:01-es frissítés
        if now.hour == 4 and now.minute == 1:
            get_daily_fixtures(); sent_ids.clear(); time.sleep(60)
        
        # Élő figyelés csak focira (nap közben)
        if 4 < now.hour < 24:
            try:
                r = requests.get(f"{SPORTS_CONFIG['FOCI']}/fixtures?live=all", headers=HEADERS, timeout=10)
                for fx in r.json().get("response", []):
                    mid = fx["fixture"]["id"]
                    if mid in daily_football_targets and mid not in sent_ids:
                        minute = fx["fixture"]["status"]["elapsed"] or 0
                        total_g = (fx["goals"]["home"] or 0) + (fx["goals"]["away"] or 0)
                        
                        # Stratégia: 25-65 perc között, kevés gólnál, jó aktivitással
                        if 25 < minute < 65 and total_g < 2:
                            if get_match_stats(mid) >= 3:
                                msg = f"⚽ <b>FOCI ÉLŐ TIPP</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\nPerc: {minute}' | Állás: {total_g}\nTipp: Over 1.5 gól"
                                send_telegram(msg); sent_ids.add(mid)
            except: pass
        time.sleep(60)

if __name__ == "__main__":
    keep_alive(); main_loop()
