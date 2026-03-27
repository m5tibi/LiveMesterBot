import os
import json
from datetime import datetime, timedelta, timezone

import requests
import numpy as np
from supabase import create_client, Client  # Supabase client
from typing import List, Dict, Any


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
        {"country": "Turkey",    "league_id": 203},
        {"country": "Portugal",  "league_id": 94},
        {"country": "Belgium",   "league_id": 144},
        {"country": "Switzerland","league_id": 207},
        {"country": "Norway",    "league_id": 103},
        {"country": "Sweden",    "league_id": 67},
    ]


def api_get(path, params, api_key, base_url):
    headers = {"x-apisports-key": api_key}
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    resp = requests.get(url, headers=headers, params=params, timeout=25)
    resp.raise_for_status()
    data = resp.json()
    return data.get("response", [])


def get_tomorrow_date_str():
    today_utc = datetime.now(timezone.utc).date()
    tomorrow = today_utc + timedelta(days=1)
    return tomorrow.strftime("%Y-%m-%d")


def fetch_fixtures_for_date(api_key, base_url, leagues, date_str):
    params = {
        "date": date_str,
    }
    fixtures = api_get("/fixtures", params, api_key, base_url)
    return fixtures


def fetch_team_last_matches(api_key, base_url, team_id, last_n=10):
    params = {"team": team_id, "last": last_n}
    resp = api_get("/fixtures", params, api_key, base_url)
    return resp


def compute_basic_stats_from_matches(matches, team_id):
    if not matches:
        return {
            "goals_for_per_match": 0.0,
            "goals_against_per_match": 0.0,
            "over15_rate": 0.0,
            "over25_rate": 0.0,
            "btts_rate": 0.0,
            "xg_for_per_match": None,
            "xg_against_per_match": None,
            "avg_corners": None,
        }

    total_for = 0
    total_against = 0
    over15 = 0
    over25 = 0
    btts = 0
    total_corners = 0
    corners_count = 0
    n = 0

    for m in matches:
        goals_home = m["goals"]["home"]
        goals_away = m["goals"]["away"]
        if goals_home is None or goals_away is None:
            continue

        home_id = m["teams"]["home"]["id"]
        away_id = m["teams"]["away"]["id"]

        if team_id == home_id:
            g_for = goals_home
            g_against = goals_away
        elif team_id == away_id:
            g_for = goals_away
            g_against = goals_home
        else:
            g_for = 0
            g_against = 0

        total_for += g_for
        total_against += g_against
        n += 1

        total_goals = (goals_home or 0) + (goals_away or 0)
        if total_goals >= 2:
            over15 += 1
        if total_goals >= 3:
            over25 += 1
        if (goals_home or 0) > 0 and (goals_away or 0) > 0:
            btts += 1

    return {
        "goals_for_per_match": total_for / n if n else 0.0,
        "goals_against_per_match": total_against / n if n else 0.0,
        "over15_rate": over15 / n if n else 0.0,
        "over25_rate": over25 / n if n else 0.0,
        "btts_rate": btts / n if n else 0.0,
        "xg_for_per_match": None,
        "xg_against_per_match": None,
        "avg_corners": (total_corners / corners_count) if corners_count else None,
    }


def fetch_odds_for_fixture(api_key, base_url, fixture_id):
    params = {
        "fixture": fixture_id,
        "bookmaker": 8  # Bet365
    }
    resp = api_get("/odds", params, api_key, base_url)

    odds_out = {
        "over15": None,
        "over25": None,
        "btts": None,
        "home_team_over15_goals": None,
        "away_team_over15_goals": None,
        "double_chance_1x": None,
        "double_chance_x2": None,
        "home_dnb": None,
        "away_dnb": None,
        "combo_1x_over15": None,
        "combo_x2_over15": None,
    }

    for item in resp:
        for bookmaker in item.get("bookmakers", []):
            for bet in bookmaker.get("bets", []):
                bet_name = (bet.get("name") or "").lower()

                for val in bet.get("values", []):
                    raw_value = val.get("value")
                    value = str(raw_value).lower() if raw_value is not None else ""

                    odd = None
                    if val.get("odd") is not None:
                        try:
                            odd = float(val["odd"])
                        except (ValueError, TypeError):
                            odd = None
                    if odd is None:
                        continue

                    if "total" in bet_name or "goals" in bet_name:
                        if value in ("over 1.5", "o 1.5") and odds_out["over15"] is None:
                            odds_out["over15"] = odd
                        if value in ("over 2.5", "o 2.5") and odds_out["over25"] is None:
                            odds_out["over25"] = odd

                    if "both teams to score" in bet_name and value in ("yes", "y"):
                        if odds_out["btts"] is None:
                            odds_out["btts"] = odd

    return odds_out


def run_monte_carlo_simulation(home_expectancy, away_expectancy, simulations=10000):
    """Poisson-eloszlás alapú Monte Carlo szimuláció a valószínűségek pontosításához."""
    h_goals = np.random.poisson(max(0.1, home_expectancy), simulations)
    a_goals = np.random.poisson(max(0.1, away_expectancy), simulations)
    total_goals = h_goals + a_goals

    return {
        "mc_over15": float(np.mean(total_goals > 1.5)),
        "mc_over25": float(np.mean(total_goals > 2.5)),
        "mc_btts": float(np.mean((h_goals > 0) & (a_goals > 0))),
    }


def simple_model_probabilities(home_stats, away_stats):
    def avg_or_none(a, b):
        vals = [v for v in [a, b] if v is not None]
        return sum(vals) / len(vals) if vals else None

    h_lambda = home_stats["goals_for_per_match"] or 0.0
    a_lambda = away_stats["goals_for_per_match"] or 0.0
    
    mc_results = run_monte_carlo_simulation(h_lambda, a_lambda)

    # Hibrid valószínűség számítás: 40% múltbeli statisztika, 60% Monte Carlo szimuláció.
    def hybrid_prob(hist_rate, mc_rate):
        if hist_rate is None: return mc_rate
        return (hist_rate * 0.4) + (mc_rate * 0.6)

    over15 = hybrid_prob(avg_or_none(home_stats["over15_rate"], away_stats["over15_rate"]), mc_results["mc_over15"])
    over25 = hybrid_prob(avg_or_none(home_stats["over25_rate"], away_stats["over25_rate"]), mc_results["mc_over25"])
    
    raw_btts = avg_or_none(home_stats["btts_rate"], away_stats["btts_rate"])
    global_btts_avg = 0.52
    if raw_btts is not None:
        btts_base = 0.5 * raw_btts + 0.5 * global_btts_avg
    else:
        btts_base = global_btts_avg
    btts = hybrid_prob(btts_base, mc_results["mc_btts"])

    def goal_prob_from_avg(avg):
        if avg is None: return None
        if avg >= 2.0: return 0.7
        if avg >= 1.5: return 0.6
        if avg >= 1.0: return 0.5
        return 0.4

    home_gprob = goal_prob_from_avg(home_stats["goals_for_per_match"])
    away_gprob = goal_prob_from_avg(away_stats["goals_for_per_match"])

    return {
        "over15": over15,
        "over25": over25,
        "btts": btts,
        "home_team_over15_goals": home_gprob,
        "away_team_over15_goals": away_gprob,
    }


def derive_profile(home_stats, away_stats, model_probs):
    hf = home_stats["goals_for_per_match"] or 0
    ha = home_stats["goals_against_per_match"] or 0
    af = away_stats["goals_for_per_match"] or 0
    aa = away_stats["goals_against_per_match"] or 0

    avg_goals_for = (hf + af) / 2
    avg_goals_against = (ha + aa) / 2

    if avg_goals_for >= 2.0 and avg_goals_against >= 1.5:
        profile = "B"
    elif avg_goals_for >= 2.0 and avg_goals_against <= 1.0:
        profile = "A"
    elif avg_goals_for <= 1.2 and avg_goals_against <= 1.2:
        profile = "C"
    else:
        profile = "D"

    safe_over_candidate = False
    if model_probs["over15"] is not None and model_probs["over15"] >= 0.75:
        safe_over_candidate = True

    return {
        "match_profile": profile,
        "safe_over_candidate": safe_over_candidate,
        "avoid_outright": profile in ("C", "D"),
    }


def upload_to_supabase(output_file, date_str, bucket_key="foci-master"):
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    bucket = os.environ.get("FOCI_MASTER_BUCKET", bucket_key)

    if not url or not key:
        print("⚠️ Supabase URL vagy KEY hiányzik, nem töltök fel.")
        return

    supabase: Client = create_client(url, key)
    object_path = f"{date_str}/{os.path.basename(output_file)}"

    with open(output_file, "rb") as f:
        data = f.read()

    try:
        supabase.storage.from_(bucket).upload(
            path=object_path,
            file=data,
            file_options={"cache-control": "3600", "upsert": "true"},
        )
        print(f"✅ Feltöltve Supabase-re: {bucket}/{object_path}")
    except Exception as e:
        print(f"❌ Supabase feltöltési hiba: {e}")


def generate_multi_market_tips_from_fixtures(
    fixtures: List[Dict[str, Any]],
    max_tips: int = 10,
    allowed_leagues: List[str] = None,
) -> List[Dict[str, Any]]:
    raw_candidates: List[Dict[str, Any]] = []

    for fx in fixtures:
        league_name = fx.get("league")
        if allowed_leagues is not None and league_name not in allowed_leagues:
            continue

        probs = fx.get("model_probabilities", {}) or {}
        odds = fx.get("odds", {}) or {}
        derived = fx.get("derived_profile", {}) or {}
        safe_flag = bool(derived.get("safe_over_candidate"))

        def maybe_add_tip(market, p, o, min_ev, min_p, max_p, min_odds, max_odds):
            if p is None or o is None: return
            ev = (p * o) - 1.0
            if ev >= min_ev and min_p <= p <= max_p and min_odds <= o <= max_odds:
                raw_candidates.append({**fx, "market": market, "model_p": p, "odds": o, "ev": ev, "safe_over_candidate": safe_flag})

        maybe_add_tip("over15", probs.get("over15"), odds.get("over15"), 0.025, 0.60, 0.85, 1.35, 1.80)
        maybe_add_tip("over25", probs.get("over25"), odds.get("over25"), 0.04, 0.48, 0.68, 1.75, 2.70)
        maybe_add_tip("btts_yes", probs.get("btts"), odds.get("btts"), 0.05, 0.48, 0.68, 1.80, 3.20)

    best_per_fixture: Dict[Any, Dict[str, Any]] = {}
    for cand in raw_candidates:
        fid = cand["fixture_id"]
        if fid not in best_per_fixture or cand["ev"] > best_per_fixture[fid]["ev"]:
            best_per_fixture[fid] = cand

    deduped = list(best_per_fixture.values())
    deduped.sort(key=lambda x: (x["safe_over_candidate"], x["ev"]), reverse=True)
    return deduped[:max_tips]


def send_telegram_message_with_json(token, chat_id, tips_payload):
    """Telegram üzenet küldése szebb formázással és magyar nyelvű tippekkel."""
    if not token or not chat_id:
        print("⚠️ Telegram token vagy chat_id hiányzik.")
        return

    tips = tips_payload.get("tips", [])
    date_str = tips_payload.get("date")
    
    header = f"📊 <b>Foci Automata Tippek – {date_str}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    lines = []
    for t in tips:
        emoji = "🔥" if t.get("safe_over_candidate") else "⚽"
        time_str = t.get("kickoff")[11:16] if t.get("kickoff") else "--:--"
        
        # Piac nevének magyarítása a kérésed szerint.
        market_name = (t.get("market") or "").upper()
        if market_name == "OVER15":
            market_display = "Gólszám 1,5 felett"
        elif market_name == "OVER25":
            market_display = "Gólszám 2,5 felett"
        elif market_name == "BTTS_YES":
            market_display = "Mindkét csapat szerez gólt: IGEN"
        else:
            market_display = market_name

        lines.append(
            f"{emoji} <b>{t.get('home_team')} – {t.get('away_team')}</b>\n"
            f"🏆 {t.get('league')} | ⏰ {time_str}\n"
            f"🎯 Tipp: <code>{market_display}</code>\n"
            f"📈 P: {t.get('model_p', 0)*100:.1f}% | EV: {t.get('ev', 0)*100:.1f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
    
    text = header + "\n".join(lines) if lines else header + "<i>Nincs mai tipp a szűrők alapján.</i>"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=15)
        print("✅ Telegram üzenet elküldve.")
    except Exception as e:
        print(f"❌ Telegram hiba: {e}")


def main():
    api_key = os.environ.get("API_FOOTBALL_KEY")
    base_url = os.environ.get("API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io")
    output_file = os.environ.get("FOCI_MASTER_OUTPUT_FILE", "foci_master_today.json")

    if not api_key:
        raise RuntimeError("Hiányzik az API_FOOTBALL_KEY env változó.")

    leagues_cfg = load_league_config()
    allowed_leagues_ids = [l["league_id"] for l in leagues_cfg]
    date_str = get_tomorrow_date_str()
    print(f"▶ Napi foci master build indul, dátum: {date_str}")

    fixtures_raw = fetch_fixtures_for_date(api_key, base_url, leagues_cfg, date_str)
    team_stats_cache: Dict[int, Dict[str, Any]] = {}
    fixtures_out: List[Dict[str, Any]] = []

    for fx in fixtures_raw:
        if fx["league"]["id"] not in allowed_leagues_ids:
            continue
            
        fixture = fx["fixture"]
        league = fx["league"]
        teams = fx["teams"]
        home_id, away_id = teams["home"]["id"], teams["away"]["id"]

        if home_id not in team_stats_cache:
            home_matches = fetch_team_last_matches(api_key, base_url, home_id)
            team_stats_cache[home_id] = compute_basic_stats_from_matches(home_matches, home_id)
        
        if away_id not in team_stats_cache:
            away_matches = fetch_team_last_matches(api_key, base_url, away_id)
            team_stats_cache[away_id] = compute_basic_stats_from_matches(away_matches, away_id)

        model_probs = simple_model_probabilities(team_stats_cache[home_id], team_stats_cache[away_id])
        odds = fetch_odds_for_fixture(api_key, base_url, fixture["id"])
        derived = derive_profile(team_stats_cache[home_id], team_stats_cache[away_id], model_probs)

        fixtures_out.append({
            "fixture_id": fixture["id"],
            "league": league["name"],
            "country": league["country"],
            "kickoff": fixture["date"],
            "home_team": teams["home"]["name"],
            "away_team": teams["away"]["name"],
            "stats": {
                "home_last10_goals_for_per_match": team_stats_cache[home_id]["goals_for_per_match"],
                "home_last10_goals_against_per_match": team_stats_cache[home_id]["goals_against_per_match"],
                "away_last10_goals_for_per_match": team_stats_cache[away_id]["goals_for_per_match"],
                "away_last10_goals_against_per_match": team_stats_cache[away_id]["goals_against_per_match"],
                "home_last10_over15_rate": team_stats_cache[home_id]["over15_rate"],
                "home_last10_over25_rate": team_stats_cache[home_id]["over25_rate"],
                "home_last10_btts_rate": team_stats_cache[home_id]["btts_rate"],
                "away_last10_over15_rate": team_stats_cache[away_id]["over15_rate"],
                "away_last10_over25_rate": team_stats_cache[away_id]["over25_rate"],
                "away_last10_btts_rate": team_stats_cache[away_id]["btts_rate"],
            },
            "model_probabilities": model_probs,
            "odds": odds,
            "derived_profile": derived,
        })

    output = {"date": date_str, "fixtures": fixtures_out}
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ Lokális mentés kész: {output_file} ({len(fixtures_out)} meccs)")
    upload_to_supabase(output_file, date_str, bucket_key="foci-master")

    tips = generate_multi_market_tips_from_fixtures(fixtures_out)
    tips_payload = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tips": tips
    }
    
    tips_file = f"tips_{date_str}.json"
    with open(tips_file, "w", encoding="utf-8") as f:
        json.dump(tips_payload, f, ensure_ascii=False, indent=2)

    upload_to_supabase(tips_file, date_str, bucket_key="foci-tips")
    send_telegram_message_with_json(os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID"), tips_payload)


if __name__ == "__main__":
    main()
