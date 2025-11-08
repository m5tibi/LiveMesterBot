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
# Alapértelmezett cél: csatorna; /summary esetén a livemesterbot.py ideiglenesen beállít _TMP_SUMMARY_CHAT-et
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
    Betölti a napi events.csv-t:
      1) data/YYYY-MM-DD/events.csv  (ha van)
      2) különben logs/events.csv    (fallback)
    Visszaad: list[dict]
    """
    candidates = [
        f"data/{datestr}/events.csv",
        "logs/events.csv",
    ]
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
    """
    "Over 2.5 (live)" -> "Over 2.5"
    """
    if not pick:
        return ""
    return re.sub(r"\s*\(live\)\s*$", "", pick).strip()

def dedup_events(rows):
    """
    Dedup kulcs: (fixture_id, market, pick_bucket), az első előfordulást meghagyjuk időrendben.
    """
    # idő szerint rendezés (ha van 'time' mező)
    def parse_time(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.min
    rows_sorted = sorted(rows, key=lambda r: parse_time(r.get("time","")))
    seen = set()
    out = []
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

def fetch_fixture_final(fixture_id: str, retry=3, sleep_sec=1.5):
    """
    Lekéri a meccs végállapotát az API-Footballtól.
    Visszaad: dict { 'status': 'FT/NS/...' , 'home': int, 'away': int }
    Ha nincs adat, None.
    """
    if not RAPIDAPI_KEY:
        return None
    url = f"{BASE_URL}/fixtures"
    params = {"id": fixture_id}
    for i in range(retry):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(min(10, sleep_sec * (2 ** i)))
                continue
            if r.status_code != 200:
                return None
            resp = r.json().get("response", [])
            if not resp:
                return None
            fx = resp[0]
            status = (fx.get("fixture",{}).get("status",{}).get("short") or "").upper()
            g = fx.get("goals", {}) or {}
            home = int(g.get("home") or 0)
            away = int(g.get("away") or 0)
            return {"status": status, "home": home, "away": away}
        except Exception:
            time.sleep(min(10, sleep_sec * (2 ** i)))
    return None

OVER_RE = re.compile(r"^over\s+(\d+(?:\.\d+)?)$", re.IGNORECASE)

def eval_over(outcome_info, pick_bucket: str):
    """
    Kimenet (win/loss/pending):
      - pending: ha nem FT/AET/PEN vége
      - win/loss: total_goals vs. N.5
    """
    m = OVER_RE.match(pick_bucket or "")
    if not m:
        return "unsupported"
    line = float(m.group(1))
    if not outcome_info:
        return "pending"
    status = (outcome_info.get("status") or "").upper()
    if status not in ("FT","AET","PEN","ABD","AWD","WO"):
        return "pending"
    total = (outcome_info.get("home",0) or 0) + (outcome_info.get("away",0) or 0)
    return "win" if total > line else "loss"

def evaluate_rows(rows):
    """
    rows: deduplikált jelzések
    Vissza: (stats, evaluated_rows)
      stats: összesítések
      evaluated_rows: sorok outcome mezővel
    """
    # Csoportosítsunk fixture szerint, hogy ne hívjuk többször az API-t
    by_fixture = defaultdict(list)
    for r in rows:
        by_fixture[str(r.get("fixture_id","")).strip()].append(r)

    fixture_outcomes = {}
    for fid in by_fixture.keys():
        if not fid or fid.lower() == "none":
            fixture_outcomes[fid] = None
            continue
        fixture_outcomes[fid] = fetch_fixture_final(fid)

    evaluated = []
    for r in rows:
        market = (r.get("market") or "").upper()
        pick_b = r.get("pick_bucket") or pick_to_bucket(r.get("pick") or "")
        outcome = "pending"
        if market == "OVER":
            outcome = eval_over(fixture_outcomes.get(str(r.get("fixture_id","")).strip()), pick_b)
        elif market in ("NEXT_GOAL","DNB","LATE_GOAL","UNDER"):
            # Itt most nem implementálunk részletes értékelést -> pending/unsupported
            outcome = "pending"
        else:
            outcome = "pending"
        r2 = dict(r)
        r2["outcome"] = outcome
        evaluated.append(r2)

    # Összesítések
    total = len(evaluated)
    cnt = Counter([r["outcome"] for r in evaluated])
    won   = cnt.get("win", 0)
    lost  = cnt.get("loss", 0)
    void  = cnt.get("void", 0)  # jelenleg nincs void logika
    pend  = cnt.get("pending", 0)
    # Sikerarány (void/pending nélkül)
    denom = won + lost
    success_rate = (won / denom * 100.0) if denom > 0 else 0.0

    # Top piacok, top ligák
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
        "success_rate": round(success_rate, 1),
        "top_markets": top_k(markets, 3),
        "top_leagues": top_k(leagues, 3),
    }
    return stats, evaluated

def format_summary_message(date_str: str, stats: dict):
    def fmt_top(items):
        if not items:
            return "—"
        return ", ".join([f"{name} ({cnt})" for name, cnt in items])

    text = (
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
    return text

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[{now_str()}] Telegram token/chat hiányzik → nem küldtem el.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
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
    # Melyik napot értékeljük? Alapértelmezetten MA (BUD időzóna)
    date_str = os.getenv("SUMMARY_DATE") or today_date_str()

    rows, src = read_events_for_date(date_str)
    if not rows:
        send_telegram(f"🧾 <b>Napi összesítő – {date_str}</b>\nMa nem keletkezett napló (nincs data).")
        return

    # DEDUP
    rows_dedup = dedup_events(rows)

    # Kiértékelés (OVER piacra tényleges W/L, többire jelenleg pending)
    stats, evaluated = evaluate_rows(rows_dedup)

    # Üzenet
    msg = format_summary_message(date_str, stats)

    # Küldés
    send_telegram(msg)

    # (Opcionális) debug log
    try:
        os.makedirs("logs", exist_ok=True)
        with open("logs/summary_debug.txt", "a", encoding="utf-8") as f:
            f.write(f"[{now_str()}] date={date_str} src={src}\n")
            f.write(f"raw={len(rows)} dedup={len(rows_dedup)} stats={stats}\n\n")
    except Exception:
        pass

if __name__ == "__main__":
    main()
