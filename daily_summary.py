import os
import csv
import requests
from datetime import datetime
import pytz
from dotenv import load_dotenv

load_dotenv()

TIMEZONE = os.getenv("TIMEZONE", "Europe/Budapest")
tz = pytz.timezone(TIMEZONE)

TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID   = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

RAPIDAPI_KEY  = (os.getenv("RAPIDAPI_KEY") or "").strip()
RAPIDAPI_HOST = (os.getenv("RAPIDAPI_HOST") or "api-football-v1.p.rapidapi.com").strip()

BASE_URL = "https://api-football-v1.p.rapidapi.com/v3"

def today_str():
    return datetime.now(tz).strftime("%Y-%m-%d")

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram token/chat_id hiányzik – kihagyva.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
        if r.status_code != 200:
            print("Telegram hiba:", r.status_code, r.text)
    except Exception as e:
        print("Telegram kivétel:", e)

def rapid_headers():
    return {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}

def fetch_fixture(fid: int):
    try:
        r = requests.get(f"{BASE_URL}/fixtures", headers=rapid_headers(), params={"id": fid}, timeout=25)
        if r.status_code != 200: return None
        resp = r.json().get("response", [])
        return resp[0] if resp else None
    except Exception:
        return None

def fetch_events(fid: int):
    try:
        r = requests.get(f"{BASE_URL}/fixtures/events", headers=rapid_headers(), params={"fixture": fid}, timeout=25)
        if r.status_code != 200: return []
        return r.json().get("response", [])
    except Exception:
        return []

def parse_over_threshold(pick: str):
    # "Over 2.5 (live)" → 2.5
    try:
        for tok in pick.split():
            try:
                return float(tok)
            except:
                continue
    except:
        pass
    return None

def first_goal_after_minute(events, minute: int):
    # visszaadja az első gól eseményt a perc után: {"team_side":"home"/"away"} vagy None
    for ev in events:
        if ev.get("type") == "Goal":
            tname = (ev.get("team", {}) or {}).get("name", "")
            # perc lehet "67'", "90'+2" stb.
            mt = ev.get("time", {}) or {}
            elapsed = mt.get("elapsed") or 0
            extra = mt.get("extra") or 0
            eff_min = (elapsed or 0) + (extra or 0)
            if eff_min > minute:
                # oldal meghatározása a "home"/"away" a tname alapján nem triviális,
                # viszont az API event elemében van "team.id" → összevetjük a fixture csapat-id-kkal, ha megvannak
                return ev
    return None

def team_side_from_event(ev, fixture):
    team_id = ((ev or {}).get("team") or {}).get("id")
    if not team_id or not fixture: return None
    home_id = ((fixture.get("teams") or {}).get("home") or {}).get("id")
    away_id = ((fixture.get("teams") or {}).get("away") or {}).get("id")
    if team_id == home_id: return "home"
    if team_id == away_id: return "away"
    return None

def evaluate_row(row, cache):
    """
    Visszaad: ("win"|"loss"|"void"|"pending", info_str)
    """
    market = (row.get("market") or "").upper()
    pick   = (row.get("pick") or "")
    fid    = row.get("fixture_id")
    minute = 0
    try:
        minute = int(row.get("minute") or "0")
    except:
        minute = 0

    if not fid:
        return ("pending", "missing_fixture_id")

    try:
        fid = int(fid)
    except:
        return ("pending", "bad_fixture_id")

    # cache: kevesebb API hívás
    if fid not in cache:
        fx = fetch_fixture(fid)
        evs = fetch_events(fid)
        cache[fid] = {"fixture": fx, "events": evs}
    else:
        data = cache[fid]
        fx, evs = data.get("fixture"), data.get("events")

    data = cache[fid]
    fx = data.get("fixture")
    evs = data.get("events", [])

    if not fx:
        return ("pending", "fixture_not_found")

    status = (((fx.get("fixture") or {}).get("status") or {}).get("short") or "")
    goals  = fx.get("goals") or {}
    ft_home = goals.get("home", 0) or 0
    ft_away = goals.get("away", 0) or 0
    total  = ft_home + ft_away

    # OVER
    if market == "OVER":
        thr = parse_over_threshold(pick)
        if thr is None:
            return ("pending", "no_threshold")
        if status in ("FT","AET","PEN"):
            return ("win", f"{total}>{thr}") if total > thr else ("loss", f"{total}<={thr}")
        else:
            return ("pending", "not_finished")

    # DNB
    if market == "DNB":
        # "Hazai DNB" / "Vendég DNB" → home/away side
        side = "home" if "Hazai" in pick else ("away" if "Vendég" in pick else None)
        if side is None:
            return ("pending", "dnb_side_unknown")
        if status in ("FT","AET","PEN"):
            if ft_home == ft_away:
                return ("void", "draw")
            winner = "home" if ft_home > ft_away else "away"
            return ("win", winner) if winner == side else ("loss", winner)
        else:
            return ("pending", "not_finished")

    # NEXT_GOAL  és LATE_GOAL (Next típus)
    if market in ("NEXT_GOAL", "LATE_GOAL"):
        if "Következő gól" in pick:
            side = "home" if "Hazai" in pick else ("away" if "Vendég" in pick else None)
            if side is None:
                return ("pending", "next_side_unknown")
            # első gól a perc után:
            ev = first_goal_after_minute(evs, minute)
            if not ev:
                # ha nem esett több gól, akkor bukta
                if status in ("FT","AET","PEN"):
                    return ("loss", "no_later_goal")
                return ("pending", "no_later_goal_yet")
            scored_side = team_side_from_event(ev, fx)
            if scored_side is None:
                return ("pending", "cant_map_team")
            return ("win", scored_side) if scored_side == side else ("loss", scored_side)
        else:
            # LATE_GOAL Over 0.5 (Late) — legalább 1 gól még a jelzés után
            if "Over 0.5" in pick:
                ev = first_goal_after_minute(evs, minute)
                if not ev:
                    if status in ("FT","AET","PEN"):
                        return ("loss", "no_later_goal")
                    return ("pending", "no_later_goal_yet")
                return ("win", "late_over_hit")
            # egyéb speciális minták
            return ("pending", "unknown_late_pattern")

    # UNDER-t most nem értékeljük külön (nálad kikapcsolt)
    return ("pending", "unknown_market")

def main():
    day = today_str()
    data_path = os.path.join("data", day, "events.csv")

    rows = []
    if os.path.exists(data_path):
        with open(data_path, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            rows = list(r)
    else:
        # fallback: ha valamiért nem perzisztált a napi fájl
        if os.path.exists("logs/events.csv"):
            with open("logs/events.csv","r",encoding="utf-8") as f:
                r = csv.DictReader(f)
                rows = list(r)

    if not rows:
        send_telegram(f"🧾 Napi összesítő – {day}\nMa nem keletkezett napló (nincs data).")
        return

    cache = {}
    totals = {"all":0,"win":0,"loss":0,"void":0,"pending":0}
    by_market = {}
    by_league = {}

    for row in rows:
        totals["all"] += 1
        league = row.get("league","").strip()
        market = (row.get("market","") or "").upper()
        by_market[market] = by_market.get(market, 0) + 1
        by_league[league] = by_league.get(league, 0) + 1

        outcome, _info = evaluate_row(row, cache)
        totals[outcome] = totals.get(outcome, 0) + 1

    played = totals["win"] + totals["loss"]  # void/pending nélkül
    success = (totals["win"] / played * 100.0) if played > 0 else 0.0

    # top piacok/ligák (3-3)
    def topn(d, n=3):
        return ", ".join([f"{k} ({v})" for k,v in sorted(d.items(), key=lambda x: x[1], reverse=True)[:n]])

    msg = (
        f"🧾 <b>Napi összesítő</b> – {day}\n"
        f"Összes tipp: <b>{totals['all']}</b>\n"
        f"✅ Nyertes: <b>{totals['win']}</b>\n"
        f"❌ Vesztett: <b>{totals['loss']}</b>\n"
        f"↔️ Void: <b>{totals['void']}</b>\n"
        f"⏳ Függő: <b>{totals['pending']}</b>\n"
        f"\nTop piacok: {topn(by_market)}\n"
        f"Top ligák: {topn(by_league)}\n"
        f"Sikerarány (void/pending nélkül): <b>{success:.1f}%</b>"
    )

    send_telegram(msg)

if __name__ == "__main__":
    main()
