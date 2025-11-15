#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LiveMesterBot – minőségi szűrőkkel, BTTS-sel, Tippmixpro-szűréssel, magyar lokalizációval és LIVE ODDS bekötéssel

Főbb funkciók:
- Élő meccsek (fixtures) API-FOOTBALL (RapidAPI)
- Élő statok (shots, SOT) kvóta-óvatosan
- Élő odds bekötés OVER és BTTS piacokra (min. odds + value-szűrés)
- 4 erős minőségi szűrő az OVER jelekre (perc-sáv, odds, value, lövés/SOT)
- BTTS (igen) nagyon szigorú szűrés
- Tippmixpro-only mód (config/ whitelist alapján)
- Magyar névleképezés (config/hu_map.json)
- Duplikáció-védelem, CSV log, admin /kvóta és /summary
"""

import os
import csv
import json
import time
import signal
import unicodedata
from collections import deque
from datetime import datetime, timedelta

import requests
import pytz
from dotenv import load_dotenv

load_dotenv()

# ======== .env / alap ========
TIMEZONE = os.getenv("TIMEZONE", "Europe/Budapest")
tz = pytz.timezone(TIMEZONE)

TELEGRAM_BOT_TOKEN      = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID        = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
TELEGRAM_ADMIN_CHAT_ID  = (os.getenv("TELEGRAM_ADMIN_CHAT_ID") or TELEGRAM_CHAT_ID).strip()

RAPIDAPI_KEY  = (os.getenv("RAPIDAPI_KEY") or "").strip()
RAPIDAPI_HOST = (os.getenv("RAPIDAPI_HOST") or "api-football-v1.p.rapidapi.com").strip()

START_HOUR   = int(os.getenv("START_HOUR", "5"))
END_HOUR     = int(os.getenv("END_HOUR",   "23"))
RUN_MINUTES  = int(os.getenv("RUN_MINUTES","0"))  # 0 = végtelen
POLL_SECONDS = int(os.getenv("POLL_SECONDS","120"))
LOW_BUDGET_POLL_SECONDS = int(os.getenv("LOW_BUDGET_POLL_SECONDS","300"))

MAX_FIXTURES_PER_CYCLE       = int(os.getenv("MAX_FIXTURES_PER_CYCLE","24"))
MAX_STATS_LOOKUPS_PER_CYCLE  = int(os.getenv("MAX_STATS_LOOKUPS_PER_CYCLE","12"))
MAX_ODDS_LOOKUPS_PER_CYCLE   = int(os.getenv("MAX_ODDS_LOOKUPS_PER_CYCLE","16"))  # kvóta-óvatos

MIN_MINUTE = int(os.getenv("MIN_MINUTE","10"))
MAX_MINUTE = int(os.getenv("MAX_MINUTE","88"))

# Odds / value
ODDS_MODE        = (os.getenv("ODDS_MODE","real") or "real").lower()   # 'real' => kötelezően live odds-szal szűr
OVER_MIN_ODDS    = float(os.getenv("OVER_MIN_ODDS","1.40"))
BTTS_MIN_ODDS    = float(os.getenv("BTTS_MIN_ODDS","1.65"))
VALUE_THRESHOLD  = float(os.getenv("VALUE_THRESHOLD","1.05"))
ODDS_BOOKMAKER   = (os.getenv("ODDS_BOOKMAKER","") or "").strip()      # pl. Bet365 (ha üres, bármelyik)

# Tippmixpro-only
TIPP_ONLY_MODE = os.getenv("TIPP_ONLY_MODE","false").lower() in ("1","true","yes","on")

NO_REPEAT_SAME_TIP = os.getenv("NO_REPEAT_SAME_TIP","true").lower() in ("1","true","yes","on")
SAVE_EVENTS = os.getenv("SAVE_EVENTS","true").lower() in ("1","true","yes","on")
DEBUG_LOG   = os.getenv("DEBUG_LOG","1").lower() in ("1","true","yes","on")
SEND_ONLINE_ON_START = os.getenv("SEND_ONLINE_ON_START","0") == "1"

RAPIDAPI_DAILY_LIMIT    = int(os.getenv("RAPIDAPI_DAILY_LIMIT","7500"))
RAPIDAPI_SAFETY_RESERVE = int(os.getenv("RAPIDAPI_SAFETY_RESERVE","150"))
API_USAGE_PATH          = os.path.join("logs","api_usage.json")

BASE_URL = "https://api-football-v1.p.rapidapi.com/v3"
HEADERS  = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}

# ======== util ========
def now(): return datetime.now(tz)
def now_str(): return now().strftime("%Y-%m-%d %H:%M:%S")
def today_ymd(): return now().strftime("%Y-%m-%d")
def ensure_dir(p): os.makedirs(p, exist_ok=True)

def normalize_text(s: str) -> str:
    s = s or ""
    s = s.strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join([c for c in s if not unicodedata.combining(c)])
    s = s.lower()
    s = s.replace("–","-").replace("—","-").replace("－","-")
    s = s.replace("  "," ").replace("\t"," ").strip()
    return s

# ======== kvóta számláló ========
def _load_api_usage():
    ensure_dir("logs")
    if not os.path.exists(API_USAGE_PATH): return {}
    try:
        with open(API_USAGE_PATH,"r",encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def _save_api_usage(obj):
    try:
        with open(API_USAGE_PATH,"w",encoding="utf-8") as f: json.dump(obj,f,ensure_ascii=False,indent=2)
    except Exception: pass

def api_usage_today():
    d = _load_api_usage()
    return int(d.get(today_ymd(),0))

def api_usage_inc(n=1):
    d = _load_api_usage()
    k = today_ymd()
    d[k] = int(d.get(k,0)) + int(n)
    _save_api_usage(d)

def api_remaining():
    used = api_usage_today()
    rem = max(0, RAPIDAPI_DAILY_LIMIT - used)
    return used, rem

# ======== Telegram ========
def tg_send(chat_id, text, parse_mode="HTML", disable_web_page_preview=True):
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        if DEBUG_LOG: print(f"[{now_str()}] Telegram token/chat hiányzik – nem küldtem.")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id":chat_id,"text":text,"parse_mode":parse_mode,"disable_web_page_preview":disable_web_page_preview},
            timeout=20
        )
        return (r.status_code==200)
    except Exception as e:
        if DEBUG_LOG: print(f"[{now_str()}] Telegram hiba: {e}")
        return False

def tg_send_pub(text):   return tg_send(TELEGRAM_CHAT_ID, text)
def tg_send_admin(text): return tg_send(TELEGRAM_ADMIN_CHAT_ID, text)

# ======== Admin parancsok ========
_last_update_id = None

def handle_cmd_quota(chat_id):
    used, rem = api_remaining()
    pct = round(100.0 * used / RAPIDAPI_DAILY_LIMIT, 1) if RAPIDAPI_DAILY_LIMIT>0 else 0.0
    approx_calls_per_cycle = 3
    left_cycles = rem // approx_calls_per_cycle
    msg = ( "<b>🔐 RapidAPI kvóta</b>\n"
            f"Felhasználva ma: <b>{used:,}</b> / {RAPIDAPI_DAILY_LIMIT:,} ({pct}%)\n"
            f"Maradék ma: <b>{rem:,}</b>\n"
            f"Becsült hátralévő ciklusok: ~{int(left_cycles):,}\n"
            f"Bizt. tartalék: {RAPIDAPI_SAFETY_RESERVE}\n"
            f"Polling: {POLL_SECONDS}s / alacsony keretnél {LOW_BUDGET_POLL_SECONDS}s" )
    tg_send(chat_id, msg)

def poll_admin_updates():
    global _last_update_id
    if not TELEGRAM_BOT_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout":0}
    if _last_update_id is not None: params["offset"] = _last_update_id+1
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200: return
        for upd in (r.json().get("result") or []):
            _last_update_id = upd.get("update_id", _last_update_id)
            msg = upd.get("message") or upd.get("channel_post") or {}
            text = (msg.get("text") or "").strip()
            chat = msg.get("chat", {}) or {}
            chat_id = str(chat.get("id",""))
            if not text or not chat_id: continue
            if TELEGRAM_ADMIN_CHAT_ID and chat_id != str(TELEGRAM_ADMIN_CHAT_ID): continue
            low = text.lower()
            if low.startswith("/kvóta") or low.startswith("/kvota"):
                handle_cmd_quota(chat_id)
            elif low.startswith("/summary"):
                try:
                    from daily_summary import main as run_daily_summary
                    os.environ["_TMP_SUMMARY_CHAT"] = chat_id
                    tg_send(chat_id, "📊 Napi összegzés indítása...")
                    try: run_daily_summary()
                    finally: os.environ.pop("_TMP_SUMMARY_CHAT", None)
                except Exception as e:
                    tg_send(chat_id, f"⚠️ A /summary nem elérhető vagy hiba történt: {e}")
    except Exception:
        return

# ======== Automatikus kvóta ping ========
_last_quota_ping_ts = 0
QUOTA_PING_ENABLED   = os.getenv("QUOTA_PING_ENABLED","true").lower() in ("1","true","yes","on")
QUOTA_PING_EVERY_MIN = int(os.getenv("QUOTA_PING_EVERY_MIN","180"))
QUOTA_PING_ON_LOW    = os.getenv("QUOTA_PING_ON_LOW","true").lower() in ("1","true","yes","on")

def send_admin_quota():
    used, rem = api_remaining()
    pct = round(100.0 * used / RAPIDAPI_DAILY_LIMIT, 1) if RAPIDAPI_DAILY_LIMIT>0 else 0.0
    approx_calls_per_cycle = 3
    left_cycles = rem // approx_calls_per_cycle
    msg = ( "<b>🔐 RapidAPI kvóta – automatikus jelentés</b>\n"
            f"Felhasználva ma: <b>{used:,}</b> / {RAPIDAPI_DAILY_LIMIT:,} ({pct}%)\n"
            f"Maradék ma: <b>{rem:,}</b>\n"
            f"Becsült hátralévő ciklusok: ~{int(left_cycles):,}\n"
            f"Bizt. tartalék: {RAPIDAPI_SAFETY_RESERVE}\n"
            f"Polling: {POLL_SECONDS}s / alacsony keretnél {LOW_BUDGET_POLL_SECONDS}s" )
    return tg_send_admin(msg)

def maybe_quota_ping(rem):
    global _last_quota_ping_ts
    if not QUOTA_PING_ENABLED or not TELEGRAM_ADMIN_CHAT_ID: return
    now_ts = int(time.time())
    if (now_ts - _last_quota_ping_ts) >= QUOTA_PING_EVERY_MIN * 60:
        if send_admin_quota(): _last_quota_ping_ts = now_ts
    if QUOTA_PING_ON_LOW and rem <= RAPIDAPI_SAFETY_RESERVE:
        if (now_ts - _last_quota_ping_ts) >= 15*60:
            if send_admin_quota(): _last_quota_ping_ts = now_ts

# ======== API-FOOTBALL kérések ========
def api_get(path, params=None, timeout=15):
    if not RAPIDAPI_KEY: return None
    try:
        api_usage_inc(1)
        r = requests.get(f"{BASE_URL}/{path}", headers=HEADERS, params=params or {}, timeout=timeout)
        if r.status_code == 429:
            time.sleep(2.0)
            api_usage_inc(1)
            r = requests.get(f"{BASE_URL}/{path}", headers=HEADERS, params=params or {}, timeout=timeout)
        if r.status_code != 200: return None
        return r.json().get("response", [])
    except Exception:
        return None

def fetch_live_fixtures():
    resp = api_get("fixtures", {"live":"all"})
    out = []
    if not resp: return out
    for fx in resp:
        fixture = fx.get("fixture", {}) or {}
        league  = fx.get("league",  {}) or {}
        teams   = fx.get("teams",   {}) or {}
        goals   = fx.get("goals",   {}) or {}
        minute  = fixture.get("status", {}).get("elapsed")
        try: minute = int(minute) if minute is not None else None
        except: minute = None
        out.append({
            "fixture_id": str(fixture.get("id")),
            "league": league.get("name") or "-",
            "league_country": league.get("country") or "",
            "match_home": (teams.get("home", {}) or {}).get("name") or "Home",
            "match_away": (teams.get("away", {}) or {}).get("name") or "Away",
            "minute": minute,
            "score_home": int(goals.get("home") or 0),
            "score_away": int(goals.get("away") or 0),
            "score": f"{int(goals.get('home') or 0)}:{int(goals.get('away') or 0)}",
        })
    return out

def fetch_fixture_stats(fid: str):
    arr = api_get("fixtures/statistics", {"fixture": fid})
    if not arr: return None
    out = {"home":{}, "away":{}}
    for t in arr:
        stats = t.get("statistics") or []
        block = {}
        for s in stats:
            k = (s.get("type") or "").strip()
            v = s.get("value")
            block[k] = v if v is not None else 0
        # egyszerű kiosztás home/away sorrendben
        if not out["home"]: out["home"] = block
        else: out["away"] = block
    return out

# ======== LIVE ODDS ========
# piaci elnevezések variációi
OU_BET_NAMES   = {"goals over/under", "match goals", "total goals", "over/under"}
BTTS_BET_NAMES = {"both teams to score", "btts", "gg/ng"}

def _pick_bookmaker(blocks, prefer_name):
    if not blocks: return None
    if prefer_name:
        for b in blocks:
            name = (b.get("name") or "").strip()
            if name.lower() == prefer_name.lower():
                return b
    # ha nem talált preferáltat, vissza az első értelmeset
    return blocks[0]

def fetch_live_odds_block(fid: str):
    """
    Odds források:
      1) odds/live?fixture=FID
      2) odds?fixture=FID
    """
    # 1) élő odds
    arr = api_get("odds/live", {"fixture": fid})
    if arr and isinstance(arr, list) and len(arr)>0:
        return arr[0]
    # 2) fallback
    arr = api_get("odds", {"fixture": fid})
    if arr and isinstance(arr, list) and len(arr)>0:
        return arr[0]
    return None

def parse_over_odds(odds_block, line_wanted: float, prefer_bookmaker: str):
    """
    Megpróbálja kinyerni az Over <line> oddst a bookmakers/bets/values tömbből.
    Elfogad többféle bet.name-et és values.value formát.
    """
    if not odds_block: return None
    books = odds_block.get("bookmakers") or []
    book = _pick_bookmaker(books, prefer_bookmaker)
    if not book: return None
    for bet in (book.get("bets") or []):
        name = (bet.get("name") or "").strip().lower()
        if name not in OU_BET_NAMES:  # Over/Under piac variációk
            continue
        for val in (bet.get("values") or []):
            v = (val.get("value") or "").strip().lower()
            # formátumok: "Over 2.5", "Over 1.5", "2.5 Over" – kezeljük rugalmasan
            tgt1 = f"over {line_wanted}".replace(".0","")
            tgt2 = f"{line_wanted} over".replace(".0","")
            if v == tgt1 or v == tgt2:
                try:
                    return float(val.get("odd"))
                except: 
                    continue
    return None

def parse_btts_odds(odds_block, prefer_bookmaker: str):
    """
    Visszaadja a BTTS Yes oddsát (ha van).
    """
    if not odds_block: return None
    books = odds_block.get("bookmakers") or []
    book = _pick_bookmaker(books, prefer_bookmaker)
    if not book: return None
    for bet in (book.get("bets") or []):
        name = (bet.get("name") or "").strip().lower()
        if name not in BTTS_BET_NAMES: 
            continue
        for val in (bet.get("values") or []):
            v = (val.get("value") or "").strip().lower()
            if v in ("yes","igen","y"):  # több nyelvi variációra felkészülve
                try:
                    return float(val.get("odd"))
                except:
                    continue
    return None

# ======== Tippmixpro-only ========
def load_tipp_league_whitelist():
    path = os.path.join("config","tippmixpro_leagues.txt")
    if not os.path.exists(path): return set()
    vals = set()
    with open(path,"r",encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s: vals.add(normalize_text(s))
    return vals

def load_tipp_fixtures_csv():
    path = os.path.join("config","tippmixpro_fixtures.csv")
    if not os.path.exists(path): return []
    out = []
    with open(path,"r",encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            out.append({
                "date": row.get("date","").strip(),
                "league": row.get("league","").strip(),
                "home": row.get("home","").strip(),
                "away": row.get("away","").strip(),
            })
    return out

def fixture_in_tipp_list(fx, fixtures_csv):
    today = today_ymd()
    n_league = normalize_text(fx["league"])
    n_home   = normalize_text(fx["match_home"])
    n_away   = normalize_text(fx["match_away"])
    for row in fixtures_csv:
        if row.get("date") and row["date"] != today: continue
        if normalize_text(row.get("league","")) != n_league: continue
        if normalize_text(row.get("home","")) == n_home and normalize_text(row.get("away","")) == n_away:
            return True
    return False

def filter_tipp_only(fixtures):
    if not TIPP_ONLY_MODE: return fixtures
    leagues = load_tipp_league_whitelist()
    fixtures_csv = load_tipp_fixtures_csv()
    if fixtures_csv:
        return [fx for fx in fixtures if fixture_in_tipp_list(fx, fixtures_csv)]
    if leagues:
        return [fx for fx in fixtures if normalize_text(fx["league"]) in leagues]
    # ha nincs whitelist/fixture lista, semmit se küldjünk (óvatosság)
    return []

# ======== Magyar lokalizáció ========
HU_MAP = {}
def load_hu_map():
    global HU_MAP
    path = os.path.join("config","hu_map.json")
    if not os.path.exists(path): HU_MAP = {}; return
    try:
        with open(path,"r",encoding="utf-8") as f: HU_MAP = json.load(f)
    except Exception: HU_MAP = {}

def hu_name(s: str) -> str:
    if not s: return s
    return HU_MAP.get(s, s)

# ======== CSV log ========
EVENT_FIELDS = ["time","league","match","minute","score","pick","prob","odds","fixture_id","details","market"]
def write_event_row(row):
    if not SAVE_EVENTS: return
    day_dir = os.path.join("data", today_ymd())
    ensure_dir(day_dir)
    path = os.path.join(day_dir,"events.csv")
    exists = os.path.exists(path)
    with open(path,"a",newline="",encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EVENT_FIELDS)
        if not exists: w.writeheader()
        out = {k: row.get(k,"") for k in EVENT_FIELDS}
        w.writerow(out)

# ======== Duplikáció-őr ========
_recent_sent = set()
_recent_queue = deque(maxlen=1500)

def sent_key(fid, market, pick):
    return (str(fid or ""), str(market or "").upper(), str(pick or "").strip().lower())

def already_sent(fid, market, pick):
    return sent_key(fid, market, pick) in _recent_sent

def mark_sent(fid, market, pick):
    k = sent_key(fid, market, pick)
    if k not in _recent_sent:
        _recent_sent.add(k)
        _recent_queue.append(k)
        if len(_recent_queue) == _recent_queue.maxlen:
            old = _recent_queue.popleft()
            if old in _recent_sent: _recent_sent.remove(old)

def preload_sent_keys_today():
    path = os.path.join("data", today_ymd(), "events.csv")
    if not os.path.exists(path): return
    try:
        with open(path,"r",encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                _recent_sent.add(sent_key(row.get("fixture_id",""), row.get("market",""), row.get("pick","")))
    except Exception: pass

# ======== OVER szűrés (4 filter) ========
def over_band_line(total_goals, minute):
    if minute < 25:
        line = 1.5 if total_goals==0 else (2.5 if total_goals==1 else (3.5 if total_goals==2 else 4.5))
    elif minute < 45:
        line = 1.5 if total_goals==0 else (2.5 if total_goals==1 else (3.5 if total_goals==2 else 4.5))
    elif minute < 65:
        if total_goals==0: line = 0.5
        elif total_goals==1: line = 1.5
        elif total_goals==2: line = 2.5
        else: line = 3.5
    elif minute < 80:
        if total_goals==0: line = 0.5
        elif total_goals==1: line = 1.5
        elif total_goals==2: line = 2.5
        else: line = 3.5
    else:
        if total_goals==0: line = 0.5
        elif total_goals==1: line = 1.5
        elif total_goals==2: line = 2.5
        else: return None
    if line <= total_goals: return None
    if line > 5.5: return None
    return line

def prob_heur_over(line, total_goals, minute, sot_total, shots_total):
    base = 0.50
    if minute >= 80: base -= 0.15
    elif minute >= 65: base -= 0.08
    elif minute >= 45: base -= 0.04
    base += min(sot_total,6) * 0.03
    base += min(shots_total,14) * 0.01
    base -= max(0.0, (line - (total_goals + 0.5))) * 0.06
    return max(0.05, min(0.95, base))

def make_over_pick(line):
    if float(line).is_integer(): return f"Over {int(line)} (live)"
    return f"Over {line}".replace(".0","") + " (live)"

# ======== BTTS szűrés ========
def prob_heur_btts(minute, h, a, sot_h, sot_a, shots_total):
    base = 0.30
    if minute < 35: base -= 0.10
    elif minute < 55: base += 0.05
    elif minute < 75: base += 0.10
    else: base += 0.05
    if sot_h >= 2 and sot_a >= 2: base += 0.20
    elif sot_h >= 3 or sot_a >= 3: base += 0.12
    base += min(shots_total, 16) * 0.008
    if h>0 and a>0: return 0.0
    return max(0.05, min(0.95, base))

# ======== HU / TIPP előkészítés ========
HU_MAP = {}
def load_hu_map():
    global HU_MAP
    path = os.path.join("config","hu_map.json")
    if not os.path.exists(path): HU_MAP = {}; return
    try:
        with open(path,"r",encoding="utf-8") as f: HU_MAP = json.load(f)
    except Exception: HU_MAP = {}

def hu_name(s: str) -> str:
    if not s: return s
    return HU_MAP.get(s, s)

# ======== Üzenetküldők ========
EVENT_FIELDS = ["time","league","match","minute","score","pick","prob","odds","fixture_id","details","market"]

def write_event_row(row):
    if not SAVE_EVENTS: return
    day_dir = os.path.join("data", today_ymd())
    ensure_dir(day_dir)
    path = os.path.join(day_dir,"events.csv")
    exists = os.path.exists(path)
    with open(path,"a",newline="",encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EVENT_FIELDS)
        if not exists: w.writeheader()
        out = {k: row.get(k,"") for k in EVENT_FIELDS}
        w.writerow(out)

def send_over_alert(fx, line_pick, odds):
    msg = (
        "⚡ OVER AJÁNLÁS\n"
        f"🏟️ Meccs: {hu_name(fx['match_home'])} – {hu_name(fx['match_away'])} ({fx['score']}, {fx['minute']}' )\n"
        f"🏆 Liga: {hu_name(fx['league'])}\n"
        f"🎯 Tipp: {line_pick}\n"
        f"💰 Odds: {odds}\n"
    )
    tg_send_pub(msg)
    write_event_row({
        "time": now_str(),
        "league": hu_name(fx["league"]),
        "match": f"{hu_name(fx['match_home'])} – {hu_name(fx['match_away'])}",
        "minute": fx["minute"],
        "score": fx["score"],
        "pick": line_pick,
        "prob": "",
        "odds": odds,
        "fixture_id": fx["fixture_id"],
        "details": "",
        "market": "OVER",
    })

def send_btts_alert(fx, odds):
    msg = (
        "⚡ BTTS AJÁNLÁS\n"
        f"🏟️ Meccs: {hu_name(fx['match_home'])} – {hu_name(fx['match_away'])} ({fx['score']}, {fx['minute']}' )\n"
        f"🏆 Liga: {hu_name(fx['league'])}\n"
        f"🎯 Tipp: BTTS – Igen (live)\n"
        f"💰 Odds: {odds}\n"
    )
    tg_send_pub(msg)
    write_event_row({
        "time": now_str(),
        "league": hu_name(fx["league"]),
        "match": f"{hu_name(fx['match_home'])} – {hu_name(fx['match_away'])}",
        "minute": fx["minute"],
        "score": fx["score"],
        "pick": "BTTS Yes (live)",
        "prob": "",
        "odds": odds,
        "fixture_id": fx["fixture_id"],
        "details": "",
        "market": "BTTS",
    })

# ======== Jelek gyártása (live odds-szal) ========
def generate_signals(fixtures):
    """
    Visszaad: list[ (fx, market, pick, odds_float) ]
    - Tippmixpro szűrés
    - perc-szűrés
    - stat + odds (kvóta-óvatos limit)
    """
    # Tipp-only
    fixtures = filter_tipp_only(fixtures)
    # perc
    fixtures = [fx for fx in fixtures if fx["minute"] is not None and MIN_MINUTE <= fx["minute"] <= MAX_MINUTE]

    # limit
    cand = fixtures[:min(len(fixtures), MAX_STATS_LOOKUPS_PER_CYCLE)]

    signals = []
    odds_lookups = 0

    for fx in cand:
        fid = fx["fixture_id"]
        minute = fx["minute"]
        h, a = fx["score_home"], fx["score_away"]
        total = h + a

        # statok
        stats = fetch_fixture_stats(fid) or {}
        hstat, astat = stats.get("home", {}), stats.get("away", {})
        sot_h = int(hstat.get("Shots on Goal") or 0)
        sot_a = int(astat.get("Shots on Goal") or 0)
        shots_h = int(hstat.get("Total Shots") or 0)
        shots_a = int(astat.get("Total Shots") or 0)
        sot_total   = sot_h + sot_a
        shots_total = shots_h + shots_a

        # ===== OVER (4 szűrő) + LIVE ODDS =====
        line = over_band_line(total, minute)
        over_odds = None
        if line:
            # min. lövés/SOT
            pass_sot = (sot_total >= 3) or (shots_total >= 8)
            if minute >= 60:
                pass_sot = (sot_total >= 4) or (shots_total >= 10)

            if pass_sot and odds_lookups < MAX_ODDS_LOOKUPS_PER_CYCLE and ODDS_MODE == "real":
                # odds lekérés
                odds_block = fetch_live_odds_block(fid)
                odds_lookups += 1
                if odds_block:
                    over_odds = parse_over_odds(odds_block, line, ODDS_BOOKMAKER)

            # min odds + value (ha van odds)
            if over_odds is not None:
                if over_odds < OVER_MIN_ODDS:
                    line = None
                else:
                    p = prob_heur_over(line, total, minute, sot_total, shots_total)
                    if over_odds * p < VALUE_THRESHOLD:
                        line = None

        if line and over_odds is not None:
            pick = make_over_pick(line)
            if not (NO_REPEAT_SAME_TIP and already_sent(fid, "OVER", pick)):
                signals.append( (fx, "OVER", pick, over_odds) )

        # ===== BTTS – nagyon szigorú + LIVE ODDS =====
        if not (h>0 and a>0):
            strong_period = (45 <= minute <= 80)
            early_ok = (30 <= minute < 45 and sot_h>=2 and sot_a>=2)
            strong_sot = (sot_h>=2 and sot_a>=2 and sot_total>=5)
            if (h==0 or a==0) and (strong_period or early_ok) and strong_sot:
                btts_odds = None
                if odds_lookups < MAX_ODDS_LOOKUPS_PER_CYCLE and ODDS_MODE == "real":
                    odds_block = fetch_live_odds_block(fid)
                    odds_lookups += 1
                    if odds_block:
                        btts_odds = parse_btts_odds(odds_block, ODDS_BOOKMAKER)

                if btts_odds is not None:
                    if btts_odds < BTTS_MIN_ODDS:
                        strong_sot = False
                    else:
                        p2 = prob_heur_btts(minute, h, a, sot_h, sot_a, shots_total)
                        if btts_odds * p2 < VALUE_THRESHOLD:
                            strong_sot = False

                if strong_sot and btts_odds is not None:
                    if not (NO_REPEAT_SAME_TIP and already_sent(fid, "BTTS", "BTTS Yes (live)")):
                        signals.append( (fx, "BTTS", "BTTS Yes (live)", btts_odds) )

    return signals

# ======== Fő ========
stop_flag = False
def handle_sigint(sig, frame):
    global stop_flag
    stop_flag = True
signal.signal(signal.SIGINT, handle_sigint)

def main():
    ensure_dir("logs"); ensure_dir("data"); ensure_dir("config")
    load_hu_map()
    preload_sent_keys_today()

    if SEND_ONLINE_ON_START:
        tg_send_pub(f"✅ LiveMesterBot online\n🕒 {now_str()}")

    start_ts = now()
    run_until = start_ts + timedelta(minutes=RUN_MINUTES) if RUN_MINUTES>0 else None

    while not stop_flag:
        h = now().hour
        if h < START_HOUR or h >= END_HOUR:
            if DEBUG_LOG:
                print(f"[{now_str()}] ⏸ időablakon kívül ({START_HOUR}-{END_HOUR}) – alvás {POLL_SECONDS}s")
            poll_admin_updates()
            time.sleep(POLL_SECONDS)
            if run_until and now() >= run_until: break
            continue

        used, rem = api_remaining()
        print(f"[{now_str()}] 🔄 ciklus indul...")
        print(f"   📉 RapidAPI maradék ma: {rem:,} / {RAPIDAPI_DAILY_LIMIT:,} (felhasználva: {used:,})")
        maybe_quota_ping(rem)

        fixtures_all = fetch_live_fixtures()
        total = len(fixtures_all)
        # Tippmixpro / perc előszűrés + darabszám-limit
        fixtures = filter_tipp_only(fixtures_all)
        fixtures = [fx for fx in fixtures if fx["minute"] is not None and MIN_MINUTE <= fx["minute"] <= MAX_MINUTE]
        use_list = fixtures[:min(len(fixtures), MAX_FIXTURES_PER_CYCLE)]

        print(f"   ✅ {total} élő meccs (előszűrve: {len(fixtures)})")
        print(f"   🔎 kiválasztva: {len(use_list)}/{total} (limit {MAX_FIXTURES_PER_CYCLE})")

        sent = 0
        for fx, market, pick, odds in generate_signals(use_list):
            if market == "OVER":
                send_over_alert(fx, pick, odds)
                mark_sent(fx["fixture_id"], market, pick)
                sent += 1
            elif market == "BTTS":
                send_btts_alert(fx, odds)
                mark_sent(fx["fixture_id"], market, pick)
                sent += 1

        print(f"   📈 jelek száma: {sent}")
        if sent == 0: print("   💤 nincs erős jel ebben a ciklusban")

        poll_admin_updates()
        current_poll = POLL_SECONDS
        if rem <= RAPIDAPI_SAFETY_RESERVE:
            current_poll = max(current_poll, LOW_BUDGET_POLL_SECONDS)
            print(f"   ⚠️ Alacsony keret → polling lassítás: {current_poll}s")
        print(f"   ⏳ alvás {current_poll}s\n")

        for _ in range(int(max(1,current_poll))):
            if stop_flag: break
            time.sleep(1)
        if run_until and now() >= run_until:
            print(f"[{now_str()}] ⏹ időkorlát elérve – futás vége")
            break

if __name__ == "__main__":
    main()
