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
    return "A LiveMesterBot Prémium Szűrővel fut! (Lövésszám szűrés kikapcsolva)"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ========= KONFIGURÁCIÓ (Környezeti változókból) =========
API_KEY = os.environ.get("FOOTBALL_API_KEY", "IDE_API_KULCS")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "IDE_TG_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "IDE_CHAT_ID")
TIMEZONE = "Europe/Budapest"

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
TG_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# Napi figyelt meccsek listája (ID -> Várható gólátlag)
daily_targets = {}

def send_telegram(message: str):
    try:
        requests.post(TG_URL, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"Telegram hiba: {e}", flush=True)

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
    """Hajnali szkenner: kigyűjti a nap legjobb meccseit."""
    global daily_targets
    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).strftime('%Y-%m-%d')
    new_targets = {}
    
    print(f"[{today}] Hajnali szkenner indul...", flush=True)
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={today}", headers=HEADERS, timeout=15)
        all_matches = r.json().get("response", [])
        
        for m in all_matches:
            league = m['league']['name'].lower()
            # Alapszűrések (maradnak a korábbiak)
            if any(bad in league for bad in ["friendly", "women", "u19", "u21"]): continue
            
            home_id = m['teams']['home']['id']
            away_id = m['teams']['away']['id']
            
            avg_home = get_team_avg_goals(home_id)
            avg_away = get_team_avg_goals(away_id)
            combined_avg = (avg_home + avg_away) / 2
            
            if combined_avg >= 2.5:
                # Elmentjük az ID-t és az átlagot is
                new_targets[m['fixture']['id']] = combined_avg
                print(f"DEBUG: {m['teams']['home']['name']} felvéve (Avg: {combined_avg:.2f})", flush=True)
        
        daily_targets = new_targets
        send_telegram(f"🔍 <b>Hajnali szkenner kész!</b>\n🎯 {len(daily_targets)} prémium célpont kiválasztva a mai napra.")
    except Exception as e:
        print(f"Szkenner hiba: {e}", flush=True)

def should_send_tip(fx):
    match_id = fx["fixture"]["id"]
    
    # CSAK a reggel kigyűjtött meccseket figyeljük!
    if match_id not in daily_targets:
        return False, None, 0, ""

    minute = fx["fixture"]["status"]["elapsed"] or 0
    home_score = fx["goals"]["home"] if fx["goals"]["home"] is not None else 0
    away_score = fx["goals"]["away"] if fx["goals"]["away"] is not None else 0
    total_goals = home_score + away_score
    current_score = f"{home_score}-{away_score}"

    # Állás és idő szűrés (0-0, 1-0, 0-1 állásnál a 20-70. perc között)
    if total_goals >= 2 or minute < 20 or minute > 70:
        return False, None, 0, ""

    # LAZA MÓD: Nem kérünk le külön statisztikát, bízunk a hajnali szűrőben!
    expected_avg = daily_targets[match_id]
    confidence = 75 + (minute // 5)
    
    return True, f"Over 1.5 gól (Prémium: {expected_avg:.2f} gól/meccs)", min(confidence, 96), current_score

def get_live_fixtures_api():
    try:
        r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
        return r.json().get("response", [])
    except:
        return []

def main_loop():
    sent_ids = set()
    tz = pytz.timezone(TIMEZONE)
    
    # Induláskor szkenner
    get_daily_fixtures()
    print(f"[{datetime.now(tz)}] Bot motor elindult...", flush=True)
    
    try:
        while True:
            now = datetime.now(tz)
            
            # Hajnali 04:01 frissítés
            if now.hour == 4 and now.minute == 1 and now.second < 35:
                get_daily_fixtures()
                sent_ids.clear()
                time.sleep(40)

            if 0 <= now.hour < 4:
                time.sleep(60)
                continue

            fixtures = get_live_fixtures_api()
            active_count = 0
            for fx in fixtures:
                match_id = fx["fixture"]["id"]
                if match_id in daily_targets:
                    active_count += 1
                    if match_id in sent_ids: continue

                    send, tip_text, confidence, score = should_send_tip(fx)
                    if send:
                        msg = (
                            f"⚽ <b>PRÉMIUM ÉLŐ TIPP</b>\n\n"
                            f"<b>Mérkőzés:</b> {fx['teams']['home']['name']} – {fx['teams']['away']['name']}\n"
                            f"<b>Állás:</b> {score}\n"
                            f"<b>Perc:</b> {fx['fixture']['status']['elapsed']}. perc\n\n"
                            f"<b>Tipp:</b> {tip_text}\n"
                            f"<b>Biztonság:</b> {confidence}%"
                        )
                        send_telegram(msg)
                        sent_ids.add(match_id)
                        print(f"[{now.strftime('%H:%M:%S')}] TIPP ELKÜLDVE: {fx['teams']['home']['name']}", flush=True)
            
            # 10 percenkénti státusz log
            if now.minute % 10 == 0 and now.second < 35:
                print(f"[{now.strftime('%H:%M')}] Figyelt élő meccsek száma: {active_count}", flush=True)

            time.sleep(45)
            
    except Exception as e:
        send_telegram(f"⚠️ <b>Hiba:</b> {str(e)}")
        raise e
    finally:
        send_telegram(f"🛑 <b>LiveMesterBot leállt.</b>")

if __name__ == "__main__":
    keep_alive()
    main_loop()
