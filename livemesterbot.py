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
    # A Render automatikusan adja meg a PORT-ot
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ========= KONFIGURÁCIÓ (Környezeti változókból) =========
API_KEY = os.environ.get("FOOTBALL_API_KEY", "IDE_IRD_AZ_API_KULCSOT")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "IDE_IRD_A_TG_TOKENT")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "IDE_IRD_A_CHAT_ID-T")
TIMEZONE = "Europe/Budapest"

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
TG_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

def send_telegram(message: str):
    try:
        requests.post(TG_URL, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"Telegram hiba: {e}")

def get_live_fixtures():
    try:
        r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
        return r.json().get("response", [])
    except Exception as e:
        print(f"API hiba (fixtures): {e}")
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

    # Alapszűrés (barátságos, utánpótlás, női)
    banned = ["friendly", "u21", "u23", "reserve", "youth", "development", "women"]
    if any(bad in league for bad in banned) or total_goals >= 2:
        return False, None, 0, ""

    # Statisztikai szűrés (min. 3 lövés)
    stats = get_match_stats(match_id)
    if not stats or stats["shots"] < 3:
        return False, None, 0, ""

    # Over 1.5 logika (20-70. perc)
    if 20 <= minute <= 70:
        confidence = 65 + (minute // 5)
        if total_goals == 1: confidence += 10
        return True, "Over 1.5 gól", min(confidence, 92), current_score

    # Félidős gól (44-55. perc)
    if 44 <= minute <= 55 and total_goals <= 1:
        return True, "2. félidőben több mint 0.5 gól", 78, current_score

    return False, None, 0, ""

def main_loop():
    sent_ids = set()
    tz = pytz.timezone(TIMEZONE)
    print(f"[{datetime.now(tz)}] Bot motor indul...")
    
    while True:
        now = datetime.now(tz)
        current_hour = now.hour

        # Éjszakai szünet: 00:00 és 04:00 (reggel 4) között pihen a bot
        if 0 <= current_hour < 4:
            if now.minute % 15 == 0 and now.second < 30:
                print(f"[{now.strftime('%H:%M:%S')}] Éjszakai szünet (00-04)...")
            time.sleep(30)
            continue

        fixtures = get_live_fixtures()
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
                print(f"Tipp elküldve: {fx['teams']['home']['name']}")
        
        time.sleep(30) # 30 másodperces frissítési ciklus

if __name__ == "__main__":
    keep_alive()
    main_loop()
