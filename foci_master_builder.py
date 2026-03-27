import os
import json
from datetime import datetime, timedelta, timezone

import requests
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
        # "timezone": "Europe/Budapest",
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

        total_goals = goals_home + goals_away
        if total_goals >= 2:
            over15 += 1
        if total_goals >= 3:
            over25 += 1
        if goals_home > 0 and goals_away > 0:
            btts += 1

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


def simple_model_probabilities(home_stats, away_stats):
    def avg_or_none(a, b):
        vals = [v for v in [a, b] if v is not None]
        return sum(vals) / len(vals) if vals else None

    over15 = avg_or_none(home_stats["over15_rate"], away_stats["over15_rate"])
    over25 = avg_or_none(home_stats["over25_rate"], away_stats["over25_rate"])
    raw_btts = avg_or_none(home_stats["btts_rate"], away_stats["btts_rate"])

    # BTTS kalibráció: shrinkelés egy globális átlag felé (pl. 0.52)
    global_btts_avg = 0.52
    if raw_btts is not None:
        btts = 0.5 * raw_btts + 0.5 * global_btts_avg
    else:
        btts = None

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
    avoid_outright = False

    if model_probs["over15"] is not None and model_probs["over15"] >= 0.75:
        safe_over_candidate = True

    if profile in ("C", "D"):
        avoid_outright = True

    return {
        "match_profile": profile,
        "safe_over_candidate": safe_over_candidate,
        "avoid_outright": avoid_outright,
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

    with open(output_file, "r", encoding="utf-8") as f:
        data = f.read().encode("utf-8")

    try:
        res = supabase.storage.from_(bucket).upload(
            path=object_path,
            file=data,
            file_options={"cache-control": "3600", "upsert": "true"},
        )
        print(f"✅ Feltöltve Supabase-re: {bucket}/{object_path}")
        print(res)
    except Exception as e:
        print(f"❌ Supabase feltöltési hiba: {e}")


def generate_multi_market_tips_from_fixtures(
    fixtures: List[Dict[str, Any]],
    max_tips: int = 10,
    allowed_leagues: List[str] = None,
) -> List[Dict[str, Any]]:
    """
    Több piac (over15, over25, btts) tip generálása finomhangolt szűrőkkel.
    - over15: EV >= 2.5%, p 0.60–0.85, odds 1.35–1.80
    - over25: EV >= 4%,   p 0.48–0.68, odds 1.75–2.70
    - btts:   EV >= 5%,   p 0.48–0.68, odds 1.80–3.20
    Meccsenként max 1 tipp (a legmagasabb EV-jű).
    """

    raw_candidates: List[Dict[str, Any]] = []

    for fx in fixtures:
        league_name = fx.get("league")
        country = fx.get("country")
        if allowed_leagues is not None and league_name not in allowed_leagues:
            continue

        probs = fx.get("model_probabilities", {}) or {}
        odds = fx.get("odds", {}) or {}
        derived = fx.get("derived_profile", {}) or {}
        safe_flag = bool(derived.get("safe_over_candidate"))

        fixture_id = fx.get("fixture_id")
        kickoff = fx.get("kickoff")
        home = fx.get("home_team")
        away = fx.get("away_team")

        def maybe_add_tip(
            market: str,
            p: float,
            o: float,
            min_ev: float,
            min_p: float,
            max_p: float,
            min_odds: float,
            max_odds: float,
        ):
            if p is None or o is None:
                return
            try:
                o_f = float(o)
            except (ValueError, TypeError):
                return

            ev = o_f * p - 1.0
            if ev < min_ev:
                return
            if p < min_p or p > max_p:
                return
            if o_f < min_odds or o_f > max_odds:
                return

            raw_candidates.append({
                "fixture_id": fixture_id,
                "league": league_name,
                "country": country,
                "kickoff": kickoff,
                "home_team": home,
                "away_team": away,
                "market": market,
                "model_p": p,
                "odds": o_f,
                "ev": ev,
                "safe_over_candidate": safe_flag,
            })

        # Over 1.5
        p_o15 = probs.get("over15")
        o_o15 = odds.get("over15")
        maybe_add_tip(
            market="over15",
            p=p_o15,
            o=o_o15,
            min_ev=0.025,
            min_p=0.60,
            max_p=0.85,
            min_odds=1.35,
            max_odds=1.80,
        )

        # Over 2.5
        p_o25 = probs.get("over25")
        o_o25 = odds.get("over25")
        maybe_add_tip(
            market="over25",
            p=p_o25,
            o=o_o25,
            min_ev=0.04,
            min_p=0.48,
            max_p=0.68,
            min_odds=1.75,
            max_odds=2.70,
        )

        # BTTS Yes
        p_btts = probs.get("btts")
        o_btts = odds.get("btts")
        maybe_add_tip(
            market="btts_yes",
            p=p_btts,
            o=o_btts,
            min_ev=0.05,
            min_p=0.48,
            max_p=0.68,
            min_odds=1.80,
            max_odds=3.20,
        )

    # Meccsenként max 1 tipp: válasszuk a legjobb EV-t
    best_per_fixture: Dict[Any, Dict[str, Any]] = {}
    for cand in raw_candidates:
        fid = cand["fixture_id"]
        if fid not in best_per_fixture:
            best_per_fixture[fid] = cand
        else:
            if cand["ev"] > best_per_fixture[fid]["ev"]:
                best_per_fixture[fid] = cand

    deduped_candidates = list(best_per_fixture.values())

    # Rendezés: safe_over_candidate előnyben, majd EV szerint csökkenő
    deduped_candidates.sort(
        key=lambda x: (x["safe_over_candidate"], x["ev"]),
        reverse=True,
    )

    return deduped_candidates[:max_tips]


def send_telegram_message_with_json(
    token: str,
    chat_id: str,
    tips_payload: Dict[str, Any],
):
    if not token or not chat_id:
        print("⚠️ TELEGRAM_BOT_TOKEN vagy TELEGRAM_CHAT_ID hiányzik, nem küldök üzenetet.")
        return

    tips = tips_payload.get("tips", [])
    date_str = tips_payload.get("date")
    header = f"📊 Foci automata tippek – {date_str}\nÖsszes tipp: {len(tips)}\n\n"

    lines = []
    for t in tips:
        kickoff = t.get("kickoff")
        league = t.get("league")
        home = t.get("home_team")
        away = t.get("away_team")
        market = t.get("market")
        odds = t.get("odds")
        ev = t.get("ev")
        p = t.get("model_p")
        line = (
            f"{kickoff} | {league}\n"
            f"{home} – {away}\n"
            f"Piac: {market}, odds: {odds:.2f}, p: {p:.2f}, EV: {ev*100:.1f}%\n"
        )
        lines.append(line)

    text = header + "\n".join(lines) if lines else header + "Nincs mai tipp a szűrők alapján."

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            print("✅ Telegram üzenet elküldve.")
        else:
            print(f"❌ Telegram hiba: {resp.status_code} – {resp.text}")
    except Exception as e:
        print(f"❌ Telegram küldési kivétel: {e}")


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

    team_stats_cache: Dict[int, Dict[str, Any]] = {}
    fixtures_out: List[Dict[str, Any]] = []

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

        if home_id not in team_stats_cache:
            home_matches = fetch_team_last_matches(api_key, base_url, home_id, last_n=10)
            team_stats_cache[home_id] = compute_basic_stats_from_matches(home_matches, home_id)
        home_stats = team_stats_cache[home_id]

        if away_id not in team_stats_cache:
            away_matches = fetch_team_last_matches(api_key, base_url, away_id, last_n=10)
            team_stats_cache[away_id] = compute_basic_stats_from_matches(away_matches, away_id)
        away_stats = team_stats_cache[away_id]

        model_probs = simple_model_probabilities(home_stats, away_stats)
        odds = fetch_odds_for_fixture(api_key, base_url, fixture_id)
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
                "avg_corners_per_match": home_stats["avg_corners"],
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

    print(f"✅ Kész lokálisan: {output_file}, meccsek száma: {len(fixtures_out)}")

    upload_to_supabase(output_file, date_str, bucket_key="foci-master")

    # --- AUTOMATA TIPPLISTA GENERÁLÁS ---
    allowed_leagues = None  # ha akarsz, itt szűrj liganévre listával
    tips = generate_multi_market_tips_from_fixtures(
        fixtures_out,
        max_tips=10,
        allowed_leagues=allowed_leagues,
    )

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

    upload_to_supabase(tips_output_file, date_str, bucket_key="foci-tips")

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    send_telegram_message_with_json(
        token=telegram_token,
        chat_id=telegram_chat_id,
        tips_payload=tips_payload,
    )


if __name__ == "__main__":
    main()
