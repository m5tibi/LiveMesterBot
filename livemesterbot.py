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
    return "A LiveMesterBot fut! (00:00 - 04:00 között pihenő módban)"

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
TG_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

def send_telegram(message: str):
    try:
        requests.post(TG_URL, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"Telegram hiba: {e}", flush=True)

def get_live_fixtures():
    try:
        r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
        return r.json().get("response", [])
    except Exception as e:
        print(f"API hiba (fixtures): {e}", flush=True)
        return []

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
    minute = fx["fixture"]["status"]["elapsed"] or 0
    league = fx["league"]["name"].lower()
    match_id = fx["fixture"]["id"]
    
    home_score = fx["goals"]["home"] if fx["goals"]["home"] is not None else 0
    away_score = fx["goals"]["away"] if fx["goals"]["away"] is not None else 0
    total_goals = home_score + away_score
    current_score = f"{home_score}-{away_score}"

    banned = ["friendly", "u21", "u23", "reserve", "youth", "development", "women"]
    if any(bad in league for bad in banned) or total_goals >= 2:
        return False, None, 0, ""

    stats = get_match_stats(match_id)
    # Ha nincs statisztika, vagy kevés a lövés
    if not stats or stats["shots"] < 3:
        return False, None, 0, ""

    if 20 <= minute <= 70:
        confidence = 65 + (minute // 5)
        if total_goals == 1: confidence += 10
        return True, "Over 1.5 gól", min(confidence, 92), current_score

    if 44 <= minute <= 55 and total_goals <= 1:
        return True, "2. félidőben több mint 0.5 gól", 78, current_score

    return False, None, 0, ""

def main_loop():
    sent_ids = set()
    tz = pytz.timezone(TIMEZONE)
    start_msg = f"🚀 <b>LiveMesterBot elindult!</b>\n⏰ Időpont: {datetime.now(tz).strftime('%H:%M:%S')}"
    print(f"[{datetime.now(tz)}] Bot motor elindult...", flush=True)
    
    # Indulási üzenet küldése Telegramra
    send_telegram(start_msg)
    
    try:
        while True:
            now = datetime.now(tz)
            current_hour = now.hour

            if 0 <= current_hour < 4:
                if now.minute % 15 == 0 and now.second < 30:
                    print(f"[{now.strftime('%H:%M:%S')}] Éjszakai szünet (00-04)...", flush=True)
                time.sleep(30)
                continue

            fixtures = get_live_fixtures()
            print(f"[{now.strftime('%H:%M:%S')}] Ellenőrzés: {len(fixtures)} élő meccs lekérve az API-ból.", flush=True)

            for fx in fixtures:
                match_id = fx["fixture"]["id"]
                if match_id in sent_ids:
                    continue

                send, tip_text, confidence, score = should_send_tip(fx)
                if send:
                    msg = (
                        f"⚽ <b>ÉLŐ FOGADÁSI TIPP</b>\n\n"
                        f"<b>Mérkőzés:</b> {fx['teams']['home']['name']} – {fx['teams']['away']['name']}\n"
                        f"<b>Állás:</b> {score}\n"
                        f"<b>Liga:</b> {fx['league']['name']}\n"
                        f"<b>Játékidő:</b> {fx['fixture']['status']['elapsed']}. perc\n\n"
                        f"<b>Ajánlott tipp:</b> {tip_text}\n"
                        f"<b>Biztonság:</b> {confidence}%"
                    )
                    send_telegram(msg)
                    sent_ids.add(match_id)
                    print(f"[{now.strftime('%H:%M:%S')}] TIPP ELKÜLDVE: {fx['teams']['home']['name']}", flush=True)
            
            time.sleep(30)
            
    except Exception as e:
        # Hiba esetén értesítés
        error_msg = f"⚠️ <b>LiveMesterBot hiba miatt leállt!</b>\n❌ Hiba: {str(e)}"
        send_telegram(error_msg)
        raise e
    finally:
        # Normál leállás (pl. kézi leállítás) esetén
        stop_msg = f"🛑 <b>LiveMesterBot leállt.</b>\n⏰ Időpont: {datetime.now(tz).strftime('%H:%M:%S')}"
        send_telegram(stop_msg)

if __name__ == "__main__":
    keep_alive()
    main_loop()
