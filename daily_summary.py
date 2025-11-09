# daily_summary.py
import os
import csv
import re
import time
import requests
from datetime import datetime
from collections import Counter, defaultdict

import pytz
from dotenv import load_dotenv

load_dotenv()

TIMEZONE = os.getenv("TIMEZONE", "Europe/Budapest")
tz = pytz.timezone(TIMEZONE)

TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
# /summary ideiglenes célja (privát), egyébként a csatorna
TELEGRAM_CHAT_ID   = (os.getenv("_TMP_SUMMARY_CHAT") or os.getenv("TELEGRAM_CHAT_ID") or "").strip()

RAPIDAPI_KEY  = (os.getenv("RAPIDAPI_KEY") or "").strip()
RAPIDAPI_HOST = (os.getenv("RAPIDAPI_HOST") or "api-football-v1.p.rapidapi.com").strip()

BASE_URL = "https://api-football-v1.p.rapidapi.com/v3"
HEADERS  = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}

def now_str():
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

def today_date_str():
    return datetime.now(tz).strftime("%Y-%m-%d")

def read_events_for_date(datestr: str):
    """
    A napi events.csv betöltése.
    Elsőként: data/YYYY-MM-DD/events.csv
    Másodsorban: logs/events.csv (fallback)
    """
    candidates = [f"data/{datestr}/events.csv", "logs/events.csv"]
    for path in candidates:
        if os.path.exists(path):
            rows = []
            with open(path, "r", encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in r:
                    rows.append(row)
            return rows, path
    return [], None

def pick_to_bucket(pick: str) -> str:
    # "Over 2.5 (live)" -> "Over 2.5" ; "Home Over 1.5 (live)" -> "Home Over 1.5"
    return re.sub(r"\s*\(live\)\s*$", "", pick or "").strip()

def dedup_events(rows):
    """
    Deduplikálás kulcson: (fixture_id, market, pick_bucket)
    időrendben az ELSŐ előfordulás marad.
    """
    def parse_time(s):
        try: return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except: return datetime.min
    rows_sorted = sorted(rows, key=lambda r: parse_time(r.get("time","")))
    out, seen = [], set()
    for r in rows_sorted:
        key = (str(r.get("fixture_id","")).strip(),
               str(r.get("market","")).strip(),
               pick_to_bucket(str(r.get("pick",""))))
        if key in seen: 
            continue
        seen.add(key)
        r["pick_bucket"] = key[2]
        out.append(r)
    return out

# ------------------------------
# API-Football lekérdezések
# ------------------------------

def _get(path, params, timeout=15):
    if not RAPIDAPI_KEY:
        return None
    try:
        r = requests.get(f"{BASE_URL}/{path}", headers=HEADERS, params=params, timeout=timeout)
        if r.status_code == 429:
            # rate limitnél próbáljunk kicsit hátrébb lépni
            time.sleep(2.0)
            r = requests.get(f"{BASE_URL}/{path}", headers=HEADERS, params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json().get("response", [])
    except Exception:
        return None

def fetch_fixture_final(fid: str):
    """
    Meccs végállapota + gólszámok.
    """
    resp = _get("fixtures", {"id": fid})
    if not resp:
        return None
    fx = resp[0]
    status = (fx.get("fixture",{}).get("status",{}).get("short") or "").upper()
    g = fx.get("goals", {}) or {}
    return {"status": status, "home": int(g.get("home") or 0), "away": int(g.get("away") or 0)}

def fetch_fixture_corners_final(fid: str):
    """
    Kész meccs végső szögletszáma (home+away).
    A fixtures/statistics végpontból olvassuk ki a "Corner Kicks"-et, és összegezzük.
    """
    resp = _get("fixtures/statistics", {"fixture": fid})
    if not resp:
        return None
    total = 0
    found_any = False
    try:
        for team_block in resp:
            for item in team_block.get("statistics", []) or []:
                if item.get("type") in ("Corner Kicks", "Corners", "Total Corners"):
                    val = item.get("value")
                    if isinstance(val, str):
                        try:
                            val = float(val)
                        except Exception:
                            continue
                    if isinstance(val, (int, float)):
                        total += int(val)
                        found_any = True
        return (total if found_any else None)
    except Exception:
        return None

# ------------------------------
# Kiértékelések (OVER / BTTS / TEAM_OVER / CORNERS)
# ------------------------------

OVER_RE   = re.compile(r"^over\s+(\d+(?:\.\d+)?)$", re.IGNORECASE)
TEAM_OVR  = re.compile(r"^(home|away)\s+over\s+(\d+(?:\.\d+)?)$", re.IGNORECASE)
CORN_OVR  = re.compile(r"^over\s+(\d+(?:\.\d+)?)$", re.IGNORECASE)  # a CORNERS pick_bucket "Over X.5" formájú

def eval_over(fi, pick_bucket: str):
    m = OVER_RE.match(pick_bucket or "")
    if not m: 
        return "unsupported"
    line = float(m.group(1))
    if not fi: 
        return "pending"
    st = (fi.get("status") or "").upper()
    if st not in ("FT","AET","PEN","ABD","AWD","WO"):
        return "pending"
    total = (fi.get("home",0) or 0) + (fi.get("away",0) or 0)
    return "win" if total > line else "loss"

def eval_btts(fi):
    if not fi: 
        return "pending"
    st = (fi.get("status") or "").upper()
    if st not in ("FT","AET","PEN","ABD","AWD","WO"):
        return "pending"
    return "win" if (fi.get("home",0)>=1 and fi.get("away",0)>=1) else "loss"

def eval_team_over(fi, pick_bucket: str):
    m = TEAM_OVR.match(pick_bucket or "")
    if not m: 
        return "unsupported"
    side, line = m.group(1).lower(), float(m.group(2))
    if not fi: 
        return "pending"
    st = (fi.get("status") or "").upper()
    if st not in ("FT","AET","PEN","ABD","AWD","WO"):
        return "pending"
    goals = fi.get("home",0) if side=="home" else fi.get("away",0)
    return "win" if goals > line else "loss"

def eval_corners(fid: str, pick_bucket: str):
    """
    Corners Over X.5 → total_corners > X.5
    A szögletszámot a fixtures/statistics-ból olvassuk (Corner Kicks).
    """
    m = CORN_OVR.match(pick_bucket or "")
    if not m:
        return "unsupported"
    line = float(m.group(1))
    total = fetch_fixture_corners_final(fid)
    if total is None:
        # nincs végső szögletadat → várjunk
        return "pending"
    return "win" if total > line else "loss"

def evaluate_rows(rows):
    """
    rows: deduplikált jelzések
    """
    # Először csak az OVER/BTTS/TEAM_OVER miatt kérjük le a végállapotot, hogy minimalizáljuk a hívásokat.
    by_fixture = defaultdict(list)
    for r in rows:
        by_fixture[str(r.get("fixture_id","")).strip()].append(r)

    fixture_outcomes = {}
    for fid in by_fixture.keys():
        fixture_outcomes[fid] = fetch_fixture_final(fid) if fid and fid.lower()!="none" else None

    evaluated = []
    for r in rows:
        market = (r.get("market") or "").upper()
        pb = r.get("pick_bucket") or pick_to_bucket(r.get("pick") or "")
        fid = str(r.get("fixture_id","")).strip()

        outcome = "pending"
        if market == "OVER":
            outcome = eval_over(fixture_outcomes.get(fid), pb)
        elif market == "BTTS":
            outcome = eval_btts(fixture_outcomes.get(fid))
        elif market == "TEAM_OVER":
            outcome = eval_team_over(fixture_outcomes.get(fid), pb)
        elif market == "CORNERS":
            # Cornersnél külön az összesített sarokrúgás számít
            outcome = eval_corners(fid, pb)
        else:
            outcome = "pending"

        r2 = dict(r); r2["outcome"] = outcome
        evaluated.append(r2)

    total = len(evaluated)
    counts = Counter([r["outcome"] for r in evaluated])
    won = counts.get("win",0); lost = counts.get("loss",0); void = counts.get("void",0); pend = counts.get("pending",0)
    denom = won + lost
    success_rate = round((won/denom*100.0),1) if denom>0 else 0.0

    markets = Counter([(r.get("market") or "").upper() for r in evaluated])
    leagues = Counter([(r.get("league") or "") for r in evaluated])

    def top_k(counter, k=3):
        return [(name, counter[name]) for name in sorted(counter, key=lambda x: (-counter[x], x))[:k]]

    stats = {
        "total": total,
        "win": won,
        "loss": lost,
        "void": void,
        "pending": pend,
        "success_rate": success_rate,
        "top_markets": top_k(markets, 3),
        "top_leagues": top_k(leagues, 3),
    }
    return stats, evaluated

def format_summary_message(date_str: str, stats: dict):
    def fmt_top(items):
        return "—" if not items else ", ".join([f"{name} ({cnt})" for name, cnt in items])
    return (
        f"🧾 <b>Napi összesítő – {date_str}</b>\n"
        f"Összes tipp: {stats['total']}\n"
        f"✅ Nyertes: {stats['win']}\n"
        f"❌ Vesztett: {stats['loss']}\n"
        f"↔️ Void: {stats['void']}\n"
        f"⏳ Függő: {stats['pending']}\n\n"
        f"Top piacok: {fmt_top(stats['top_markets'])}\n"
        f"Top ligák: {fmt_top(stats['top_leagues'])}\n"
        f"Sikerarány (void/pending nélkül): {stats['success_rate']}%\n"
    )

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[{now_str()}] Telegram token/chat hiányzik → nem küldtem el.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200:
            print(f"[{now_str()}] Telegram hiba: {r.status_code} {r.text}")
            return False
        return True
    except Exception as e:
        print(f"[{now_str()}] Telegram kivétel: {e}")
        return False

def main():
    # Ha a workflow ad SUMMARY_DATE-et (pl. tegnap), azt használjuk; különben ma.
    date_str = os.getenv("SUMMARY_DATE") or today_date_str()

    rows, src = read_events_for_date(date_str)
    if not rows:
        send_telegram(f"🧾 <b>Napi összesítő – {date_str}</b>\nMa nem keletkezett napló (nincs data).")
        return

    rows_dedup = dedup_events(rows)
    stats, evaluated = evaluate_rows(rows_dedup)

    send_telegram(format_summary_message(date_str, stats))

    # Debug napló
    try:
        os.makedirs("logs", exist_ok=True)
        with open("logs/summary_debug.txt", "a", encoding="utf-8") as f:
            f.write(f"[{now_str()}] date={date_str} src={src}\n")
            f.write(f"raw={len(rows)} dedup={len(rows_dedup)} stats={stats}\n\n")
    except Exception:
        pass

if __name__ == "__main__":
    main()
