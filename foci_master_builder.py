import os
import json
from datetime import datetime, timedelta, timezone
import numpy as np
import requests
from supabase import create_client, Client  # Supabase client
from typing import List, Dict, Any
import pytz

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
        {"country": "Switzerland", "league_id": 207},
        {"country": "Norway",    "league_id": 103},
        {"country": "Sweden",    "league_id": 113},
        {"country": "Poland",    "league_id": 106},
        {"country": "Czech-Republic", "league_id": 345},
        {"country": "Croatia",   "league_id": 210},
        {"country": "Romania",   "league_id": 283},
        {"country": "Hungary",   "league_id": 271},
        {"country": "Ukraine",   "league_id": 333},
        {"country": "USA",       "league_id": 253},
        {"country": "Mexico",    "league_id": 262},
        {"country": "Brazil",    "league_id": 71},
        {"country": "Argentina", "league_id": 128}
    ]

# ========= ÚJ FUNKCIÓK: MONTE CARLO & ROI =========

def run_monte_carlo(home_lambda: float, away_lambda: float, iterations: int = 10000) -> Dict[str, Any]:
    """10,000 szimuláció Poisson eloszlással a pontosabb valószínűségért."""
    h_l = max(home_lambda, 0.1)
    a_l = max(away_lambda, 0.1)
    
    home_goals = np.random.poisson(h_l, iterations)
    away_goals = np.random.poisson(a_l, iterations)
    total_goals = home_goals + away_goals
    
    prob_o15 = np.mean(total_goals > 1.5)
    prob_o25 = np.mean(total_goals > 2.5)
    prob_btts = np.mean((home_goals > 0) & (away_goals > 0))
    
    # Leggyakoribb pontos eredmény (Mode)
    scores = [f"{h}-{a}" for h, a in zip(home_goals, away_goals)]
    predicted_score = max(set(scores), key=scores.count)
    
    return {
        "o15": float(prob_o15),
        "o25": float(prob_o25),
        "btts": float(prob_btts),
        "score": predicted_score
    }

def evaluate_previous_day():
    """Előző napi tippek lezárása és ROI jelentés küldése."""
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
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": report, "parse_mode": "HTML"})
    except: pass

# ========= EREDETI ADATGYŰJTŐ FÜGGVÉNYEK =========

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
    goals_scored, goals_conceded = 0, 0
    over15, over25, btts = 0, 0, 0
    for m in matches:
        home_id = m["teams"]["home"]["id"]
        h_g = m["goals"]["home"] or 0
        a_g = m["goals"]["away"] or 0
        scored, conceded = (h_g, a_g) if home_id == team_id else (a_g, h_g)
        goals_scored += scored
        goals_conceded += conceded
        if (h_g + a_g) > 1.5: over15 += 1
        if (h_g + a_g) > 2.5: over25 += 1
        if h_g > 0 and a_g > 0: btts += 1
    count = len(matches)
    return {
        "avg_scored": goals_scored / count,
        "avg_conceded": goals_conceded / count,
        "over15_rate": over15 / count,
        "over25_rate": over25 / count,
        "btts_rate": btts / count
    }

def get_odds_for_fixture(fixture_id: int) -> Dict[str, float]:
    url = f"{BASE_URL}/odds?fixture={fixture_id}"
    odds_out = {"over15": None, "over25": None, "btts_yes": None}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15).json()
        for bookie in resp.get("response", []):
            if bookie["bookmaker"]["name"] == "Bet365":
                for bet in bookie["bets"]:
                    if bet["name"] == "Goals Over/Under":
                        for val in bet["values"]:
                            if val["value"] == "Over 1.5": odds_out["over15"] = float(val["odd"])
                            if val["value"] == "Over 2.5": odds_out["over25"] = float(val["odd"])
                    if bet["name"] == "Both Teams Score":
                        for val in bet["values"]:
                            if val["value"] == "Yes": odds_out["btts_yes"] = float(val["odd"])
    except: pass
    return odds_out

def upload_to_supabase(file_path: str, date_str: str, bucket_key: str = "foci-master"):
    if not SUPABASE_URL or not SUPABASE_KEY: return
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        file_name = os.path.basename(file_path)
        remote_path = f"{date_str}/{file_name}"
        with open(file_path, "rb") as f:
            supabase.storage.from_(bucket_key).upload(remote_path, f.read(), {"content-type": "application/json", "x-upsert": "true"})
    except Exception as e:
        print(f"Supabase hiba: {e}")

# ========= TIPPEK GENERÁLÁSA ÉS PRÉMIUM KÜLDÉS =========

def generate_multi_market_tips_mc(fixtures_out: List[Dict], date_str: str, max_tips=10):
    candidates = []
    for f in fixtures_out:
        dp = f.get("derived_profile")
        if not dp: continue
        
        # Monte Carlo szimuláció a lambda értékek alapján
        mc = run_monte_carlo(dp.get("home_expected_goals", 0), dp.get("away_expected_goals", 0))
        
        odds = f.get("odds", {})
        markets = [
            {"type": "over15",   "p": mc["o15"],  "odds": odds.get("over15"),  "min_ev": 2.5},
            {"type": "over25",   "p": mc["o25"],  "odds": odds.get("over25"),  "min_ev": 4.0},
            {"type": "btts_yes", "p": mc["btts"], "odds": odds.get("btts_yes"), "min_ev": 5.0}
        ]
        
        best_ev, best_tip = -999, None
        for m in markets:
            if m["odds"] and m["odds"] > 1:
                ev = (m["p"] * m["odds"] - 1) * 100
                if ev > m["min_ev"] and ev > best_ev:
                    best_ev = ev
                    best_tip = {
                        "id": f["fixture_id"], "league": f["league_name"], 
                        "teams": f"{f['home_name']} - {f['away_name']}",
                        "market": m["type"], "odds": m["odds"], "p": m["p"], 
                        "ev": round(ev, 1), "sc": mc["score"], "time": f["start_time"]
                    }
        if best_tip: candidates.append(best_tip)
    
    top_tips = sorted(candidates, key=lambda x: x["ev"], reverse=True)[:max_tips]
    
    # Mentés a ROI jelentéshez
    history = {}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f: history = json.load(f)
    history[date_str] = top_tips
    with open(HISTORY_FILE, 'w') as f: json.dump(history, f)
    
    return top_tips

def send_premium_telegram(tips, date_str):
    if not tips: return
    msg = f"🏆 <b>ELITE AUTOMATA TIPPEK</b> 🏆\n📅 <i>{date_str}</i>\n───────────────────\n"
    icons = {"over25": "⚽️", "over15": "🥅", "btts_yes": "🔄"}
    for t in tips:
        t_short = t['time'].split('T')[1][:5] if 'T' in t['time'] else "??:??"
        market_label = "Over 2.5" if t['market'] == "over25" else "BTTS" if t['market'] == "btts_yes" else "Over 1.5"
        msg += f"⏰ <b>{t_short}</b> | {t['league']}\n⚔️ <b>{t['teams']}</b>\n"
        msg += f"{icons.get(t['market'], '👉')} Tipp: <b>{market_label}</b>\n"
        msg += f"📊 Odds: <b>{t['odds']}</b> | MC Esély: <b>{int(t['p']*100)}%</b>\n"
        msg += f"💎 EV: <b>{t['ev']}%</b> | 🎯 Tipp: <b>{t['sc']}</b>\n───────────────────\n"
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"})

# ========= FŐ PROGRAMCIKLUS =========

def main():
    # 1. Másnapi dátum beállítása és tegnapi kiértékelés
    evaluate_previous_day()
    tz = pytz.timezone("Europe/Budapest")
    target_date = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d')
    print(f"Szkennelés indul: {target_date}...")
    
    # 2. Meccsek lekérése a ligák alapján
    fixtures = get_fixtures_for_date(target_date)
    fixtures_out = []
    
    for fx in fixtures:
        fid = fx["fixture"]["id"]
        h_id, a_id = fx["teams"]["home"]["id"], fx["teams"]["away"]["id"]
        
        # Utolsó 10 meccs lekérése mindkét csapathoz
        h_matches = get_team_last_matches(h_id)
        a_matches = get_team_last_matches(a_id)
        
        h_stats = compute_basic_stats_from_matches(h_matches, h_id)
        a_stats = compute_basic_stats_from_matches(a_matches, a_id)
        
        if h_stats and a_stats:
            # Poisson lambda számítás (Saját lőtt + Ellenfél kapott átlag)
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

    # 3. JSON fájlok mentése és Supabase feltöltés
    out_file = f"foci_master_{target_date}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"date": target_date, "fixtures": fixtures_out}, f, indent=2, ensure_ascii=False)
    
    upload_to_supabase(out_file, target_date)
    
    # 4. Monte Carlo tippek generálása és prémium küldés
    elite_tips = generate_multi_market_tips_mc(fixtures_out, target_date)
    send_premium_telegram(elite_tips, target_date)
    print(f"Kész. Küldve {len(elite_tips)} tipp.")

if __name__ == "__main__":
    main()
