import os
import json
from datetime import datetime, timedelta, timezone
import requests
import numpy as np
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
        "o15": float(prob_o15), "o25": float(prob_o25),
        "btts": float(prob_btts), "score": predicted_score
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
                url = f"{BASE_URL}/fixtures?id={t['id']}"
                resp = requests.get(url, headers=HEADERS, timeout=15).json()
                r = resp.get("response", [])
                if r and r[0]['fixture']['status']['short'] == 'FT':
                    h, a = r[0]['goals']['home'], r[0]['goals']['away']
                    is_win = False
                    if t['market'] == 'over25' and (h+a) > 2.5: is_win = True
                    elif t['market'] == 'btts_yes' and (h or 0) > 0 and (a or 0) > 0: is_win = True
                    elif t['market'] == 'over15' and (h+a) > 1.5: is_win = True
                    total_spent += 1
                    icon = "✅" if is_win else "❌"
                    if is_win:
                        wins += 1
                        total_return += t['odds']
                    report += f"{icon} {t['teams']} ({h}-{a}) @{t['odds']}\n"
            except: continue
        if total_spent > 0:
            roi = ((total_return - total_spent) / total_spent * 100)
            report += f"───────────────────\n🎯 <b>Mérleg: {wins}/{total_spent}</b>\n💰 <b>ROI: {roi:+.1f}%</b>"
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": report, "parse_mode": "HTML"})
    except: pass
        # ========= EREDETI ADATGYŰJTŐ FÜGGVÉNYEK (VÁGATLAN) =========

def get_fixtures_for_date(date_str: str) -> List[Dict]:
    leagues = load_league_config()
    headers = {"x-apisports-key": API_KEY}
    all_fixtures = []
    for league_cfg in leagues:
        league_id = league_cfg["league_id"]
        # Season fixálva 2025-re az eredeti kódod alapján
        url = f"https://v3.football.api-sports.io/fixtures?league={league_id}&season=2025&date={date_str}"
        try:
            response = requests.get(url, headers=headers, timeout=20).json()
            all_fixtures.extend(response.get("response", []))
        except Exception as e:
            print(f"Hiba a fixture-ök lekérésekor (League: {league_id}): {e}")
            continue
    return all_fixtures


def get_team_last_matches(team_id: int, last_n: int = 10) -> List[Dict]:
    headers = {"x-apisports-key": API_KEY}
    url = f"https://v3.football.api-sports.io/fixtures?team={team_id}&last={last_n}"
    try:
        response = requests.get(url, headers=headers, timeout=15).json()
        return response.get("response", [])
    except Exception as e:
        print(f"Hiba a csapat utolsó meccseinek lekérésekor (Team: {team_id}): {e}")
        return []


def compute_basic_stats_from_matches(matches: List[Dict], team_id: int):
    if not matches:
        return None

    goals_scored = 0
    goals_conceded = 0
    over15_matches = 0
    over25_matches = 0
    btts_matches = 0

    for match in matches:
        home_id = match["teams"]["home"]["id"]
        # Ha None az érték, 0-nak vesszük
        home_goals = match["goals"]["home"] if match["goals"]["home"] is not None else 0
        away_goals = match["goals"]["away"] if match["goals"]["away"] is not None else 0

        if home_id == team_id:
            scored = home_goals
            conceded = away_goals
        else:
            scored = away_goals
            conceded = home_goals

        goals_scored += scored
        goals_conceded += conceded

        total_goals = home_goals + away_goals
        if total_goals > 1.5:
            over15_matches += 1
        if total_goals > 2.5:
            over25_matches += 1
        if home_goals > 0 and away_goals > 0:
            btts_matches += 1

    count = len(matches)
    return {
        "avg_scored": goals_scored / count,
        "avg_conceded": goals_conceded / count,
        "over15_rate": over15_matches / count,
        "over25_rate": over25_matches / count,
        "btts_rate": btts_matches / count,
    }


def get_odds_for_fixture(fixture_id: int) -> Dict[str, float]:
    headers = {"x-apisports-key": API_KEY}
    url = f"https://v3.football.api-sports.io/odds?fixture={fixture_id}"
    
    odds_out = {
        "over15": None,
        "over25": None,
        "btts_yes": None,
    }

    try:
        response = requests.get(url, headers=headers, timeout=15).json()
        for bookmaker_data in response.get("response", []):
            if bookmaker_data["bookmaker"]["name"] == "Bet365":
                for bet in bookmaker_data["bets"]:
                    if bet["name"] == "Goals Over/Under":
                        for val in bet["values"]:
                            if val["value"] == "Over 1.5":
                                odds_out["over15"] = float(val["odd"])
                            if val["value"] == "Over 2.5":
                                odds_out["over25"] = float(val["odd"])
                    if bet["name"] == "Both Teams Score":
                        for val in bet["values"]:
                            if val["value"] == "Yes":
                                odds_out["btts_yes"] = float(val["odd"])
    except Exception as e:
        print(f"Hiba az odds lekérésekor (Fixture: {fixture_id}): {e}")
        
    return odds_out


def upload_to_supabase(file_path: str, date_str: str, bucket_key: str = "foci-master"):
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Supabase credentials hiányoznak, feltöltés kihagyva.")
        return

    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        file_name = os.path.basename(file_path)
        remote_path = f"{date_str}/{file_name}"

        with open(file_path, "rb") as f:
            file_data = f.read()

        supabase.storage.from_(bucket_key).upload(
            path=remote_path,
            file=file_data,
            file_options={"content-type": "application/json", "x-upsert": "true"}
        )
        print(f"✅ Feltöltve Supabase-re: {bucket_key}/{remote_path}")
    except Exception as e:
        print(f"Supabase hiba feltöltés közben: {e}")
        # ========= PRÉMIUM KÜLDÉS ÉS TIPPGENERÁLÁS (MONTE CARLO) =========

def send_premium_telegram(tips: List[Dict], date_str: str):
    if not tips:
        print("Nincs küldhető tipp.")
        return
    
    msg = f"🏆 <b>ELITE AUTOMATA TIPPEK</b> 🏆\n📅 <i>{date_str}</i>\n"
    msg += "───────────────────\n"
    
    icons = {"over25": "⚽️", "over15": "🥅", "btts_yes": "🔄"}
    
    for t in tips:
        try:
            # ISO formátumból kinyerjük az időt (HH:MM)
            t_short = t['time'].split('T')[1][:5]
        except:
            t_short = "??:??"
            
        market_label = "Over 2.5" if t['market'] == "over25" else "BTTS" if t['market'] == "btts_yes" else "Over 1.5"
        
        msg += f"⏰ <b>{t_short}</b> | {t['league']}\n"
        msg += f"⚔️ <b>{t['teams']}</b>\n"
        msg += f"{icons.get(t['market'], '👉')} Tipp: <b>{market_label}</b>\n"
        msg += f"📊 Odds: <b>{t['odds']}</b> | MC Esély: <b>{int(t['p']*100)}%</b>\n"
        msg += f"💎 EV: <b>{t['ev']}%</b> | 🎯 Tipp: <b>{t['sc']}</b>\n"
        msg += "───────────────────\n"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
        print("✅ Telegram üzenet elküldve.")
    except Exception as e:
        print(f"Telegram hiba: {e}")


def generate_multi_market_tips_from_fixtures(fixtures: List[Dict], max_tips: int = 10, allowed_leagues: List[str] = None):
    candidates = []

    for f in fixtures:
        if allowed_leagues and f["league_name"] not in allowed_leagues:
            continue

        derived = f.get("derived_profile")
        if not derived:
            continue

        # --- MONTE CARLO SZIMULÁCIÓ FONTOS ADATOKKAL ---
        mc = run_monte_carlo(
            derived.get("home_expected_goals", 0), 
            derived.get("away_expected_goals", 0)
        )

        odds = f.get("odds", {})
        markets_to_check = [
            {"type": "over15",   "p": mc["o15"],  "odds": odds.get("over15"),  "min_ev": 2.5},
            {"type": "over25",   "p": mc["o25"],  "odds": odds.get("over25"),  "min_ev": 4.0},
            {"type": "btts_yes", "p": mc["btts"], "odds": odds.get("btts_yes"), "min_ev": 5.0},
        ]

        best_fixture_ev = -999.0
        best_fixture_tip = None

        for m in markets_to_check:
            m_odds = m["odds"]
            if m_odds and m_odds > 1.0:
                prob = m["p"]
                ev = (prob * m_odds - 1) * 100
                if ev > m["min_ev"] and ev > best_fixture_ev:
                    best_fixture_ev = ev
                    best_fixture_tip = {
                        "id": f["fixture_id"],
                        "league": f["league_name"],
                        "teams": f"{f['home_name']} - {f['away_name']}",
                        "market": m["type"],
                        "odds": m_odds,
                        "p": prob,
                        "ev": round(ev, 1),
                        "sc": mc["score"],
                        "time": f["start_time"]
                    }

        if best_fixture_tip:
            candidates.append(best_fixture_tip)

    # EV szerinti csökkenő sorrend
    candidates.sort(key=lambda x: x["ev"], reverse=True)
    top_tips = candidates[:max_tips]

    # Mentés a helyi HISTORY fájlba az automata másnapi ROI kiértékeléshez
    try:
        history = {}
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as hf:
                history = json.load(hf)
        
        # A dátumot a start_time-ból vesszük ki (YYYY-MM-DD)
        if top_tips:
            target_date = top_tips[0]['time'].split('T')[0]
            history[target_date] = top_tips
            with open(HISTORY_FILE, 'w', encoding='utf-8') as hf:
                json.dump(history, hf, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Hiba a history mentésekor: {e}")

    return top_tips


# ========= FŐ PROGRAMCIKLUS (MAIN) =========

def main():
    # 1. LÉPÉS: Előző napi eredmények kiértékelése (ROI jelentés)
    evaluate_previous_day()

    # 2. LÉPÉS: Új nap előkészítése (Holnap)
    tz = pytz.timezone("Europe/Budapest")
    now = datetime.now(tz)
    date_str = (now + timedelta(days=1)).strftime('%Y-%m-%d')
    output_file = f"foci_master_{date_str}.json"

    print(f"▶ Napi foci master build indul, dátum: {date_str}")
    
    # Meccsek lekérése az összes konfigurált ligából
    fixtures = get_fixtures_for_date(date_str)
    fixtures_out = []

    for fixture_data in fixtures:
        fixture_id = fixture_data["fixture"]["id"]
        home_id = fixture_data["teams"]["home"]["id"]
        away_id = fixture_data["teams"]["away"]["id"]

        # Csapatstatisztikák (utolsó 10 meccs)
        home_matches = get_team_last_matches(home_id)
        away_matches = get_team_last_matches(away_id)

        home_stats = compute_basic_stats_from_matches(home_matches, home_id)
        away_stats = compute_basic_stats_from_matches(away_matches, away_id)

        if home_stats and away_stats:
            # Poisson eloszlás várható értékeinek kiszámítása
            home_exp = (home_stats["avg_scored"] + away_stats["avg_conceded"]) / 2
            away_exp = (away_stats["avg_scored"] + home_stats["avg_conceded"]) / 2

            derived = {
                "home_expected_goals": home_exp,
                "away_expected_goals": away_exp,
            }

            # Oddsok lekérése (Bet365 fókusz)
            odds = get_odds_for_fixture(fixture_id)

            fixture_obj = {
                "fixture_id": fixture_id,
                "start_time": fixture_data["fixture"]["date"],
                "league_name": fixture_data["league"]["name"],
                "home_name": fixture_data["teams"]["home"]["name"],
                "away_name": fixture_data["teams"]["away"]["name"],
                "odds": odds,
                "derived_profile": derived,
            }
            fixtures_out.append(fixture_obj)

    # 3. LÉPÉS: Adatok mentése és Supabase feltöltés
    output = {
        "date": date_str,
        "fixtures": fixtures_out,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ Kész lokálisan: {output_file}, meccsek száma: {len(fixtures_out)}")
    upload_to_supabase(output_file, date_str, bucket_key="foci-master")

    # 4. LÉPÉS: Automata Elite tipplista generálás Monte Carlo szimulációval
    tips = generate_multi_market_tips_from_fixtures(fixtures_out, max_tips=10)

    # Tippfájl mentése és feltöltése
    tips_output_file = f"tips_{date_str}.json"
    tips_payload = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "max_tips": 10,
        "tips": tips,
    }
    
    with open(tips_output_file, "w", encoding="utf-8") as f:
        json.dump(tips_payload, f, ensure_ascii=False, indent=2)

    print(f"✅ Tippfájl kész: {tips_output_file}, tippek száma: {len(tips)}")
    upload_to_supabase(tips_output_file, date_str, bucket_key="foci-master")
    
    # 5. LÉPÉS: Értesítés Telegramon (Prémium formátum)
    send_premium_telegram(tips, date_str)


if __name__ == "__main__":
    main()
