import subprocess, requests, time, os, json, math
from datetime import datetime, timedelta
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

# ========= RENDER ÉBREN TARTÓ =========
app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot EXPERT v4.6: Persistent Memory & Auto-Cleanup Active"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server); t.daemon = True; t.start()

# ========= KONFIGURÁCIÓ =========
API_KEY = os.environ.get("FOOTBALL_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
REPO_URL = "https://github.com/m5tibi/LiveMesterBot.git"
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
TIMEZONE = "Europe/Budapest"

CACHE_FILE = "foci_master_cache.json"
LIVE_HISTORY_FILE = "live_history.json"
SENT_ALERTS_FILE = "sent_alerts.json"
TEAM_STATS_CACHE_FILE = "team_stats_cache.json"

# ========= SEGÉDFÜGGVÉNYEK =========

def send_telegram(message, file_path=None):
    try:
        if file_path:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
            with open(file_path, 'rb') as f:
                requests.post(url, data={"chat_id": CHAT_ID, "caption": message, "parse_mode": "HTML"}, files={"document": f}, timeout=45)
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=20)
    except: pass

def load_json(file, default):
    if os.path.exists(file):
        try:
            with open(file, 'r') as f: return json.load(f)
        except: return default
    return default

def save_json(file, data):
    with open(file, 'w') as f: json.dump(data, f)

def sync_to_github(file_list, commit_message, delete_files=None):
    if not GITHUB_TOKEN: return
    try:
        subprocess.run(["git", "config", "--global", "user.email", "bot@livemester.com"])
        subprocess.run(["git", "config", "--global", "user.name", "LiveMesterBot"])
        auth_url = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")
        subprocess.run(["git", "remote", "remove", "origin"], stderr=subprocess.DEVNULL)
        subprocess.run(["git", "remote", "add", "origin", auth_url])
        
        if delete_files:
            for df in delete_files:
                subprocess.run(["git", "rm", df], stderr=subprocess.DEVNULL)
        
        for f in file_list:
            if os.path.exists(f): subprocess.run(["git", "add", f])
            
        subprocess.run(["git", "commit", "-m", commit_message])
        subprocess.run(["git", "push", "origin", "HEAD:main", "--force"])
    except: pass

# ========= TAKARÍTÁS (7 NAPNÁL RÉGEBBI EXCEL) =========

def cleanup_old_files():
    files_to_delete = []
    now = datetime.now()
    cutoff = now - timedelta(days=7)
    
    for f in os.listdir('.'):
        if (f.startswith("expert_lista_") or f.startswith("report_")) and f.endswith(".xlsx"):
            try:
                # Fájlnévből dátum kinyerése (expert_lista_YYYY-MM-DD.xlsx)
                date_str = f.split('_')[-1].split('.xlsx')[0]
                file_date = datetime.strptime(date_str, '%Y-%m-%d')
                if file_date < cutoff:
                    os.remove(f)
                    files_to_delete.append(f)
            except: pass
    return files_to_delete

# ========= STATISZTIKAI MOTOR =========

def get_team_detailed_data(team_id):
    cache = load_json(TEAM_STATS_CACHE_FILE, {})
    if str(team_id) in cache: return cache[str(team_id)]
    
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=12)
        matches = r.json().get("response", [])
        if not matches: return None
        s, c, corn_list, btts_count = 0, 0, [], 0
        for i, m in enumerate(matches):
            is_h = m['teams']['home']['id'] == team_id
            scored = m['goals']['home'] if is_h else m['goals']['away']
            conceded = m['goals']['away'] if is_h else m['goals']['home']
            s += (scored or 0); c += (conceded or 0)
            if i < 5 and (scored or 0) > 0 and (conceded or 0) > 0: btts_count += 1
            if i < 5:
                fid = m['fixture']['id']
                sr = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={fid}&team={team_id}", headers=HEADERS, timeout=10)
                stats = sr.json().get("response", [])
                if stats:
                    for stat in stats[0].get('statistics', []):
                        if stat['type'] == 'Corner Kicks': corn_list.append(stat['value'] or 0)
        res = {
            "avg_scored": s/10, "avg_conceded": c/10, "btts_trend": btts_count,
            "corner_avg": sum(corn_list)/len(corn_list) if len(corn_list) >= 3 else None
        }
        cache[str(team_id)] = res
        save_json(TEAM_STATS_CACHE_FILE, cache)
        return res
    except: return None

# ========= SZKENNER =========

def scan_next_day():
    tz = pytz.timezone(TIMEZONE)
    target = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    send_telegram(f"🔬 <b>EXPERT v4.6 Deep Scan: {target}</b>")
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={target}", headers=HEADERS, timeout=30)
        matches = r.json().get("response", [])
        valid = []
        for m in matches:
            h_data = get_team_detailed_data(m['teams']['home']['id'])
            a_data = get_team_detailed_data(m['teams']['away']['id'])
            if not h_data or not a_data: continue
            
            total_avg = (h_data['avg_scored'] + h_data['avg_conceded'] + a_data['avg_scored'] + a_data['avg_conceded']) / 2
            over_prob = (1 - (math.exp(-total_avg) * (1 + total_avg + (total_avg**2)/2))) * 100
            
            tips = []
            if over_prob > 82: tips.append("Over 2.5")
            elif over_prob > 68: tips.append("Over 1.5")
            if h_data['avg_scored'] > 1.1 and a_data['avg_scored'] > 1.1 and h_data['btts_trend'] >= 2 and a_data['btts_trend'] >= 2:
                if over_prob > 80: tips.append("Over 2.5 & BTTS")
                else: tips.append("BTTS")
            
            corner_info = "N/A"
            if h_data['corner_avg'] is not None and a_data['corner_avg'] is not None:
                exp_corners = h_data['corner_avg'] + a_data['corner_avg']
                corner_info = round(exp_corners, 1)
                if exp_corners >= 10.5: tips.append("Corners Over 8.5")
                elif exp_corners >= 9.2: tips.append("Corners Over 7.5")

            if tips:
                valid.append({"ID": m['fixture']['id'], "IDŐPONT": (datetime.fromisoformat(m['fixture']['date'][:19]).replace(tzinfo=pytz.utc)).astimezone(tz).strftime('%H:%M'), "BAJNOKSÁG": m['league']['name'].upper(), "MECCS": f"{m['teams']['home']['name']} - {m['teams']['away']['name']}", "OVER 2.5 ESÉLY": f"{round(over_prob, 1)}%", "VÁRHATÓ SZÖGLET": corner_info, "TIPP JAVASLAT": " | ".join(tips)})
        
        if valid:
            cache = load_json(CACHE_FILE, {})
            cache[target] = valid; save_json(CACHE_FILE, cache)
            f_name = f"expert_lista_{target}.xlsx"
            pd.DataFrame(valid).to_excel(f_name, index=False)
            send_telegram(f"✅ Deep Scan kész!", f_name)
            sync_to_github([CACHE_FILE, f_name, TEAM_STATS_CACHE_FILE], f"v4.6 Update: {target}")
    except Exception as e: send_telegram(f"⚠️ Hiba: {e}")

# ========= JELENTÉS ÉS TAKARÍTÁS =========

def get_final_report():
    tz = pytz.timezone(TIMEZONE)
    yest = (datetime.now(tz) - timedelta(days=1)).strftime('%Y-%m-%d')
    cache = load_json(CACHE_FILE, {})
    matches = cache.get(yest, [])
    if not matches: return

    send_telegram(f"📊 <b>Összetett jelentés ({yest})</b>")
    final = []
    for m in matches:
        try:
            r = requests.get(f"{BASE_URL}/fixtures?id={m['ID']}", headers=HEADERS).json().get("response", [])
            if r:
                res = r[0]; h, a = res['goals']['home'], res['goals']['away']
                c_total = 0
                if 'statistics' in res:
                    for s_set in res['statistics']:
                        for it in s_set['statistics']:
                            if it['type'] == 'Corner Kicks': c_total += (it['value'] or 0)
                m["EREDMÉNY"] = f"{h}-{a}"; m["GÓL SIKER"] = "✅" if (h+a) >= 2.5 else "❌"
                m["BTTS SIKER"] = "✅" if (h or 0) > 0 and (a or 0) > 0 else "❌"; m["SZÖGLET ÖSSZ"] = c_total
            final.append(m); time.sleep(1)
        except: continue

    live_history = load_json(LIVE_HISTORY_FILE, [])
    live_wins = 0
    if live_history:
        for lt in live_history:
            try:
                r = requests.get(f"{BASE_URL}/fixtures?id={lt['id']}", headers=HEADERS).json().get("response", [])
                if r:
                    res = r[0]; h_f, a_f = (res['goals']['home'] or 0), (res['goals']['away'] or 0)
                    if (h_f + a_f) >= 2: live_wins += 1
            except: continue
        live_msg = f"📱 <b>LIVE ÖSSZESÍTŐ:</b>\n🎯 Küldött: {len(live_history)}\n✅ Nyert (O1.5): {live_wins}"
    else: live_msg = "📱 <b>LIVE ÖSSZESÍTŐ:</b>\nMa nem volt élő tipp."
    
    f_name = f"report_{yest}.xlsx"
    pd.DataFrame(final).to_excel(f_name, index=False)
    send_telegram(live_msg, f_name)
    
    # Régi fájlok takarítása
    deleted_files = cleanup_old_files()
    save_json(LIVE_HISTORY_FILE, [])
    save_json(SENT_ALERTS_FILE, []) # Éjfélkor nullázzuk a riasztásokat
    
    sync_to_github([f_name, LIVE_HISTORY_FILE, SENT_ALERTS_FILE], f"Final Report: {yest}", delete_files=deleted_files)

# ========= FŐ CIKLUS =========

def main_loop():
    tz = pytz.timezone(TIMEZONE)
    print("Bot v4.6 elindult...")
    while True:
        now = datetime.now(tz)
        if now.hour == 19 and now.minute == 00: scan_next_day(); time.sleep(61)
        if now.hour == 0 and now.minute == 10: get_final_report(); time.sleep(61)
        
        try:
            today_m = load_json(CACHE_FILE, {}).get(now.strftime('%Y-%m-%d'), [])
            sent_alerts = load_json(SENT_ALERTS_FILE, [])
            
            if today_m:
                t_ids = [m['ID'] for m in today_m]
                r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
                for fx in r.json().get("response", []):
                    mid = fx["fixture"]["id"]
                    # CSAK AKKOR KÜLDJÜK, HA NINCS A SENT_ALERTS-BEN
                    if mid in t_ids and str(mid) not in sent_alerts:
                        min_ = fx["fixture"]["status"]["elapsed"] or 0
                        h, a = (fx["goals"]["home"] or 0), (fx["goals"]["away"] or 0)
                        
                        if ((35 <= min_ <= 42) or (50 <= min_ <= 65)) and (h+a) <= 1:
                            sr = requests.get(f"{BASE_URL}/fixtures/statistics?fixture={mid}", headers=HEADERS).json().get("response", [])
                            shots = 0
                            if sr:
                                for team in sr:
                                    for s in team['statistics']:
                                        if s['type'] in ['Shots on Goal', 'Shots off Goal']: shots += (s['value'] or 0)
                            
                            if shots >= 4:
                                send_telegram(f"⚽ <b>STRICT LIVE: Over 1.5</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\n{h}-{a} ({min_}. perc)\n📈 Aktivitás: {shots} lövés")
                                sent_alerts.append(str(mid))
                                save_json(SENT_ALERTS_FILE, sent_alerts)
                                
                                hst = load_json(LIVE_HISTORY_FILE, [])
                                hst.append({"id": mid, "time": now.strftime('%H:%M')})
                                save_json(LIVE_HISTORY_FILE, hst)
        except: pass
        time.sleep(40)

if __name__ == "__main__":
    keep_alive(); main_loop()
