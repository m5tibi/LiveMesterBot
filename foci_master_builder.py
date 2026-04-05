import os
import json
from datetime import datetime, timedelta, timezone
from math import exp, factorial

import requests
import numpy as np
from supabase import create_client, Client
from typing import List, Dict, Any, Optional

# =========================================================
# GLOBÁLIS KONSTANSOK
# =========================================================
# Európai top ligák átlagos gólszáma (2023/24 szezon összesítő)
# Forrás: fbref.com / statsbomb nyilvános adatok
GLOBAL_AVG_GOALS = 2.65

# Hazai pálya előny szorzó (meta-analízis: ~12% gól-többlet)
HOME_ADVANTAGE = 1.12

# =========================================================
# EV SZŰRŐ KÜSZÖBÖK — piaconként
# =========================================================
# Magyarázat:
#   min_ev:  minimális elvárt Expected Value (pl. 0.04 = 4% EV)
#   min_p:   modell valószínűség alsó határa
#   max_p:   modell valószínűség felső határa
#             (max_p=0.92 → a 92%+ esélyek is bekerülnek, nem dobjuk ki)
#   min_o:   odds alsó határa (nagyon alacsony odds = nem éri meg)
#   max_o:   odds felső határa (nagyon magas odds = túl kockázatos)
#
# Profil-szűrők:
#   blocked_profiles: ezeken a meccs-profilokon NEM adjuk a tippet
#     'C' = zárt/defenzív meccs  → over25 és btts NEM ajánlott
#     'D' = vegyes                → over25 óvatosan, btts tiltva

MARKET_CONFIG = {
    "over15": {
        "min_ev":          0.04,   # volt: 0.025 → emelve, kevesebb "szemét" tipp
        "min_p":           0.62,   # volt: 0.60
        "max_p":           0.92,   # volt: 0.85 → a nagyon biztos esélyek sem esnek ki
        "min_o":           1.30,   # volt: 1.35 → kicsit tágabb odds-sáv
        "max_o":           1.85,   # volt: 1.80
        "blocked_profiles": ["C"], # zárt meccsen ne ajánljunk Over 1.5-öt sem
    },
    "over25": {
        "min_ev":          0.05,   # volt: 0.04
        "min_p":           0.50,   # volt: 0.48
        "max_p":           0.72,   # volt: 0.68 → tágabb felső határ
        "min_o":           1.70,   # volt: 1.75 → kissé tágabb
        "max_o":           2.80,   # volt: 2.70
        "blocked_profiles": ["C", "D"],  # zárt és vegyes meccsen tiltva
    },
    "btts_yes": {
        "min_ev":          0.06,   # volt: 0.05
        "min_p":           0.50,   # volt: 0.48
        "max_p":           0.72,   # volt: 0.68
        "min_o":           1.75,   # volt: 1.80 → kissé tágabb
        "max_o":           3.20,   # változatlan
        "blocked_profiles": ["C", "D"],  # defenzív/vegyes meccsen tiltva
    },
}

# Hány tipp engedélyezett ugyanarról a meccsről?
# Ha 1: csak a legjobb EV marad (régi viselkedés)
# Ha 2: pl. over15 ÉS over25 is bekerülhet, ha mindkettő erős
MAX_TIPS_PER_FIXTURE = 2


# =========================================================
# LIGA KONFIGURÁCIÓ
# =========================================================
def load_league_config():
    env_val = os.environ.get("FOCI_MASTER_LEAGUES")
    if env_val:
        try:
            return json.loads(env_val)
        except Exception:
            pass
    return [
        {"country": "England",      "league_id": 39},
        {"country": "England",      "league_id": 40},
        {"country": "England",      "league_id": 41},
        {"country": "England",      "league_id": 42},
        {"country": "Germany",      "league_id": 78},
        {"country": "Germany",      "league_id": 79},
        {"country": "Netherlands",  "league_id": 88},
        {"country": "Netherlands",  "league_id": 89},
        {"country": "Austria",      "league_id": 218},
        {"country": "Scotland",     "league_id": 179},
        {"country": "Spain",        "league_id": 140},
        {"country": "Spain",        "league_id": 141},
        {"country": "Italy",        "league_id": 135},
        {"country": "Italy",        "league_id": 136},
        {"country": "France",       "league_id": 61},
        {"country": "France",       "league_id": 62},
        {"country": "Turkey",       "league_id": 203},
        {"country": "Portugal",     "league_id": 94},
        {"country": "Belgium",      "league_id": 144},
        {"country": "Switzerland",  "league_id": 207},
        {"country": "Norway",       "league_id": 103},
        {"country": "Sweden",       "league_id": 67},
    ]


# =========================================================
# API SEGÉDEK
# =========================================================
def api_get(path, params, api_key, base_url):
    headers = {"x-apisports-key": api_key}
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    resp = requests.get(url, headers=headers, params=params, timeout=25)
    resp.raise_for_status()
    return resp.json().get("response", [])


def get_tomorrow_date_str():
    today_utc = datetime.now(timezone.utc).date()
    return (today_utc + timedelta(days=1)).strftime("%Y-%m-%d")


def fetch_fixtures_for_date(api_key, base_url, leagues, date_str):
    return api_get("/fixtures", {"date": date_str}, api_key, base_url)


def fetch_team_last_matches(api_key, base_url, team_id, last_n=15):
    """Legutóbbi N meccs lekérése. 15 meccs jobb mintát ad mint 10."""
    return api_get("/fixtures", {"team": team_id, "last": last_n}, api_key, base_url)


# =========================================================
# STATISZTIKA SZÁMÍTÁS — HAZAI/IDEGENBELI BONTÁSSAL
# =========================================================
def compute_basic_stats_from_matches(
    matches: List[Dict],
    team_id: int,
    side: str = "all",   # 'home' | 'away' | 'all'
) -> Dict[str, Any]:
    """
    Kiszámítja az alap statisztikákat a csapat elmúlt meccsei alapján.

    Paraméterek:
        side='home'  -> csak a hazai meccseket veszi figyelembe
        side='away'  -> csak az idegenbeli meccseket veszi figyelembe
        side='all'   -> minden meccset (legacy mód, visszafelé kompatibilis)

    Visszatér:
        goals_for_per_match, goals_against_per_match,
        over15_rate, over25_rate, btts_rate, avg_corners
    """
    empty = {
        "goals_for_per_match":     0.0,
        "goals_against_per_match": 0.0,
        "over15_rate":             0.0,
        "over25_rate":             0.0,
        "btts_rate":               0.0,
        "avg_corners":             None,
        "sample_size":             0,
    }
    if not matches:
        return empty

    total_for, total_against = 0, 0
    over15, over25, btts = 0, 0, 0
    total_corners, corners_count = 0, 0
    n = 0

    for m in matches:
        home_id    = m["teams"]["home"]["id"]
        away_id    = m["teams"]["away"]["id"]
        goals_home = m["goals"]["home"]
        goals_away = m["goals"]["away"]

        if goals_home is None or goals_away is None:
            continue

        is_home_match = (team_id == home_id)
        is_away_match = (team_id == away_id)

        if side == "home" and not is_home_match:
            continue
        if side == "away" and not is_away_match:
            continue
        if not is_home_match and not is_away_match:
            continue

        g_for     = goals_home if is_home_match else goals_away
        g_against = goals_away if is_home_match else goals_home

        total_for     += g_for
        total_against += g_against
        n += 1

        total_goals = goals_home + goals_away
        if total_goals >= 2: over15 += 1
        if total_goals >= 3: over25 += 1
        if goals_home > 0 and goals_away > 0: btts += 1

    if n == 0:
        return empty

    return {
        "goals_for_per_match":     total_for / n,
        "goals_against_per_match": total_against / n,
        "over15_rate":             over15 / n,
        "over25_rate":             over25 / n,
        "btts_rate":               btts / n,
        "avg_corners":             (total_corners / corners_count) if corners_count else None,
        "sample_size":             n,
    }


# =========================================================
# DIXON-COLES KORRIGÁLT LAMBDA SZÁMÍTÁS
# =========================================================
def dixon_coles_lambda(
    attack_avg: float,
    defence_avg: float,
    global_avg: float = GLOBAL_AVG_GOALS,
    home: bool = False,
) -> float:
    """
    Dixon-Coles alapú várható gólszám (lambda) kiszámítása.
    Képlet: lambda = attack_avg * defence_avg / global_avg
    """
    raw = (attack_avg * defence_avg) / max(global_avg, 0.01)
    if home:
        raw *= HOME_ADVANTAGE
    return max(raw, 0.05)


# =========================================================
# POISSON CDF — GÓLVALÓSZÍNŰSÉGEK
# =========================================================
def poisson_prob(lam: float, k: int) -> float:
    """P(X = k) Poisson-eloszlás szerint."""
    return exp(-lam) * (lam ** k) / factorial(k)


def poisson_cdf(lam: float, max_k: int) -> float:
    """P(X <= max_k) Poisson-eloszlás szerint."""
    return sum(poisson_prob(lam, k) for k in range(max_k + 1))


def prob_team_over_n5_goals(lam: float) -> float:
    """P(csapat >= 1 gól) — Poisson CDF alapján."""
    return 1.0 - poisson_prob(lam, 0)


# =========================================================
# MONTE CARLO SZIMULÁCIÓ
# =========================================================
def run_monte_carlo_simulation(
    home_lambda: float,
    away_lambda: float,
    simulations: int = 10_000,
) -> Dict[str, float]:
    """Poisson Monte Carlo szimuláció a valószínűségek finomításához."""
    h_goals = np.random.poisson(max(0.05, home_lambda), simulations)
    a_goals = np.random.poisson(max(0.05, away_lambda), simulations)
    total   = h_goals + a_goals
    return {
        "mc_over15": float(np.mean(total > 1.5)),
        "mc_over25": float(np.mean(total > 2.5)),
        "mc_btts":   float(np.mean((h_goals > 0) & (a_goals > 0))),
    }


# =========================================================
# FŐ MODELL — JAVÍTOTT VALÓSZÍNŰSÉG SZÁMÍTÁS
# =========================================================
def simple_model_probabilities(
    home_stats_h: Dict,
    home_stats_a: Dict,
    away_stats_a: Dict,
    away_stats_h: Dict,
) -> Dict[str, Any]:
    """Dixon-Coles korrigált lambda + Monte Carlo hibrid modell."""
    h_att = home_stats_h.get("goals_for_per_match") or 0.0
    h_def = home_stats_h.get("goals_against_per_match") or 0.0
    a_att = away_stats_a.get("goals_for_per_match") or 0.0
    a_def = away_stats_a.get("goals_against_per_match") or 0.0

    h_sample = home_stats_h.get("sample_size", 0)
    a_sample = away_stats_a.get("sample_size", 0)
    if h_sample < 3:
        h_att = home_stats_a.get("goals_for_per_match") or h_att
        h_def = home_stats_a.get("goals_against_per_match") or h_def
    if a_sample < 3:
        a_att = away_stats_h.get("goals_for_per_match") or a_att
        a_def = away_stats_h.get("goals_against_per_match") or a_def

    home_lambda = dixon_coles_lambda(h_att, a_def, GLOBAL_AVG_GOALS, home=True)
    away_lambda = dixon_coles_lambda(a_att, h_def, GLOBAL_AVG_GOALS, home=False)

    mc = run_monte_carlo_simulation(home_lambda, away_lambda)

    def avg_rates(s_h, s_a, key):
        vals = [v for v in [s_h.get(key), s_a.get(key)] if v is not None]
        return sum(vals) / len(vals) if vals else None

    hist_over15 = avg_rates(home_stats_h, away_stats_a, "over15_rate")
    hist_over25 = avg_rates(home_stats_h, away_stats_a, "over25_rate")
    hist_btts   = avg_rates(home_stats_h, away_stats_a, "btts_rate")

    global_btts = 0.52
    if hist_btts is not None:
        hist_btts = 0.5 * hist_btts + 0.5 * global_btts
    else:
        hist_btts = global_btts

    def hybrid(hist, mc_val):
        if hist is None:
            return mc_val
        return hist * 0.4 + mc_val * 0.6

    over15 = hybrid(hist_over15, mc["mc_over15"])
    over25 = hybrid(hist_over25, mc["mc_over25"])
    btts   = hybrid(hist_btts,   mc["mc_btts"])

    home_team_over15 = prob_team_over_n5_goals(home_lambda)
    away_team_over15 = prob_team_over_n5_goals(away_lambda)

    return {
        "over15":                  round(over15, 4),
        "over25":                  round(over25, 4),
        "btts":                    round(btts,   4),
        "home_team_over15_goals":  round(home_team_over15, 4),
        "away_team_over15_goals":  round(away_team_over15, 4),
        "_home_lambda":            round(home_lambda, 3),
        "_away_lambda":            round(away_lambda, 3),
    }


# =========================================================
# PROFIL ÉS ODDS LEKÉRÉS
# =========================================================
def fetch_odds_for_fixture(api_key, base_url, fixture_id):
    params = {"fixture": fixture_id, "bookmaker": 8}  # Bet365
    resp   = api_get("/odds", params, api_key, base_url)

    odds_out = {
        "over15": None, "over25": None, "btts": None,
        "home_team_over15_goals": None, "away_team_over15_goals": None,
        "double_chance_1x": None, "double_chance_x2": None,
        "home_dnb": None, "away_dnb": None,
        "combo_1x_over15": None, "combo_x2_over15": None,
    }
    for item in resp:
        for bookmaker in item.get("bookmakers", []):
            for bet in bookmaker.get("bets", []):
                bet_name = (bet.get("name") or "").lower()
                for val in bet.get("values", []):
                    raw_value = val.get("value")
                    value = str(raw_value).lower() if raw_value is not None else ""
                    odd = None
                    try:
                        odd = float(val["odd"]) if val.get("odd") is not None else None
                    except (ValueError, TypeError):
                        pass
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


def derive_profile(
    home_stats: Dict,
    away_stats: Dict,
    model_probs: Dict,
) -> Dict[str, Any]:
    hf = home_stats.get("goals_for_per_match") or 0
    ha = home_stats.get("goals_against_per_match") or 0
    af = away_stats.get("goals_for_per_match") or 0
    aa = away_stats.get("goals_against_per_match") or 0

    avg_for     = (hf + af) / 2
    avg_against = (ha + aa) / 2

    if avg_for >= 2.0 and avg_against >= 1.5:
        profile = "B"  # nyílt, gólgazdag
    elif avg_for >= 2.0 and avg_against <= 1.0:
        profile = "A"  # offenzív, jó védekezés
    elif avg_for <= 1.2 and avg_against <= 1.2:
        profile = "C"  # zárt, defenzív
    else:
        profile = "D"  # vegyes

    safe_over = bool(
        model_probs.get("over15") is not None
        and model_probs["over15"] >= 0.75
    )
    return {
        "match_profile":       profile,
        "safe_over_candidate": safe_over,
        "avoid_outright":      profile in ("C", "D"),
    }


# =========================================================
# TIPP GENERÁLÁS — JAVÍTOTT EV SZŰRŐ
# =========================================================
def generate_multi_market_tips_from_fixtures(
    fixtures: List[Dict[str, Any]],
    max_tips: int = 10,
    allowed_leagues: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Tipp generálás MARKET_CONFIG alapú EV szűrővel.

    Javítások a régi verzióhoz képest:
    1. Profil-szűrés: 'C' profil (zárt meccs) meccsen over25/btts tiltva
    2. max_p=0.92 → a 85%+ esélyek sem esnek ki (over15)
    3. min_ev emelve piacon: over15: 0.04, over25: 0.05, btts: 0.06
    4. MAX_TIPS_PER_FIXTURE=2: egy meccsen 2 különböző piac is bekerülhet,
       ha mindkettő átmegy a szűrőn (pl. over15 + over25 egyszerre)
    """
    raw_candidates: List[Dict[str, Any]] = []

    for fx in fixtures:
        league_name = fx.get("league")
        if allowed_leagues is not None and league_name not in allowed_leagues:
            continue

        probs   = fx.get("model_probabilities", {}) or {}
        odds    = fx.get("odds", {}) or {}
        derived = fx.get("derived_profile", {}) or {}
        profile = derived.get("match_profile", "D")
        safe    = bool(derived.get("safe_over_candidate"))

        # odds dict-ben a btts kulcs neve 'btts', a market neve 'btts_yes' —
        # az odds lekérőhöz igazítva:
        odds_map = {
            "over15":   odds.get("over15"),
            "over25":   odds.get("over25"),
            "btts_yes": odds.get("btts"),
        }
        prob_map = {
            "over15":   probs.get("over15"),
            "over25":   probs.get("over25"),
            "btts_yes": probs.get("btts"),
        }

        for market, cfg in MARKET_CONFIG.items():
            p = prob_map.get(market)
            o = odds_map.get(market)

            # Alapadatok hiánya
            if p is None or o is None:
                continue

            # ── PROFIL SZŰRŐ (ÚJ) ──────────────────────────────────────
            # Ha a meccs profilja szerepel a tiltott profilok között,
            # az adott piacon NEM ajánlunk tippet.
            if profile in cfg["blocked_profiles"]:
                continue

            # ── EV ÉS ODDS SZŰRŐ ───────────────────────────────────────
            ev = (p * o) - 1.0
            if not (cfg["min_ev"] <= ev
                    and cfg["min_p"] <= p <= cfg["max_p"]
                    and cfg["min_o"] <= o <= cfg["max_o"]):
                continue

            raw_candidates.append({
                **fx,
                "market":               market,
                "model_p":              p,
                "odds":                 o,
                "ev":                   round(ev, 4),
                "safe_over_candidate":  safe,
                "match_profile":        profile,
            })

    # ── DEDUPLIKÁCIÓ — max MAX_TIPS_PER_FIXTURE tipp/meccs ─────────────
    # Meccsenként a legjobb EV-jű tipppeket tartjuk meg,
    # de legfeljebb MAX_TIPS_PER_FIXTURE darabot.
    # Azonos piacon belül csak a legjobb marad (nincs duplikált over15).
    from collections import defaultdict
    fixture_markets: Dict[Any, Dict[str, Dict]] = defaultdict(dict)

    for c in raw_candidates:
        fid    = c["fixture_id"]
        market = c["market"]
        # Ugyanolyan piacból csak a legmagasabb EV marad
        if market not in fixture_markets[fid] or c["ev"] > fixture_markets[fid][market]["ev"]:
            fixture_markets[fid][market] = c

    # Meccsenként EV szerint rendezve, legfeljebb MAX_TIPS_PER_FIXTURE tipp
    deduped: List[Dict] = []
    for fid, markets in fixture_markets.items():
        top = sorted(markets.values(), key=lambda x: x["ev"], reverse=True)
        deduped.extend(top[:MAX_TIPS_PER_FIXTURE])

    # Végső rendezés: safe_over_candidate előre, azon belül EV szerint
    deduped.sort(key=lambda x: (x["safe_over_candidate"], x["ev"]), reverse=True)
    return deduped[:max_tips]


# =========================================================
# TELEGRAM ÜZENET KÜLDÉS
# =========================================================
def send_telegram_message_with_json(token, chat_id, tips_payload):
    if not token or not chat_id:
        print("⚠️ Telegram token vagy chat_id hiányzik.")
        return

    tips     = tips_payload.get("tips", [])
    date_str = tips_payload.get("date")
    header   = f"📊 <b>Foci Automata Tippek – {date_str}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    lines    = []

    for t in tips:
        emoji     = "🔥" if t.get("safe_over_candidate") else "⚽"
        time_str  = t.get("kickoff", "")[11:16] if t.get("kickoff") else "--:--"
        market_lk = {
            "over15":   "Gólszám 1,5 felett",
            "over25":   "Gólszám 2,5 felett",
            "btts_yes": "Mindkét csapat szerez gólt: IGEN",
        }
        market_display = market_lk.get((t.get("market") or "").lower(), (t.get("market") or "").upper())
        profile_emoji  = {"A": "🏆", "B": "⚡", "C": "🔒", "D": "🔀"}.get(t.get("match_profile"), "❓")

        mp = t.get("model_probabilities", {})
        lam_str = ""
        if mp.get("_home_lambda") and mp.get("_away_lambda"):
            lam_str = f"\n   λ hazai: {mp['_home_lambda']} | λ vendég: {mp['_away_lambda']}"

        lines.append(
            f"{emoji} <b>{t.get('home_team')} – {t.get('away_team')}</b>\n"
            f"🏆 {t.get('league')} | ⏰ {time_str} | {profile_emoji} Profil: {t.get('match_profile', '?')}\n"
            f"🎯 Tipp: <code>{market_display}</code>\n"
            f"📈 P: {t.get('model_p', 0)*100:.1f}% | EV: {t.get('ev', 0)*100:.1f}%{lam_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

    text = header + "\n".join(lines) if lines else header + "<i>Nincs mai tipp a szűrők alapján.</i>"
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=15)
        print("✅ Telegram üzenet elküldve.")
    except Exception as e:
        print(f"❌ Telegram hiba: {e}")


# =========================================================
# SUPABASE FELTÖLTÉS
# =========================================================
def upload_to_supabase(output_file, date_str, bucket_key="foci-master"):
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    bucket = os.environ.get("FOCI_MASTER_BUCKET", bucket_key)
    if not url or not key:
        print("⚠️ Supabase URL vagy KEY hiányzik.")
        return
    supabase: Client = create_client(url, key)
    path = f"{date_str}/{os.path.basename(output_file)}"
    with open(output_file, "rb") as f:
        data = f.read()
    try:
        supabase.storage.from_(bucket).upload(path=path, file=data, file_options={"cache-control": "3600", "upsert": "true"})
        print(f"✅ Supabase: {bucket}/{path}")
    except Exception as e:
        print(f"❌ Supabase hiba: {e}")


# =========================================================
# MAIN
# =========================================================
def main():
    api_key  = os.environ.get("API_FOOTBALL_KEY")
    base_url = os.environ.get("API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io")
    output_file = os.environ.get("FOCI_MASTER_OUTPUT_FILE", "foci_master_today.json")

    if not api_key:
        raise RuntimeError("Hiányzik az API_FOOTBALL_KEY env változó.")

    leagues_cfg        = load_league_config()
    allowed_league_ids = {l["league_id"] for l in leagues_cfg}
    date_str           = get_tomorrow_date_str()
    print(f"▶ Napi foci master build: {date_str}")

    fixtures_raw = fetch_fixtures_for_date(api_key, base_url, leagues_cfg, date_str)

    team_stats_cache: Dict[int, Dict[str, Dict]] = {}
    fixtures_out: List[Dict[str, Any]] = []

    for fx in fixtures_raw:
        if fx["league"]["id"] not in allowed_league_ids:
            continue

        fixture = fx["fixture"]
        league  = fx["league"]
        teams   = fx["teams"]
        home_id, away_id = teams["home"]["id"], teams["away"]["id"]

        for tid in (home_id, away_id):
            if tid not in team_stats_cache:
                raw_matches = fetch_team_last_matches(api_key, base_url, tid, last_n=15)
                team_stats_cache[tid] = {
                    "home": compute_basic_stats_from_matches(raw_matches, tid, side="home"),
                    "away": compute_basic_stats_from_matches(raw_matches, tid, side="away"),
                    "all":  compute_basic_stats_from_matches(raw_matches, tid, side="all"),
                }

        h_stats = team_stats_cache[home_id]
        a_stats = team_stats_cache[away_id]

        model_probs = simple_model_probabilities(
            home_stats_h = h_stats["home"],
            home_stats_a = h_stats["all"],
            away_stats_a = a_stats["away"],
            away_stats_h = a_stats["all"],
        )

        odds    = fetch_odds_for_fixture(api_key, base_url, fixture["id"])
        derived = derive_profile(h_stats["all"], a_stats["all"], model_probs)

        fixtures_out.append({
            "fixture_id": fixture["id"],
            "league":     league["name"],
            "country":    league["country"],
            "kickoff":    fixture["date"],
            "home_team":  teams["home"]["name"],
            "away_team":  teams["away"]["name"],
            "stats": {
                "home_last15_home_goals_for":     h_stats["home"]["goals_for_per_match"],
                "home_last15_home_goals_against": h_stats["home"]["goals_against_per_match"],
                "home_last15_home_sample":        h_stats["home"]["sample_size"],
                "away_last15_away_goals_for":     a_stats["away"]["goals_for_per_match"],
                "away_last15_away_goals_against": a_stats["away"]["goals_against_per_match"],
                "away_last15_away_sample":        a_stats["away"]["sample_size"],
                "home_over15_rate":  h_stats["all"]["over15_rate"],
                "home_over25_rate":  h_stats["all"]["over25_rate"],
                "home_btts_rate":    h_stats["all"]["btts_rate"],
                "away_over15_rate":  a_stats["all"]["over15_rate"],
                "away_over25_rate":  a_stats["all"]["over25_rate"],
                "away_btts_rate":    a_stats["all"]["btts_rate"],
            },
            "model_probabilities": model_probs,
            "odds":            odds,
            "derived_profile": derived,
        })

    output = {"date": date_str, "fixtures": fixtures_out}
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ Mentés kész: {output_file} ({len(fixtures_out)} meccs)")

    upload_to_supabase(output_file, date_str, bucket_key="foci-master")

    tips = generate_multi_market_tips_from_fixtures(fixtures_out)
    tips_payload = {
        "date":         date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tips":         tips,
    }
    tips_file = f"tips_{date_str}.json"
    with open(tips_file, "w", encoding="utf-8") as f:
        json.dump(tips_payload, f, ensure_ascii=False, indent=2)

    upload_to_supabase(tips_file, date_str, bucket_key="foci-tips")
    send_telegram_message_with_json(
        os.environ.get("TELEGRAM_BOT_TOKEN"),
        os.environ.get("TELEGRAM_CHAT_ID"),
        tips_payload,
    )


if __name__ == "__main__":
    main()
