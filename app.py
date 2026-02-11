import requests
import time
import os
from datetime import datetime
from flask import Flask
from threading import Thread

# ========= LÁTHATATLAN WEBSZERVER A RENDERNEK =========
app = Flask('')

@app.route('/')
def home():
    return "LiveMesterBot is active and running!"

def run_web_server():
    # A Render automatikusan kioszt egy portot a környezeti változókban
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ========= BOT KONFIGURÁCIÓ =========
API_KEY = "IDE_API_KULCS"
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

TELEGRAM_TOKEN = "IDE_TELEGRAM_TOKEN"
CHAT_ID = "IDE_CHAT_ID"
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
    current_score_text = f"{home_score}-{away_score}"

    banned = ["friendly", "u21", "u23", "reserve", "youth", "development", "women"]
    if any(bad in league for bad in banned) or total_goals >= 2:
        return False, None, 0, ""

    stats = get_match_stats(match_id)
    if not stats or stats["shots"] < 3:
        return False, None, 0, ""

    if 20 <= minute <= 70:
        confidence = 65 + (minute // 5)
        if total_goals == 1: confidence += 10
        return True, "Over 1.5 gól", min(confidence, 92), current_score_text

    if 44 <= minute <= 55 and total_goals <= 1:
        return True, "2. félidőben több mint 0.5 gól", 78, current_score_text

    return False, None, 0, ""

# ========= FŐ CIKLUS =========
def main_bot_loop():
    sent_ids = set()
    print("Bot motor elindult...")
    
    while True:
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
        
        time.sleep(30)

if __name__ == "__main__":
    # 1. Indítjuk a web-szervert a Rendernek
    keep_alive()
    # 2. Indítjuk a botot
    main_bot_loop()
