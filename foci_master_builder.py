import os
import json
from datetime import datetime, timedelta, timezone

import requests


def load_league_config():
    """
    Ligák listája JSON-ből vagy env változóból.
    FOCI_MASTER_LEAGUES formátum (env-ben):
    [
      {"country": "England", "league_id": 39},
      {"country": "Netherlands", "league_id": 88}
    ]
    """
    env_val = os.environ.get("FOCI_MASTER_LEAGUES")
    if env_val:
        try:
            return json.loads(env_val)
        except Exception:
            pass

    # Alap default, ha nincs beállítva env-ben
    return [
        {"country": "England", "league_id": 39},   # Premier League
        {"country": "Germany", "league_id": 78},   # Bundesliga
        {"country": "Netherlands", "league_id": 88},  # Eredivisie
        {"country": "Austria", "league_id": 218},  # Austrian Bundesliga
    ]


def api_get(path, params, api_key, base_url):
    headers = {
        "x-apisports-key": api_key
    }
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    resp = requests.get(url, headers=headers, params=params, timeout=25)
    resp.raise_for_status()
    data = resp.json()
    return data.get("response", [])


def get_tomorrow_date_str():
    # UTC-ben dolgozunk (Render is ezt használja).
    today_utc = datetime.now(timezone.utc).date()
    tomorrow = today_utc + timedelta(days=1)
    return tomorrow.strftime("%Y-%m-%d")


def fetch_fixtures_for_date(api_key, base_url, leagues, date_str):
    fixtures = []
    for league in leagues:
        league_id = league["league_id"]
        params = {
            "date": date_str,
            "league": league_id,
            "season": datetime.now().year  # ha kell, átírhatod fixre
        }
        resp = api_get("/fixtures", params, api_key, base_url)
        fixtures.extend(resp)
    return fixtures


def fetch_team_last_matches(api_key, base_url, team_id, last_n=10):
    params = {
        "team": team_id,
        "last": last_n
    }
    resp = api_get("/fixtures", params, api_key, base_url)
    return resp


def compute_basic_stats_from_matches(matches, team_id):
    """
    Nagyon egyszerű, de stabil stat-aggregátor.
    Külön számoljuk a 'for' és 'against' gólokat a team_id alapján.
    """
    if not matches:
        return {
            "goals_for_per_match": None,
            "goals_against_per_match": None,
            "over15_rate": None,
            "over25_rate": None,
            "btts_rate": None,
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
            # elvileg nem kéne ide jutni
            g_for = 0
            g_against = 0

        total_for += g_for
        total_against += g_against

        total_goals = goals_home + goals_away
        if total_goals >= 2:
            over15 += 1
        if total_goals >= 3:
            over25 += 1
        if goals_home > 0 and goals_away > 0:
            btts += 1

        # Ha használsz corners/statistics endpointot, itt kell kiegészíteni.
        # Most placeholder (None).
        # total_corners += ...
        # corners_count += 1

    n = len(matches)
    return {
        "goals_for_per_match": total_for / n if n else None,
        "goals_against_per_match": total_against / n if n else None,
        "over15_rate": over15 / n if n else None,
        "over25_rate": over25 / n if n else None,
        "btts_rate": btts / n if n else None,
        "xg_for_per_match": None,
        "xg_against_per_match": None,
        "avg_corners": (total_corners / corners_count) if corners_count else None,
    }


def fetch_odds_for_fixture(api_key, base_url, fixture_id):
    params = {
        "fixture": fixture_id,
        "bookmaker": 8  # pl. Bet365 – pontosítsd docs alapján
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

    # api-football odds struktúra: league->fixture->bookmakers->bets->values
    for item in resp:
        for bookmaker in item.get("bookmakers", []):
            for bet in bookmaker.get("bets", []):
                bet_name = (bet.get("name") or "").lower()
                for val in bet.get("values", []):
                    value = (val.get("value") or "").lower()
                    odd = None
                    if val.get("odd") is not None:
                        try:
                            odd = float(val["odd"])
                        except ValueError:
                            pass

                    if odd is None:
                        continue

                    # Összgól piacok
                    if bet_name == "total goals":
                        if value == "over 1.5" and odds_out["over15"] is None:
                            odds_out["over15"] = odd
                        if value == "over 2.5" and odds_out["over25"] is None:
                            odds_out["over25"] = odd

                    # BTTS
                    if bet_name == "both teams to score" and value == "yes" and odds_out["btts"] is None:
                        odds_out["btts"] = odd

                    # Itt bővíthető: team_goals, double chance, DNB, combo, ha a docs szerint be tudjuk azonosítani.

    return odds_out


def simple_model_probabilities(home_stats, away_stats):
    """
    Egyszerű modell: a múltbeli arányok átlagából becsült P-k.
    Ez csak alap – a Safe Over / Biztonsági Index logikát én fogom ráépíteni elemzéskor.
    """
    def avg_or_none(a, b):
        vals = [v for v in [a, b] if v is not None]
        return sum(vals) / len(vals) if vals else None

    over15 = avg_or_none(home_stats["over15_rate"], away_stats["over15_rate"])
    over25 = avg_or_none(home_stats["over25_rate"], away_stats["over25_rate"])
    btts = avg_or_none(home_stats["btts_rate"], away_stats["btts_rate"])

    def goal_prob_from_avg(avg):
        if avg is None:
            return None
        if avg >= 2.0:
            return 0.7
        if avg >= 1.5:
            return 0.6
        if avg >= 1.0:
            return 0.5
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
    """
    A/B/C/D profil + safe_over_candidate + avoid_outright flag.
    Ezeket a Prompt-2/3/6 logikájára hangoltam.[file:3][file:4][file:6]
    """
    hf = home_stats["goals_for_per_match"] or 0
    ha = home_stats["goals_against_per_match"] or 0
    af = away_stats["goals_for_per_match"] or 0
    aa = away_stats["goals_against_per_match"] or 0

    avg_goals_for = (hf + af) / 2
    avg_goals_against = (ha + aa) / 2

    # Profil besorolás – később finomíthatjuk.
    if avg_goals_for >= 2.0 and avg_goals_against >= 1.5:
        profile = "B"  # kaotikus, gólgazdag
    elif avg_goals_for >= 2.0 and avg_goals_against <= 1.0:
        profile = "A"  # domináns támadó
    elif avg_goals_for <= 1.2 and avg_goals_against <= 1.2:
        profile = "C"  # taktikai, kevés gól
    else:
        profile = "D"  # vegyes / bizonytalan / derby

    safe_over_candidate = False
    avoid_outright = False

    # Safe Over jelölt – Prompt-6: min. 75–80% over, erős támadás, gyenge védelem.[file:6]
    if model_probs["over15"] is not None and model_probs["over15"] >= 0.75:
        safe_over_candidate = True

    # C/D profilon kerüljük a sima 1X2-t, inkább gl-piac vagy teljes tiltás.[file:3][file:4]
    if profile in ("C", "D"):
        avoid_outright = True

    return {
        "match_profile": profile,
        "safe_over_candidate": safe_over_candidate,
        "avoid_outright": avoid_outright,
    }


def main():
    api_key = os.environ.get("API_FOOTBALL_KEY")
    base_url = os.environ.get("API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io")
    output_file = os.environ.get("FOCI_MASTER_OUTPUT_FILE", "foci_master_today.json")

    if not api_key:
        raise RuntimeError("Hiányzik az API_FOOTBALL_KEY env változó.")

    leagues = load_league_config()
    date_str = get_tomorrow_date_str()
    print(f"▶ Napi foci master build indul, dátum: {date_str}")

    fixtures_raw = fetch_fixtures_for_date(api_key, base_url, leagues, date_str)

    team_stats_cache = {}
    fixtures_out = []

    for fx in fixtures_raw:
        fixture = fx["fixture"]
        league = fx["league"]
        teams = fx["teams"]

        fixture_id = fixture["id"]
        kickoff_iso = fixture["date"]
        home_id = teams["home"]["id"]
        away_id = teams["away"]["id"]
        home_name = teams["home"]["name"]
        away_name = teams["away"]["name"]

        # Home stats
        if home_id not in team_stats_cache:
            home_matches = fetch_team_last_matches(api_key, base_url, home_id, last_n=10)
            team_stats_cache[home_id] = compute_basic_stats_from_matches(home_matches, home_id)
        home_stats = team_stats_cache[home_id]

        # Away stats
        if away_id not in team_stats_cache:
            away_matches = fetch_team_last_matches(api_key, base_url, away_id, last_n=10)
            team_stats_cache[away_id] = compute_basic_stats_from_matches(away_matches, away_id)
        away_stats = team_stats_cache[away_id]

        # Modell valószínűségek
        model_probs = simple_model_probabilities(home_stats, away_stats)

        # Odds
        odds = fetch_odds_for_fixture(api_key, base_url, fixture_id)

        # Profil
        derived = derive_profile(home_stats, away_stats, model_probs)

        fixture_obj = {
            "fixture_id": fixture_id,
            "league": league["name"],
            "country": league["country"],
            "kickoff": kickoff_iso,
            "home_team": home_name,
            "away_team": away_name,
            "stats": {
                "home_last10_goals_for_per_match": home_stats["goals_for_per_match"],
                "home_last10_goals_against_per_match": home_stats["goals_against_per_match"],
                "away_last10_goals_for_per_match": away_stats["goals_for_per_match"],
                "away_last10_goals_against_per_match": away_stats["goals_against_per_match"],
                "home_last10_over15_rate": home_stats["over15_rate"],
                "home_last10_over25_rate": home_stats["over25_rate"],
                "home_last10_btts_rate": home_stats["btts_rate"],
                "away_last10_over15_rate": away_stats["over15_rate"],
                "away_last10_over25_rate": away_stats["over25_rate"],
                "away_last10_btts_rate": away_stats["btts_rate"],
                "home_last10_xg_for_per_match": home_stats["xg_for_per_match"],
                "home_last10_xg_against_per_match": home_stats["xg_against_per_match"],
                "away_last10_xg_for_per_match": away_stats["xg_for_per_match"],
                "away_last10_xg_against_per_match": away_stats["xg_against_per_match"],
                "avg_corners_per_match": home_stats["avg_corners"],  # egyszerűsítés
            },
            "model_probabilities": model_probs,
            "odds": odds,
            "derived_profile": derived,
        }

        fixtures_out.append(fixture_obj)

    output = {
        "date": date_str,
        "fixtures": fixtures_out,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ Kész: {output_file}, meccsek száma: {len(fixtures_out)}")


if __name__ == "__main__":
    main()
