import requests
import time
import os
from datetime import datetime
import pytz
from flask import Flask
from threading import Thread

# ========= RENDER ÉBREN TARTÓ SZERVER =========
app = Flask('')

@app.route('/')
def home():
    return "A LiveMesterBot Statisztikai Előszűrővel fut!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ========= KONFIGURÁCIÓ =========
API_KEY = os.environ.get("FOOTBALL_API_KEY", "IDE_API_KULCS")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "IDE_TG_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "IDE_CHAT_ID")
TIMEZONE = "Europe/Budapest"

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# Napi figyelt meccsek listája (ID-k)
daily_targets = []

def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except:
        pass

def get_team_avg_goals(team_id):
    """Lekéri a csapat utolsó 10 meccsének gólátlagát."""
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=10)
        fixtures = r.json().get("response", [])
        if not fixtures: return 0
        total_goals = sum((f['goals']['home'] or 0) + (f['goals']['away'] or 0) for f in fixtures)
        return total_goals / len(fixtures)
    except:
        return 0

def get_daily_fixtures():
    """Hajnali szkenner: csak a gólgazdag (avg > 2.5) meccseket menti el."""
    global daily_targets
    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).strftime('%Y-%m-%d')
    new_targets = []
    
    print(f"[{today}] Hajnali szkenner indul...", flush=True)
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={today}", headers=HEADERS, timeout=15)
        all_matches = r.json().get("response", [])
        
        for m in all_matches:
            # Alapszűrés: ne legyen barátságos vagy női
            league = m['league']['name'].lower()
            if any(bad in league for bad in ["friendly", "women", "u19", "u21"]): continue
            
            home_id = m['teams']['home']['id']
            away_id = m['teams']['away']['id']
            
            # Statisztikai szűrés (ez sok API hívás, de belefér a 75k-ba)
            avg_home = get_team_avg_goals(home_id)
            avg_away = get_team_avg_goals(away_id)
            combined_avg = (avg_home + avg_away) / 2
            
            if combined_avg >= 2.5:
                new_targets.append(m['fixture']['id'])
                print(f"DEBUG: {m['teams']['home']['name']} meccse felvéve (Avg: {combined_avg:.2f})", flush=True)
        
        daily_targets = new_targets
        send_telegram(f"🔍 <b>Hajnali szkenner kész!</b>\n🎯 {len(all_matches)} meccsből {len(daily_targets)} statisztikailag erős célpont kiválasztva.")
    except Exception as e:
        print(f"Szkenner hiba: {e}", flush=True)

def get_match_stats(match_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={match_id}", headers=HEADERS, timeout=10)
        stats_data = r.json().get("response", [])
        combined_stats = {"shots": 0}
        for team_stat in stats_data:
            for stat in team_stat.get("statistics", []):
                if stat["type"] == "Total Shots":
                    val = stat["value"]
                    combined_stats["shots"] += int(val) if val else 0
        return combined_stats
    except:
        return None

def should_send_tip(fx):
    match_id = fx["fixture"]["id"]
    # CSAK a reggel kigyűjtött meccseket figyeljük!
    if match_id not in daily_targets:
        return False, None, 0, ""

    minute = fx["fixture"]["status"]["elapsed"] or 0
    home_score = fx["goals"]["home"] or 0
    away_score = fx["goals"]["away"] or 0
    total_goals = home_score + away_score

    # Ha már van 2 gól, vagy nem a 20-70 perc között vagyunk, nem érdekes
    if total_goals >= 2 or minute < 20 or minute > 70:
        return False, None, 0, ""

    stats = get_match_stats(match_id)
    shots = stats["shots"] if stats else 0
    
    # Intelligens statisztika: ha van adat, kell 2 lövés, ha nincs adat (0), engedjük a statisztikai múlt miatt
    if stats and 0 < shots < 2:
        return False, None, 0, ""

    confidence = 75 + (minute // 5)
    return True, "Over 1.5 gól (Prémium Szűrt)", min(confidence, 96), f"{home_score}-{away_score}"

def main_loop():
    sent_ids = set()
    tz = pytz.timezone(TIMEZONE)
    
    # Első indításkor is fusson le
    get_daily_fixtures()
    
    while True:
        now = datetime.now(tz)
        
        # Minden hajnali 04:01-kor frissítés
        if now.hour == 4 and now.minute == 1 and now.second < 35:
            get_daily_fixtures()
            sent_ids.clear()
            time.sleep(40)

        if 0 <= now.hour < 4:
            time.sleep(60)
            continue

        fixtures = get_live_fixtures_api() # API-Football 'live=all' hívás
        for fx in fixtures:
            match_id = fx["fixture"]["id"]
            if match_id in sent_ids: continue

            send, tip_text, confidence, score = should_send_tip(fx)
            if send:
                # Telegram üzenet küldése...
                sent_ids.add(match_id)
        
        time.sleep(45) # Ritkább frissítés elég, mert szűrt a lista

def get_live_fixtures_api():
    try:
        r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
        return r.json().get("response", [])
    except:
        return []

if __name__ == "__main__":
    keep_alive()
    main_loop()
