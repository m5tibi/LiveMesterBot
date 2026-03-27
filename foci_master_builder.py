import os
import json
import numpy as np
import requests
from datetime import datetime, timedelta, timezone
import pytz
from typing import List, Dict, Any
from supabase import create_client, Client

# ========= KONFIGURÁCIÓ =========
API_KEY = os.environ.get("FOOTBALL_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}
HISTORY_FILE = "automated_tips_history.json"

def load_league_config():
    env_val = os.environ.get("FOCI_MASTER_LEAGUES")
    if env_val:
        try:
            return json.loads(env_val)
        except Exception:
            pass

    return [
        {"country": "England",   "league_id": 39},
        {"country": "England",   "league_id": 40},
        {"country": "England",   "league_id": 41},
        {"country": "England",   "league_id": 42},
        {"country": "Germany",   "league_id": 78},
        {"country": "Germany",   "league_id": 79},
        {"country": "Netherlands", "league_id": 88},
        {"country": "Netherlands", "league_id": 89},
        {"country": "Austria",   "league_id": 218},
        {"country": "Scotland",  "league_id": 179},
        {"country": "Spain",     "league_id": 140},
        {"country": "Spain",     "league_id": 141},
        {"country": "Italy",     "league_id": 135},
        {"country": "Italy",     "league_id": 136},
        {"country": "France",    "league_id": 61},
        {"country": "France",    "league_id": 62},
        {"country": "Portugal",  "league_id": 94},
        {"country": "Belgium",   "league_id": 144},
        {"country": "Turkey",    "league_id": 203},
        {"country": "Greece",    "league_id": 197},
        {"country": "Denmark",   "league_id": 119},
        {"country": "Switzerland", "league_id": 207}
    ]

# ========= MONTE CARLO & ROI MOTOR =========

def run_monte_carlo(home_lambda: float, away_lambda: float, iterations: int = 10000) -> Dict[str, Any]:
    h_l = max(home_lambda, 0.1)
    a_l = max(away_lambda, 0.1)
    home_goals = np.random.poisson(h_l, iterations)
    away_goals = np.random.poisson(a_l, iterations)
    total_goals = home_goals + away_goals
    
    prob_o15 = np.mean(total_goals > 1.5)
    prob_o25 = np.mean(total_goals > 2.5)
    prob_btts = np.mean((home_goals > 0) & (away_goals > 0))
    
    scores = [f"{h}-{a}" for h, a in zip(home_goals, away_goals)]
    predicted_score = max(set(scores), key=scores.count)
    
    return {
        "o15": float(prob_o15),
        "o25": float(prob_o25),
        "btts": float(prob_btts),
        "score": predicted_score
    }

def evaluate_previous_day():
    if not os.path.exists(HISTORY_FILE): return
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            history = json.load(f)
        
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d')
        if yesterday not in history: return

        tips = history[yesterday]
        wins, total_spent, total_return = 0, 0, 0
        report = f"📊 <b>NAPI KIÉRTÉKELÉS ({yesterday})</b>\n───────────────────\n"
        
        for t in tips:
            try:
                r = requests.get(f"{BASE_URL}/fixtures?id={t['id']}", headers=HEADERS, timeout=15).json().get("response", [])
                if r and r[0]['fixture']['status']['short'] == 'FT':
                    h, a = r[0]['goals']['home'], r[0]['goals']['away']
                    is_win = False
                    if t['market'] == 'over25' and (h+a) > 2.5: is_win = True
                    elif t['market'] == 'btts_yes' and (h or 0) > 0 and (a or 0) > 0: is_win = True
                    elif t['market'] == 'over15' and (h+a) > 1.5: is_win = True
                    
                    total_spent += 1
                    status_icon = "✅" if is_win else "❌"
                    if is_win:
                        wins += 1
                        total_return += t['odds']
                    report += f"{status_icon} {t['teams']} ({h}-{a}) @{t['odds']}\n"
            except: continue

        if total_spent > 0:
            roi = ((total_return - total_spent) / total_spent * 100)
            report += f"───────────────────\n🎯 <b>Mérleg: {wins}/{total_spent}</b>\n💰 <b>ROI: {roi:+.1f}%</b>"
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                          data={"chat_id": CHAT_ID, "text": report, "parse_mode": "HTML"})
    except Exception as e:
        print(f"Hiba a kiértékelés során: {e}")

# ========= ADATGYŰJTŐ FÜGGVÉNYEK =========

def get_fixtures_for_date(date_str: str) -> List[Dict]:
    leagues = load_league_config()
    all_fixtures = []
    for l_cfg in leagues:
        url = f"{BASE_URL}/fixtures?league={l_cfg['league_id']}&season=2025&date={date_str}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20).json()
            all_fixtures.extend(resp.get("response", []))
        except: continue
    return all_fixtures

def get_team_last_matches(team_id: int, last_n: int = 10) -> List[Dict]:
    url = f"{BASE_URL}/fixtures?team={team_id}&last={last_n}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15).json()
        return resp.get("response", [])
    except: return []

def compute_basic_stats_from_matches(matches: List[Dict], team_id: int):
    if not matches: return None
    g_scored, g_conceded = 0, 0
    o15, o25, btts = 0, 0, 0
    for m in matches:
        h_id = m["teams"]["home"]["id"]
        h_g, a_g = m["goals"]["home"] or 0, m["goals"]["away"] or 0
        s, c = (h_g, a_g) if h_id == team_id else (a_g, h_g)
        g_scored += s
        g_conceded += c
        if (h_g + a_g) > 1.5: o15 += 1
        if (h_g + a_g) > 2.5: o25 += 1
        if h_g > 0 and a_g > 0: btts += 1
    n = len(matches)
    return {
        "avg_scored": g_scored / n, "avg_conceded": g_conceded / n,
        "o15_rate": o15 / n, "o25_rate": o25 / n, "btts_rate": btts / n
    }

def get_odds_for_fixture(fixture_id: int) -> Dict[str, float]:
    url = f"{BASE_URL}/odds?fixture={fixture_id}"
    odds_out = {"over15": None, "over25": None, "btts_yes": None}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15).json()
        for b in resp.get("response", []):
            if b["bookmaker"]["name"] == "Bet365":
                for bet in b["bets"]:
                    if bet["name"] == "Goals Over/Under":
                        for v in bet["values"]:
                            if v["value"] == "Over 1.5": odds_out["over15"] = float(v["odd"])
                            if v["value"] == "Over 2.5": odds_out["over25"] = float(v["odd"])
                    if bet["name"] == "Both Teams Score":
                        for v in bet["values"]:
                            if v["value"] == "Yes": odds_out["btts_yes"] = float(v["odd"])
    except: pass
    return odds_out

def upload_to_supabase(file_path: str, date_str: str, bucket_key: str = "foci-master"):
    if not SUPABASE_URL or not SUPABASE_KEY: return
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        f_name = os.path.basename(file_path)
        r_path = f"{date_str}/{f_name}"
        with open(file_path, "rb") as f:
            supabase.storage.from_(bucket_key).upload(r_path, f.read(), {"content-type": "application/json", "x-upsert": "true"})
    except Exception as e: print(f"Supabase error: {e}")

# ========= ELITE TIPPGENERÁLÁS ÉS TELEGRAM =========

def generate_multi_market_tips_mc(fixtures_out: List[Dict], date_str: str, max_tips=10):
    candidates = []
    for f in fixtures_out:
        dp = f.get("derived_profile")
        if not dp: continue
        
        mc = run_monte_carlo(dp.get("home_expected_goals", 0), dp.get("away_expected_goals", 0))
        o = f.get("odds", {})
        mkts = [
            {"m": "over15", "p": mc["o15"], "odds": o.get("over15"), "ev_min": 2.5},
            {"m": "over25", "p": mc["o25"], "odds": o.get("over25"), "ev_min": 4.0},
            {"m": "btts_yes", "p": mc["btts"], "odds": o.get("btts_yes"), "ev_min": 5.0}
        ]
        b_ev, b_tip = -999, None
        for m in mkts:
            if m["odds"] and m["odds"] > 1:
                ev = (m["p"] * m["odds"] - 1) * 100
                if ev > m["ev_min"] and ev > b_ev:
                    b_ev = ev
                    b_tip = {
                        "id": f["fixture_id"], "league": f["league_name"], "teams": f"{f['home_name']} - {f['away_name']}",
                        "market": m["m"], "odds": m["odds"], "p": m["p"], "ev": round(ev, 1), "sc": mc["score"],
                        "time": f["start_time"]
                    }
        if b_tip: candidates.append(b_tip)
    
    res = sorted(candidates, key=lambda x: x["ev"], reverse=True)[:max_tips]
    hist = {}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f: hist = json.load(f)
    hist[date_str] = res
    with open(HISTORY_FILE, 'w') as f: json.dump(hist, f)
    return res

def send_premium_telegram(tips, date_str):
    if not tips: return
    msg = f"🏆 <b>ELITE AUTOMATA TIPPEK</b> 🏆\n📅 <i>{date_str}</i>\n───────────────────\n"
    icons = {"over25": "⚽️", "over15": "🥅", "btts_yes": "🔄"}
    for t in tips:
        t_s = t['time'].split('T')[1][:5] if 'T' in t['time'] else "??:??"
        m_l = "Over 2.5" if t['market'] == "over25" else "BTTS" if t['market'] == "btts_yes" else "Over 1.5"
        msg += f"⏰ <b>{t_s}</b> | {t['league']}\n⚔️ <b>{t['teams']}</b>\n"
        msg += f"{icons.get(t['market'], '👉')} Tipp: <b>{m_l}</b>\n"
        msg += f"📊 Odds: <b>{t['odds']}</b> | MC Esély: <b>{int(t['p']*100)}%</b>\n"
        msg += f"💎 EV: <b>{t['ev']}%</b> | 🎯 Tipp: <b>{t['sc']}</b>\n───────────────────\n"
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"})

# ========= FŐ CIKLUS =========

def main():
    # 1. Tegnapi kiértékelés
    evaluate_previous_day()
    
    # 2. Új nap szkennelése
    tz = pytz.timezone("Europe/Budapest")
    target_date = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    print(f"Szkennelés indul: {target_date}...")
    
    fixtures = get_fixtures_for_date(target_date)
    fixtures_out = []
    
    for fx in fixtures:
        fid = fx["fixture"]["id"]
        h_id = fx["teams"]["home"]["id"]
        a_id = fx["teams"]["away"]["id"]
        
        # Statisztikák gyűjtése
        h_matches = get_team_last_matches(h_id)
        a_matches = get_team_last_matches(a_id)
        h_stats = compute_basic_stats_from_matches(h_matches, h_id)
        a_stats = compute_basic_stats_from_matches(a_matches, a_id)
        
        if h_stats and a_stats:
            h_exp = (h_stats["avg_scored"] + a_stats["avg_conceded"]) / 2
            a_exp = (a_stats["avg_scored"] + h_stats["avg_conceded"]) / 2
            
            fixture_obj = {
                "fixture_id": fid,
                "start_time": fx["fixture"]["date"],
                "league_name": fx["league"]["name"],
                "home_name": fx["teams"]["home"]["name"],
                "away_name": fx["teams"]["away"]["name"],
                "odds": get_odds_for_fixture(fid),
                "derived_profile": {"home_expected_goals": h_exp, "away_expected_goals": a_exp}
            }
            fixtures_out.append(fixture_obj)

    # Lokális JSON és Supabase
    out_file = f"foci_master_{target_date}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"date": target_date, "fixtures": fixtures_out}, f, indent=2, ensure_ascii=False)
    
    upload_to_supabase(out_file, target_date)
    
    # TIPPGENERÁLÁS (MONTE CARLO-VAL)
    elite_tips = generate_multi_market_tips_mc(fixtures_out, target_date)
    send_premium_telegram(elite_tips, target_date)
    print(f"Kész. Küldve {len(elite_tips)} tipp.")

if __name__ == "__main__":
    main()
