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
    return "A LiveMesterBot Elit módban fut napi összegzővel!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ========= KONFIGURÁCIÓ =========
API_KEY = os.environ.get("FOOTBALL_API_KEY", "IDE_AZ_API_KULCSOT")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "IDE_A_TG_TOKENT")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "IDE_A_CHAT_ID-T")
TIMEZONE = "Europe/Budapest"

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
TG_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# GLOBÁLIS TÁROLÓK
daily_targets = {}      # ID -> Várható gólátlag
sent_tips_history = []  # ID-k a napi ellenőrzéshez

def send_telegram(message: str):
    try:
        requests.post(TG_URL, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"Telegram hiba: {e}", flush=True)

def get_team_avg_goals(team_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=10)
        fixtures = r.json().get("response", [])
        if not fixtures: return 0
        total_goals = sum((f['goals']['home'] or 0) + (f['goals']['away'] or 0) for f in fixtures)
        return total_goals / len(fixtures)
    except:
        return 0

def get_daily_fixtures():
    global daily_targets
    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).strftime('%Y-%m-%d')
    new_targets = {}
    
    print(f"[{today}] SZIGORÚ Hajnali szkenner indul (Min Avg: 3.0)...", flush=True)
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={today}", headers=HEADERS, timeout=15)
        all_matches = r.json().get("response", [])
        
        for m in all_matches:
            league = m['league']['name'].lower()
            if any(bad in league for bad in ["friendly", "women", "u19", "u21", "youth", "reserve"]): continue
            
            home_id = m['teams']['home']['id']
            away_id = m['teams']['away']['id']
            
            avg_home = get_team_avg_goals(home_id)
            avg_away = get_team_avg_goals(away_id)
            combined_avg = (avg_home + avg_away) / 2
            
            if combined_avg >= 3.0:
                new_targets[m['fixture']['id']] = combined_avg
        
        daily_targets = new_targets
        send_telegram(f"🛡️ <b>Szigorú szkenner kész!</b>\n🎯 {len(daily_targets)} elit célpont kiválasztva (Avg > 3.0).")
    except Exception as e:
        print(f"Szkenner hiba: {e}", flush=True)

def get_match_result(match_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures?id={match_id}", headers=HEADERS, timeout=10)
        res = r.json().get("response", [])
        if res:
            goals = res[0].get("goals", {})
            return (goals.get("home") or 0) + (goals.get("away") or 0)
    except:
        return None
    return None

def send_daily_report():
    global sent_tips_history
    if not sent_tips_history:
        send_telegram("📊 <b>Napi összegző:</b> Ma nem volt kiküldött tipp.")
        return

    wins = 0
    total = len(sent_tips_history)
    send_telegram(f"⏳ <b>Napi összegző...</b> {total} tipp ellenőrzése.")
    
    for mid in sent_tips_history:
        final_goals = get_match_result(mid)
        if final_goals is not None and final_goals >= 2:
            wins += 1
        time.sleep(1)

    win_rate = (wins / total) * 100
    report = (
        f"📊 <b>NAPI MÉRLEG</b>\n"
        f"----------------------\n"
        f"✅ Nyertes: {wins}\n"
        f"❌ Vesztes: {total - wins}\n"
        f"📈 Összesen: {total}\n"
        f"🎯 <b>Hatékonyság: {win_rate:.1f}%</b>"
    )
    send_telegram(report)
    sent_tips_history = []

def get_match_stats(match_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={match_id}", headers=HEADERS, timeout=10)
        stats_data = r.json().get("response", [])
        combined_stats = {"shots": 0}
        for team_stat in stats_data:
            for stat in team_stat.get("statistics", []):
                if stat["type"] in ["Total Shots", "Shots on Goal"]:
                    val = stat["value"]
                    combined_stats["shots"] += int(val) if val else 0
        return combined_stats
    except:
        return None

def should_send_tip(fx):
    mid = fx["fixture"]["id"]
    if mid not in daily_targets: return False, None, 0, ""

    minute = fx["fixture"]["status"]["elapsed"] or 0
    home_score = fx["goals"]["home"] or 0
    away_score = fx["goals"]["away"] or 0
    total_goals = home_score + away_score

    if total_goals >= 2 or minute < 25 or minute > 65:
        return False, None, 0, ""

    stats = get_match_stats(mid)
    shots = stats["shots"] if stats else 0
    
    # Szigorú feltétel: ha nincs adat, most nem küldünk (biztonság)
    if not stats or shots < 3:
        return False, None, 0, ""

    avg = daily_targets[mid]
    confidence = 80 + (shots * 2)
    return True, f"Over 1.5 gól (Elit avg: {avg:.2f})", min(confidence, 98), f"{home_score}-{away_score}"

def main_loop():
    sent_ids = set()
    tz = pytz.timezone(TIMEZONE)
    get_daily_fixtures()
    
    while True:
        now = datetime.now(tz)
        
        if now.hour == 23 and now.minute == 55 and now.second < 45:
            send_daily_report()
            time.sleep(60)

        if now.hour == 4 and now.minute == 1:
            get_daily_fixtures()
            sent_ids.clear()
            time.sleep(60)

        if 0 <= now.hour < 4:
            time.sleep(60)
            continue

        try:
            r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
            fixtures = r.json().get("response", [])
            active_count = 0
            
            for fx in fixtures:
                mid = fx["fixture"]["id"]
                if mid in daily_targets:
                    active_count += 1
                    if mid in sent_ids: continue

                    send, tip, conf, score = should_send_tip(fx)
                    if send:
                        msg = (f"🔥 <b>ELIT ÉLŐ TIPP</b>\n\n"
                               f"<b>Meccs:</b> {fx['teams']['home']['name']} – {fx['teams']['away']['name']}\n"
                               f"<b>Állás:</b> {score} ({fx['fixture']['status']['elapsed']}. perc)\n"
                               f"<b>Tipp:</b> {tip}\n"
                               f"<b>Biztonság:</b> {conf}%")
                        send_telegram(msg)
                        sent_ids.add(mid)
                        sent_tips_history.append(mid)
            
            if now.minute % 15 == 0 and now.second < 45:
                print(f"[{now.strftime('%H:%M')}] Aktív elit figyelés: {active_count} meccs.", flush=True)

        except Exception as e:
            print(f"Hiba az élő lekérésnél: {e}", flush=True)

        time.sleep(45)

if __name__ == "__main__":
    keep_alive()
    main_loop()
