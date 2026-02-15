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
def home(): return "LiveMesterBot PRO: Elit + Favorit vadász üzemmód!"

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

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
TG_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# GLOBÁLIS TÁROLÓK
daily_targets = {}      # ID -> {avg, favorit_team_id, favorit_side}
sent_tips_history = [] 

def send_telegram(message: str):
    try: requests.post(TG_URL, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except: pass

def get_league_standings(league_id, season):
    try:
        r = requests.get(f"{BASE_URL}/standings?league={league_id}&season={season}", headers=HEADERS, timeout=10)
        data = r.json().get("response", [])
        if not data: return {}
        standings = {}
        for rank in data[0]['league']['standings'][0]:
            standings[rank['team']['id']] = rank['rank']
        return standings
    except: return {}

def get_team_avg_goals(team_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=10)
        fixtures = r.json().get("response", [])
        if not fixtures: return 0
        total = sum((f['goals']['home'] or 0) + (f['goals']['away'] or 0) for f in fixtures)
        return total / len(fixtures)
    except: return 0

def get_daily_fixtures():
    global daily_targets
    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).strftime('%Y-%m-%d')
    new_targets = {}
    
    print(f"[{today}] PRO Szkenner indul...", flush=True)
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={today}", headers=HEADERS, timeout=15)
        all_matches = r.json().get("response", [])
        
        for m in all_matches:
            league_id = m['league']['id']
            season = m['league']['season']
            home_id = m['teams']['home']['id']
            away_id = m['teams']['away']['id']
            
            # Gólátlag lekérése
            avg = (get_team_avg_goals(home_id) + get_team_avg_goals(away_id)) / 2
            
            # Favorit ellenőrzése (Tabella alapján)
            standings = get_league_standings(league_id, season)
            favorit_side = None
            if standings:
                h_rank = standings.get(home_id, 99)
                a_rank = standings.get(away_id, 99)
                if h_rank <= 5 and a_rank >= 12: favorit_side = "home"
                elif a_rank <= 5 and h_rank >= 12: favorit_side = "away"

            if avg >= 2.8 or favorit_side:
                new_targets[m['fixture']['id']] = {
                    "avg": avg,
                    "favorit_side": favorit_side,
                    "home_name": m['teams']['home']['name'],
                    "away_name": m['teams']['away']['name']
                }
        
        daily_targets = new_targets
        send_telegram(f"🎯 <b>PRO Szkenner kész!</b>\n🔥 {len(daily_targets)} meccs a listán.\n⭐ Ebből {len([x for x in daily_targets.values() if x['favorit_side']])} favorit meccs.")
    except Exception as e: print(f"Hiba: {e}", flush=True)

def get_match_stats(match_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={match_id}", headers=HEADERS, timeout=10)
        data = r.json().get("response", [])
        stats = {"home_shots": 0, "away_shots": 0}
        if len(data) >= 2:
            for i, side in enumerate(["home_shots", "away_shots"]):
                for s in data[i]['statistics']:
                    if s['type'] in ["Total Shots", "Shots on Goal"]:
                        val = s['value']
                        stats[side] += int(val) if val else 0
        return stats
    except: return None

def should_send_tip(fx):
    mid = fx["fixture"]["id"]
    if mid not in daily_targets: return False, None, 0, ""

    data = daily_targets[mid]
    minute = fx["fixture"]["status"]["elapsed"] or 0
    h_goals = fx["goals"]["home"] or 0
    a_goals = fx["goals"]["away"] or 0
    total = h_goals + a_goals
    
    stats = get_match_stats(mid)
    h_shots = stats["home_shots"] if stats else 0
    a_shots = stats["away_shots"] if stats else 0

    # 1. STRATÉGIA: FAVORIT HÁTRÁNYBAN (FORDÍTÁS VÁRHATÓ)
    if data['favorit_side'] == "home" and a_goals > h_goals and total == 1 and 25 < minute < 70:
        return True, "FAVORIT HÁTRÁNYBAN - Hazai vagy Döntetlen (1X)", 85, f"{h_goals}-{a_goals}"
    
    if data['favorit_side'] == "away" and h_goals > a_goals and total == 1 and 25 < minute < 70:
        return True, "FAVORIT HÁTRÁNYBAN - Vendég vagy Döntetlen (X2)", 85, f"{h_goals}-{a_goals}"

    # 2. STRATÉGIA: GÓL VÁRHATÓ (OVER 1.5) - Általános elit szűrő
    if total < 2 and 25 < minute < 65:
        if (h_shots + a_shots) >= 4:
            return True, f"GÓL VÁRHATÓ (Over 1.5) - Elit avg: {data['avg']:.2f}", 80, f"{h_goals}-{a_goals}"

    return False, None, 0, ""

def send_daily_report():
    global sent_tips_history
    if not sent_tips_history: return
    wins = 0
    for mid in sent_tips_history:
        r = requests.get(f"{BASE_URL}/fixtures?id={mid}", headers=HEADERS).json().get("response", [])
        if r and (r[0]['goals']['home'] + r[0]['goals']['away']) >= 2: wins += 1
        time.sleep(1)
    
    rate = (wins / len(sent_tips_history)) * 100
    send_telegram(f"📊 <b>NAPI MÉRLEG</b>\n✅ Nyert: {wins}\n❌ Vesztett: {len(sent_tips_history)-wins}\n🎯 Hatékonyság: {rate:.1f}%")
    sent_tips_history = []

def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE); get_daily_fixtures()
    while True:
        now = datetime.now(tz)
        if now.hour == 23 and now.minute == 55: send_daily_report(); time.sleep(60)
        if now.hour == 4 and now.minute == 1: get_daily_fixtures(); sent_ids.clear(); time.sleep(60)
        if 0 <= now.hour < 4: time.sleep(60); continue

        try:
            r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
            fixtures = r.json().get("response", [])
            for fx in fixtures:
                mid = fx["fixture"]["id"]
                if mid in daily_targets and mid not in sent_ids:
                    send, tip, conf, score = should_send_tip(fx)
                    if send:
                        msg = f"🌟 <b>STRATÉGIAI TIPP</b>\n\n<b>Meccs:</b> {fx['teams']['home']['name']} - {fx['teams']['away']['name']}\n<b>Állás:</b> {score} ({fx['fixture']['status']['elapsed']}. perc)\n<b>Tipp:</b> {tip}\n<b>Biztonság:</b> {conf}%"
                        send_telegram(msg); sent_ids.add(mid); sent_tips_history.append(mid)
        except: pass
        time.sleep(45)

if __name__ == "__main__":
    keep_alive(); main_loop()
