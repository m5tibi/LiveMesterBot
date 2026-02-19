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
def home(): return "LiveMesterBot MULTISPORT PRO: Online"

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
    "FOCI": "https://v3.football.api-sports.io",
    "KOSÁR": "https://v1.basketball.api-sports.io",
    "HOKI": "https://v1.hockey.api-sports.io"
}

HEADERS = {"x-apisports-key": API_KEY}
TG_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# GLOBÁLIS TÁROLÓK
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

def get_league_standings(league_id, season):
    try:
        r = requests.get(f"{SPORTS_CONFIG['FOCI']}/standings?league={league_id}&season={season}", headers=HEADERS, timeout=10)
        data = r.json().get("response", [])
        if not data: return {}
        standings = {}
        for rank in data[0]['league']['standings'][0]:
            standings[rank['team']['id']] = rank['rank']
        return standings
    except: return {}

def get_team_avg(sport, url, team_id):
    try:
        endpoint = "/fixtures" if sport == "FOCI" else "/games"
        r = requests.get(f"{url}{endpoint}?team={team_id}&last=10", headers=HEADERS, timeout=12)
        games = r.json().get("response", [])
        if not games: return 0
        total = 0
        for g in games:
            if sport == "FOCI":
                goals = g.get('goals', {})
                total += (goals.get('home') or 0) + (goals.get('away') or 0)
            else:
                scores = g.get('scores', {})
                total += (scores.get('home', {}).get('total') or 0) + (scores.get('away', {}).get('total') or 0)
        return total / len(games)
    except: return 0

def get_daily_fixtures():
    global daily_football_targets
    tz = pytz.timezone(TIMEZONE)
    today_str = datetime.now(tz).strftime('%Y-%m-%d')
    new_football_targets = {}
    excel_rows = []
    
    send_telegram(f"🔍 <b>Multisport Szkenner indul...</b> ({today_str})")

    for sport, url in SPORTS_CONFIG.items():
        try:
            print(f"Szkennelés: {sport}", flush=True)
            endpoint = "/fixtures" if sport == "FOCI" else "/games"
            r = requests.get(f"{url}{endpoint}?date={today_str}", headers=HEADERS, timeout=20)
            matches = r.json().get("response", [])
            
            # Sebességkorlát: hokinál és kosárnál csak az első 60 meccset nézzük a fagyás elkerülésére
            process_limit = 60 if sport != "FOCI" else 999
            
            for m in matches[:process_limit]:
                try:
                    league_id = m['league']['id']
                    league_name = m['league']['name'].upper()
                    season = m['league'].get('season')
                    home = m['teams']['home']
                    away = m['teams']['away']
                    start_iso = m['fixture']['date'] if sport == "FOCI" else m['date']
                    
                    avg_h = get_team_avg(sport, url, home['id'])
                    avg_a = get_team_avg(sport, url, away['id'])
                    combined_avg = (avg_h + avg_a) / 2
                    
                    favorit_side = "Nincs"
                    if sport == "FOCI" and season:
                        standings = get_league_standings(league_id, season)
                        if standings:
                            h_rank = standings.get(home['id'], 99)
                            a_rank = standings.get(away['id'], 99)
                            if h_rank <= 5 and a_rank >= 12: favorit_side = "HAZAI"
                            elif a_rank <= 5 and h_rank >= 12: favorit_side = "VENDÉG"

                    if combined_avg > 0:
                        excel_rows.append({
                            "SPORT": sport,
                            "IDŐPONT (HU)": get_local_time(start_iso),
                            "BAJNOKSÁG": league_name,
                            "HAZAI": home['name'],
                            "VENDÉG": away['name'],
                            "ÁTLAG": round(combined_avg, 2),
                            "FAVORIT": favorit_side
                        })
                        
                        if sport == "FOCI" and (combined_avg >= 2.8 or favorit_side != "Nincs"):
                            new_football_targets[m['fixture']['id']] = {
                                "avg": combined_avg,
                                "favorit": favorit_side
                            }
                except: continue
        except Exception as e:
            print(f"Hiba a {sport} feldolgozásánál: {e}", flush=True)

    daily_football_targets = new_football_targets
    
    if excel_rows:
        file_name = f"sport_lista_{today_str}.xlsx"
        df = pd.DataFrame(excel_rows).sort_values(by=["SPORT", "IDŐPONT (HU)"])
        df.to_excel(file_name, index=False)
        send_telegram(f"✅ <b>Napi lista kész!</b>\n⚽ Foci célpontok: {len(new_football_targets)}\n📊 Összesen: {len(excel_rows)} meccs", file_name)
        if os.path.exists(file_name): os.remove(file_name)

def get_match_stats(match_id):
    try:
        r = requests.get(f"{SPORTS_CONFIG['FOCI']}/fixtures/statistics?fixture={match_id}", headers=HEADERS, timeout=12)
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
            get_daily_fixtures(); sent_ids.clear(); time.sleep(60)
        
        if 5 < now.hour < 24:
            try:
                r = requests.get(f"{SPORTS_CONFIG['FOCI']}/fixtures?live=all", headers=HEADERS, timeout=15)
                for fx in r.json().get("response", []):
                    mid = fx["fixture"]["id"]
                    if mid in daily_football_targets and mid not in sent_ids:
                        minute = fx["fixture"]["status"]["elapsed"] or 0
                        h_g = fx["goals"]["home"] or 0
                        a_g = fx["goals"]["away"] or 0
                        total_g = h_g + a_g
                        
                        # Stratégia 1: Favorit hátrányban
                        is_fav_home = daily_football_targets[mid]['favorit'] == "HAZAI"
                        is_fav_away = daily_football_targets[mid]['favorit'] == "VENDÉG"
                        
                        if (is_fav_home and a_g > h_g) or (is_fav_away and h_g > a_g):
                            if 25 < minute < 75:
                                msg = f"⭐ <b>FAVORIT HÁTRÁNYBAN!</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\nÁllás: {h_g}-{a_g} ({minute}. perc)"
                                send_telegram(msg); sent_ids.add(mid)
                                continue

                        # Stratégia 2: Gól várható (Over 1.5)
                        if 25 < minute < 65 and total_g < 2:
                            if get_match_stats(mid) >= 3:
                                msg = f"⚽ <b>FOCI ÉLŐ TIPP</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\nPerc: {minute}' | Állás: {total_g}\nTipp: Over 1.5 gól"
                                send_telegram(msg); sent_ids.add(mid)
            except: pass
        time.sleep(60)

if __name__ == "__main__":
    keep_alive(); main_loop()
