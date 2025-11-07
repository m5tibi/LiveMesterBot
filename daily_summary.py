import os
import csv
from datetime import datetime
import pytz
import requests
from collections import Counter, defaultdict

TZ = pytz.timezone("Europe/Budapest")
today = datetime.now(TZ).strftime("%Y-%m-%d")
path = f"data/{today}/events.csv"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN","").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID","").strip()

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram token/chat_id missing – summary to console only.")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=15)
    if r.status_code != 200:
        print(f"Telegram error: {r.status_code} {r.text}")

def human_pct(n, d):
    return f"{(n/d*100):.1f}%" if d else "–"

def main():
    if not os.path.exists(path):
        send_telegram(f"🧾 <b>Napi összesítő – {today}</b>\nMa nem keletkezett napló (nincs data).")
        return

    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(row)

    total = len(rows)
    by_pick = Counter(r.get("pick","") for r in rows)
    by_league = Counter(r.get("league","") for r in rows)

    # percek eloszlása (10-es csoportok)
    buckets = defaultdict(int)
    for r in rows:
        try:
            m = int(r.get("minute","0") or 0)
        except:
            m = 0
        bucket = f"{(m//10)*10:02d}–{((m//10)*10)+9:02d}'"
        buckets[bucket] += 1
    buckets_sorted = sorted(buckets.items(), key=lambda x: x[0])

    # Üzenet összeállítás
    lines = []
    lines.append(f"🧾 <b>Napi összesítő – {today}</b>")
    lines.append(f"Összes élő value jel: <b>{total}</b>")

    if total > 0:
        # TOP pickek
        top_picks = ", ".join([f"{k} ({v})" for k,v in by_pick.most_common(3)])
        lines.append(f"Top tipptípusok: {top_picks if top_picks else '–'}")

        # TOP ligák
        top_leagues = ", ".join([f"{k} ({v})" for k,v in by_league.most_common(3)])
        lines.append(f"Top ligák: {top_leagues if top_leagues else '–'}")

        # Idősávok
        bucket_str = ", ".join([f"{k} {v}x" for k,v in buckets_sorted if v>0])
        if bucket_str:
            lines.append(f"Idősáv eloszlás: {bucket_str}")

        # Minta: utolsó 3 jel
        last3 = rows[-3:] if total>=3 else rows
        if last3:
            lines.append("")
            lines.append("<b>Utolsó jelek</b>:")
            for r in last3:
                lines.append(f"• {r.get('time','')} | {r.get('match','')} ({r.get('score','')}, {r.get('minute','')}') – {r.get('pick','')} @ {r.get('odds','')}")

    msg = "\n".join(lines)
    send_telegram(msg)

if __name__ == "__main__":
    main()
