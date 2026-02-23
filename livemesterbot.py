import requests
import time
import os
import json
from datetime import datetime, timedelta
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot PRO: Foci Elemző és Szelvényépítő"

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
CACHE_FILE = "foci_master_cache.json"
LIVE_HISTORY_FILE = "live_history.json"

def save_json(file, data):
    with open(file, 'w') as f: json.dump(data, f)

def load_json(file, default):
    if os.path.exists(file):
        with open(file, 'r') as f: return json.load(f)
    return default

def send_telegram(message, file_path=None):
    try:
        if file_path:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
            with open(file_path, 'rb') as f:
                requests.post(url, data={"chat_id": CHAT_ID, "caption": message, "parse_mode": "HTML"}, files={"document": f}, timeout=40)
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=20)
    except: pass

def get_team_detailed_stats(team_id):
    """Gólátlag (összes), Rúgott gólátlag és Forma-pontok lekérése."""
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=12)
        res = r.json().get("response", [])
        if not res: return 0, 0, 0
        
        total_goals, scored_goals, points = 0, 0, 0
        for g in res:
            is_home = g['teams']['home']['id'] == team_id
            goals_f = g['goals']['home'] if is_home else g['goals']['away']
            goals_a = g['goals']['away'] if is_home else g['goals']['home']
            
            total_goals += (g['goals']['home'] or 0) + (g['goals']['away'] or 0)
            scored_goals += (goals_f or 0)
            if (goals_f or 0) > (goals_a or 0): points += 3
            elif (goals_f or 0) == (goals_a or 0): points += 1
            
        count = len(res)
        return total_goals/count, scored_goals/count, points
    except: return 0, 0, 0

def generate_suggestions(avg, fav, scored_h, scored_a):
    """Konkrét fogadási ötletek generálása."""
    tips = []
    if avg >= 3.0: tips.append("Over 2.5 gól")
    if avg >= 3.6: tips.append("Over 3.5 gól")
    
    if fav == "HAZAI":
        tips.append("1X és Over 1.5")
        if scored_h > 1.8: tips.append("Hazai Over 1.5 csapatgól")
    elif fav == "VENDÉG":
        tips.append("X2 és Over 1.5")
        if scored_a > 1.8: tips.append("Vendég Over 1.5 csapatgól")
        
    return " | ".join(tips) if tips else "Over 1.5 gól"

def scan_next_day():
    tz = pytz.timezone(TIMEZONE)
    target_date = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    send_telegram(f"📊 <b>Szelvényépítő Szkenner: {target_date}</b>")
    
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={target_date}", headers=HEADERS, timeout=30)
        matches = r.json().get("response", [])
        valid_matches = []
        
        for m in matches:
            if "friendly" in m['league']['name'].lower(): continue
            
            h_id, a_id = m['teams']['home']['id'], m['teams']['away']['id']
            avg_h, scored_h, points_h = get_team_detailed_stats(h_id)
            avg_a, scored_a, points_a = get_team_detailed_stats(a_id)
            combined_avg = (avg_h + avg_a) / 2
            
            if combined_avg >= 3.0:
                fav = "Nincs"
                if points_h >= points_a + 8 and scored_h > scored_a: fav = "HAZAI"
                elif points_a >= points_h + 8 and scored_a > scored_h: fav = "VENDÉG"

                valid_matches.append({
                    "ID": m['fixture']['id'],
                    "IDŐPONT": (datetime.fromisoformat(m['fixture']['date'][:19]).replace(tzinfo=pytz.utc)).astimezone(tz).strftime('%H:%M'),
                    "BAJNOKSÁG": m['league']['name'].upper(),
                    "HAZAI": m['teams']['home']['name'],
                    "VENDÉG": m['teams']['away']['name'],
                    "ÖSSZ. GÓL ÁTLAG": round(combined_avg, 2),
                    "FAVORIT": fav,
                    "JAVASOLT TIPPEK": generate_suggestions(combined_avg, fav, scored_h, scored_a),
                    "FORMA (H-V)": f"{points_h}-{points_a} pont"
                })

        if valid_matches:
            cache = load_json(CACHE_FILE, {})
            cache[target_date] = valid_matches
            save_json(CACHE_FILE, cache)
            
            file_name = f"szelveny_epito_{target_date}.xlsx"
            pd.DataFrame(valid_matches).to_excel(file_name, index=False)
            send_telegram(f"✅ <b>Holnapi lista elkészült!</b>", file_name)
            os.remove(file_name)
    except Exception as e:
        send_telegram(f"⚠️ Hiba: {e}")

def get_final_report():
    tz = pytz.timezone(TIMEZONE)
    today_str = (datetime.now(tz) - timedelta(days=1)).strftime('%Y-%m-%d') # Éjfél után hívjuk, tehát a tegnapit nézzük
    cache = load_json(CACHE_FILE, {})
    matches = cache.get(today_str, [])
    live_history = load_json(LIVE_HISTORY_FILE, [])

    if not matches: return

    send_telegram(f"📊 <b>Napi jelentés ({today_str})</b>")
    final_rows = []
    for m in matches:
        try:
            r = requests.get(f"{BASE_URL}/fixtures?id={m['ID']}", headers=HEADERS).json().get("response", [])
            if r:
                res = r[0]
                h, a = res['goals']['home'], res['goals']['away']
                m["VÉGEREDMÉNY"] = f"{h}-{a}" if h is not None else "N/A"
                m["GÓL TIPP SIKER"] = "✅" if h is not None and (h+a) >= 2 else "❌"
            final_rows.append(m)
            time.sleep(0.5)
        except: continue

    excel_name = f"eredmenyek_{today_str}.xlsx"
    pd.DataFrame(final_rows).to_excel(excel_name, index=False)
    
    wins = sum(1 for x in live_history if x.get('win'))
    total = len(live_history)
    msg = f"📈 <b>ÉLŐ TIPPEK MÉRLEGE</b>\n✅ Nyert: {wins}\n❌ Vesztett: {total-wins}\n🎯 { (wins/total*100) if total > 0 else 0 :.1f}%"
    
    send_telegram(msg, excel_name)
    os.remove(excel_name)

def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE)
    while True:
        now = datetime.now(tz)
        
        # 16:00 - Szkenner holnapra
        if now.hour == 16 and now.minute == 0:
            scan_next_day(); time.sleep(60)
            
        # 00:10 - Összegző a tegnapi 0-24h meccseiről
        if now.hour == 0 and now.minute == 10:
            get_final_report(); sent_ids.clear(); time.sleep(60)

        # Élő figyelés (csak a napi cache alapján)
        if 0 <= now.hour <= 23:
            cache = load_json(CACHE_FILE, {})
            today_matches = cache.get(now.strftime('%Y-%m-%d'), [])
            if today_matches:
                target_ids = [m['ID'] for m in today_matches]
                try:
                    r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
                    for fx in r.json().get("response", []):
                        mid = fx["fixture"]["id"]
                        if mid in target_ids and mid not in sent_ids:
                            minute = fx["fixture"]["status"]["elapsed"] or 0
                            h_g, a_g = (fx["goals"]["home"] or 0), (fx["goals"]["away"] or 0)
                            if 25 < minute < 65 and (h_g + a_g) < 2:
                                send_telegram(f"⚽ <b>ÉLŐ TIPP: Over 1.5 gól</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\n{h_g}-{a_g} ({minute}. perc)")
                                sent_ids.add(mid)
                except: pass
        time.sleep(60)

if __name__ == "__main__":
    keep_alive(); main_loop()
