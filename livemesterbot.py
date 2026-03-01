import subprocess, requests, time, os, json, math
from datetime import datetime, timedelta
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

# ========= RENDER ÉBREN TARTÓ =========
app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot EXPERT v3.4: JSON Fix Aktív"

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

# ========= SEGÉDFÜGGVÉNYEK =========
def load_json(file, default):
    if os.path.exists(file):
        try:
            if os.path.getsize(file) > 0:
                with open(file, 'r') as f:
                    return json.load(f)
        except:
            pass
    return default

def save_json(file, data):
    with open(file, 'w') as f:
        json.dump(data, f)

# (A korábbi Poisson, GitHub szinkron és statisztikai függvények maradnak...)

def sync_to_github(file_list, commit_message):
    if not GITHUB_TOKEN: return
    try:
        subprocess.run(["git", "config", "--global", "user.email", "bot@livemester.com"])
        subprocess.run(["git", "config", "--global", "user.name", "LiveMesterBot"])
        auth_url = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")
        for f in file_list:
            if os.path.exists(f): subprocess.run(["git", "add", f])
        subprocess.run(["git", "commit", "-m", commit_message])
        subprocess.run(["git", "push", auth_url])
    except Exception as e: print(f"Git hiba: {e}")

def get_over_25_probability(avg_goals):
    if avg_goals <= 0: return 0
    p0 = math.exp(-avg_goals)
    p1 = (avg_goals**1) * math.exp(-avg_goals) / 1
    p2 = (avg_goals**2) * math.exp(-avg_goals) / 2
    prob = (1 - (p0 + p1 + p2)) * 100
    return max(0, min(100, prob))

def generate_suggestions(avg, fav, s_h, s_a, prob):
    tips = []
    if prob > 75: tips.append("Over 2.5 gól")
    elif prob > 60: tips.append("Over 1.5 gól")
    if fav == "HAZAI" and s_h > 1.7: tips.append("Hazai csapat > 1.5 gól")
    elif fav == "VENDÉG" and s_a > 1.7: tips.append("Vendég csapat > 1.5 gól")
    if avg > 3.3 and fav == "Nincs": tips.append("BTTS")
    return " | ".join(tips) if tips else "Over 1.5 gól"

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

def get_expert_stats(team_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=12)
        res = r.json().get("response", [])
        if not res: return 0, 0, 0, 0
        scored, conceded, clean_sheets, pts = 0, 0, 0, 0
        for g in res:
            is_h = g['teams']['home']['id'] == team_id
            f = g['goals']['home'] if is_h else g['goals']['away']
            a = g['goals']['away'] if is_h else g['goals']['home']
            scored += (f or 0); conceded += (a or 0)
            if (a or 0) == 0: clean_sheets += 1
            if (f or 0) > (a or 0): pts += 3
            elif (f or 0) == (a or 0): pts += 1
        return scored/10, conceded/10, clean_sheets, pts
    except: return 0, 0, 0, 0

def scan_next_day():
    tz = pytz.timezone(TIMEZONE)
    target = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    send_telegram(f"🔬 <b>Szakértői Analízis: {target}</b>")
    try:
        r = requests.get(f"{BASE_URL}/fixtures?date={target}", headers=HEADERS, timeout=30)
        matches = r.json().get("response", [])
        valid = []
        for m in matches:
            if "friendly" in m['league']['name'].lower(): continue
            h_sc, h_con, h_cs, h_p = get_expert_stats(m['teams']['home']['id'])
            a_sc, a_con, a_cs, a_p = get_expert_stats(m['teams']['away']['id'])
            total_avg = (h_sc + h_con + a_sc + a_con) / 2
            over_prob = get_over_25_probability(total_avg)
            cs_total = h_cs + a_cs
            if over_prob > 60 and cs_total < 6:
                fav = "Nincs"
                if h_p >= a_p + 9 and h_sc > a_sc: fav = "HAZAI"
                elif a_p >= h_p + 9 and a_sc > h_sc: fav = "VENDÉG"
                valid.append({
                    "ID": m['fixture']['id'],
                    "IDŐPONT": (datetime.fromisoformat(m['fixture']['date'][:19]).replace(tzinfo=pytz.utc)).astimezone(tz).strftime('%H:%M'),
                    "BAJNOKSÁG": m['league']['name'].upper(),
                    "MECCS": f"{m['teams']['home']['name']} - {m['teams']['away']['name']}",
                    "OVER 2.5 ESÉLY": f"{round(over_prob, 1)}%",
                    "FAVORIT": fav, "CLEAN SHEET (20/x)": cs_total, "FORMA (H-V)": f"{h_p}-{a_p}",
                    "TIPP JAVASLAT": generate_suggestions(total_avg, fav, h_sc, a_sc, over_prob)
                })
        if valid:
            cache = load_json(CACHE_FILE, {})
            cache[target] = valid
            save_json(CACHE_FILE, cache)
            f_name = f"expert_lista_{target}.xlsx"
            pd.DataFrame(valid).to_excel(f_name, index=False)
            send_telegram(f"✅ Lista kész!", f_name)
            sync_to_github([CACHE_FILE, f_name], f"Update cache: {target}")
            if os.path.exists(f_name): os.remove(f_name)
    except Exception as e: send_telegram(f"⚠️ Hiba: {e}")

def get_final_report():
    tz = pytz.timezone(TIMEZONE)
    yest = (datetime.now(tz) - timedelta(days=1)).strftime('%Y-%m-%d')
    cache = load_json(CACHE_FILE, {})
    matches = cache.get(yest, [])
    if not matches: return
    send_telegram(f"📊 <b>Napi jelentés ({yest})</b>")
    final = []
    for m in matches:
        try:
            r = requests.get(f"{BASE_URL}/fixtures?id={m['ID']}", headers=HEADERS).json().get("response", [])
            if r:
                res = r[0]; h, a = res['goals']['home'], res['goals']['away']
                m["EREDMÉNY"] = f"{h}-{a}" if h is not None else "N/A"
                m["TIPP NYERT?"] = "✅" if h is not None and (h+a) >= 2.5 else "❌"
            final.append(m); time.sleep(1)
        except: continue
    f_name = f"eredmenyek_{yest}.xlsx"
    pd.DataFrame(final).to_excel(f_name, index=False)
    send_telegram(f"📊 Tegnapi statisztika:", f_name)
    sync_to_github([f_name], f"Summary: {yest}")

def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE)
    while True:
        now = datetime.now(tz)
        if now.hour == 16 and now.minute == 0: scan_next_day(); time.sleep(60)
        if now.hour == 0 and now.minute == 10: get_final_report(); sent_ids.clear(); time.sleep(60)
        if 0 <= now.hour <= 23:
            try:
                cache = load_json(CACHE_FILE, {})
                today_m = cache.get(now.strftime('%Y-%m-%d'), [])
                if today_m:
                    t_ids = [m['ID'] for m in today_m]
                    r = requests.get(f"{BASE_URL}/fixtures?live=all", headers=HEADERS, timeout=10)
                    for fx in r.json().get("response", []):
                        mid = fx["fixture"]["id"]
                        if mid in t_ids and mid not in sent_ids:
                            min_ = fx["fixture"]["status"]["elapsed"] or 0
                            h, a = (fx["goals"]["home"] or 0), (fx["goals"]["away"] or 0)
                            if 25 < min_ < 65 and (h+a) < 2:
                                send_telegram(f"⚽ <b>EXPERT ÉLŐ: Over 1.5</b>\n{fx['teams']['home']['name']} - {fx['teams']['away']['name']}\n{h}-{a} ({min_}. perc)")
                                sent_ids.add(mid)
            except: pass
        time.sleep(60)

if __name__ == "__main__":
    keep_alive(); main_loop()
