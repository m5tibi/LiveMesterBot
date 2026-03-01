import subprocess, requests, time, os, json, math
from datetime import datetime, timedelta
import pytz
import pandas as pd
from flask import Flask
from threading import Thread

# ========= RENDER ÉBREN TARTÓ =========
app = Flask('')
@app.route('/')
def home(): return "LiveMesterBot EXPERT v3.6: Force Push Aktív"

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

# ========= GITHUB SZINKRONIZÁCIÓ (KÉNYSZERÍTETT) =========
def sync_to_github(file_list, commit_message):
    if not GITHUB_TOKEN:
        print("Nincs GITHUB_TOKEN beállítva.")
        return
    try:
        subprocess.run(["git", "config", "--global", "user.email", "bot@livemester.com"])
        subprocess.run(["git", "config", "--global", "user.name", "LiveMesterBot"])
        
        auth_url = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")
        
        # Távoli URL beállítása és fájlok hozzáadása
        subprocess.run(["git", "remote", "set-url", "origin", auth_url])
        
        for f in file_list:
            if os.path.exists(f): 
                subprocess.run(["git", "add", f])
        
        # Commit készítése
        subprocess.run(["git", "commit", "-m", commit_message])
        
        # KÉNYSZERÍTETT feltöltés a távoli main ágra (HEAD:main)
        result = subprocess.run(["git", "push", "origin", "HEAD:main", "--force"], capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"Sikeres GitHub szinkronizáció: {commit_message}")
        else:
            print(f"Git Push hiba: {result.stderr}")
            
    except Exception as e: 
        print(f"Git hiba a szinkronizáció alatt: {e}")

# ========= MATEMATIKAI MODELLEZÉS =========
def get_over_25_probability(avg_goals):
    if avg_goals <= 0: return 0
    p0 = math.exp(-avg_goals)
    p1 = avg_goals * math.exp(-avg_goals)
    p2 = (avg_goals**2) * math.exp(-avg_goals) / 2
    return max(0, min(100, (1 - (p0 + p1 + p2)) * 100))

def generate_suggestions(avg, fav, s_h, s_a, prob):
    tips = []
    if prob > 75: tips.append("Over 2.5 gól")
    elif prob > 60: tips.append("Over 1.5 gól")
    if fav == "HAZAI" and s_h > 1.7: tips.append("Hazai csapat > 1.5 gól")
    elif fav == "VENDÉG" and s_a > 1.7: tips.append("Vendég csapat > 1.5 gól")
    if avg > 3.3 and fav == "Nincs": tips.append("BTTS")
    return " | ".join(tips) if tips else "Over 1.5 gól"

# ========= STATISZTIKAI ELEMZÉS =========
def get_expert_stats(team_id):
    try:
        r = requests.get(f"{BASE_URL}/fixtures?team={team_id}&last=10", headers=HEADERS, timeout=12)
        res = r.json().get("response", [])
        if not res: return 0, 0, 0, 0
        s, c, cs, p = 0, 0, 0, 0
        for g in res:
            is_h = g['teams']['home']['id'] == team_id
            f = g['goals']['home'] if is_h else g['goals']['away']
            a = g['goals']['away'] if is_h else g['goals']['home']
            s += (f or 0); c += (a or 0)
            if (a or 0) == 0: cs += 1
            if (f or 0) > (a or 0): p += 3
            elif (f or 0) == (a or 0): p += 1
        return s/10, c/10, cs, p
    except: return 0, 0, 0, 0

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

# ========= FŐ FELADATOK =========

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
            if over_prob > 60 and (h_cs + a_cs) < 6:
                fav = "Nincs"
                if h_p >= a_p + 9 and h_sc > a_sc: fav = "HAZAI"
                elif a_p >= h_p + 9 and a_sc > h_sc: fav = "VENDÉG"
                valid.append({
                    "ID": m['fixture']['id'],
                    "IDŐPONT": (datetime.fromisoformat(m['fixture']['date'][:19]).replace(tzinfo=pytz.utc)).astimezone(tz).strftime('%H:%M'),
                    "BAJNOKSÁG": m['league']['name'].upper(),
                    "MECCS": f"{m['teams']['home']['name']} - {m['teams']['away']['name']}",
                    "OVER 2.5 ESÉLY": f"{round(over_prob, 1)}%",
                    "FAVORIT": fav,
                    "TIPP JAVASLAT": generate_suggestions(total_avg, fav, h_sc, a_sc, over_prob)
                })
        if valid:
            cache = {}
            if os.path.exists(CACHE_FILE):
                try:
                    with open(CACHE_FILE, 'r') as f: cache = json.load(f)
                except: cache = {}
            cache[target] = valid
            with open(CACHE_FILE, 'w') as f: json.dump(cache, f)
            f_name = f"expert_lista_{target}.xlsx"
            pd.DataFrame(valid).to_excel(f_name, index=False)
            send_telegram(f"✅ Lista kész!", f_name)
            sync_to_github([CACHE_FILE, f_name], f"Update cache: {target}")
    except Exception as e: send_telegram(f"⚠️ Hiba a szkennerben: {e}")

def get_final_report():
    tz = pytz.timezone(TIMEZONE)
    yest = (datetime.now(tz) - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # Éjféli Pull a GitHubról a friss adatokért (detached HEAD kezeléssel)
    if GITHUB_TOKEN:
        auth_url = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")
        subprocess.run(["git", "fetch", "origin", "main"])
        subprocess.run(["git", "checkout", "origin/main", CACHE_FILE], stderr=subprocess.DEVNULL)
    
    if not os.path.exists(CACHE_FILE):
        send_telegram(f"📊 <b>Jelentés ({yest}):</b>\n⚠️ Cache fájl nem található a kiértékeléshez.")
        return

    try:
        with open(CACHE_FILE, 'r') as f: cache = json.load(f)
        matches = cache.get(yest, [])
    except:
        send_telegram(f"📊 <b>Jelentés ({yest}):</b>\n⚠️ Cache fájl sérült.")
        return
    
    if not matches:
        send_telegram(f"📊 <b>Jelentés ({yest}):</b>\n⚠️ Nincs mentett adat ehhez a naphoz.")
        return

    send_telegram(f"📊 <b>Napi jelentés ({yest})</b>\nEredmények lekérése...")
    final = []
    for m in matches:
        try:
            r = requests.get(f"{BASE_URL}/fixtures?id={m['ID']}", headers=HEADERS).json().get("response", [])
            if r:
                res = r[0]; h, a = res['goals']['home'], res['goals']['away']
                m["EREDMÉNY"] = f"{h}-{a}" if h is not None else "Elmaradt"
                m["SIKER"] = "✅" if h is not None and (h+a) >= 2.5 else "❌"
            final.append(m); time.sleep(1)
        except: continue

    f_name = f"eredmenyek_{yest}.xlsx"
    pd.DataFrame(final).to_excel(f_name, index=False)
    send_telegram(f"📈 A tegnapi nap kiértékelve.", f_name)
    sync_to_github([f_name], f"Summary: {yest}")

# ========= IDŐZÍTŐ CIKLUS =========
def main_loop():
    sent_ids = set(); tz = pytz.timezone(TIMEZONE)
    print("Bot elindult, várakozás a feladatokra...")
    while True:
        now = datetime.now(tz)
        # 16:00 - Szkenner
        if now.hour == 16:15 and now.minute == 0:
            scan_next_day(); time.sleep(61)
        # 00:10 - Összegző
        if now.hour == 0 and now.minute == 10:
            get_final_report(); sent_ids.clear(); time.sleep(61)
            
        time.sleep(30)

if __name__ == "__main__":
    keep_alive(); main_loop()
