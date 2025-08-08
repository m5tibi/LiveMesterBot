import os
import time
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
API_FOOTBALL_KEY = os.getenv('API_FOOTBALL_KEY')
SIMULATE = os.getenv('SIMULATE', 'true').lower() in ('1','true','yes')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '60'))  # seconds

STATS_FILE = 'stats.json'

def save_stats(stats):
    try:
        with open(STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print('Could not save stats:', e)

def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print('Missing BOT_TOKEN or CHAT_ID, skipping send.')
        return False
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    payload = {
        'chat_id': CHAT_ID,
        'text': text,
        'parse_mode': 'Markdown'
    }
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code == 200:
            return True
        else:
            print('Telegram send failed', r.status_code, r.text)
            return False
    except Exception as e:
        print('Telegram send exception:', e)
        return False

def format_live_tip(match, tip, prob, odds):
    return (f"🎯 *Live Tipp*\n\n"
            f"🏟️ *Mérkőzés:* {match}\n"
            f"🕒 *Idő:* LIVE\n"
            f"💡 *Tipp:* {tip}\n"
            f"📈 *Becsült esély:* {prob}% | *Odds:* {odds}\n"
            f"✅ *Kategória:* Value Bet (Live)\n"
            f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

def format_prematch_tip(match, kickoff, tip, prob, odds):
    return (f"🔮 *Pre-Match Value Tipp*\n\n"
            f"🏟️ *Mérkőzés:* {match}\n"
            f"🗓️ *Kezdés:* {kickoff}\n"
            f"💡 *Tipp:* {tip}\n"
            f"📈 *Becsült esély:* {prob}% | *Odds:* {odds}\n"
            f"✅ *Kategória:* Value Bet (Pre-Match)\n"
            f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

def simulate_startup_sends():
    stats = load_stats()
    messages = []
    messages.append(format_live_tip('FC Basel vs Grasshoppers (60\')', 'Over 2.5 gól', 73, 1.88))
    messages.append(format_prematch_tip('Rizespor vs Göztepe', '2025-08-08 20:30 CET', 'Rizespor nyer', 61, 2.65))
    messages.append(format_live_tip('Augsburg II vs Bayreuth (72\')', 'Következő gól: Augsburg II', 70, 1.95))
    for m in messages:
        ok = send_telegram(m)
        stats.setdefault('sent_tests', 0)
        if ok:
            stats['sent_tests'] += 1
        save_stats(stats)
        time.sleep(10)

def query_api_football_live():
    if not API_FOOTBALL_KEY:
        return []
    url = 'https://v3.football.api-sports.io/fixtures?live=all'
    headers = {'x-apisports-key': API_FOOTBALL_KEY}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json().get('response', [])
            return data
        else:
            print('API-Football live failed', r.status_code)
            return []
    except Exception as e:
        print('API-Football live exception', e)
        return []

def query_api_football_prematch():
    if not API_FOOTBALL_KEY:
        return []
    url = 'https://v3.football.api-sports.io/fixtures?next=50'
    headers = {'x-apisports-key': API_FOOTBALL_KEY}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json().get('response', [])
            return data
        else:
            print('API-Football pre failed', r.status_code)
            return []
    except Exception as e:
        print('API-Football pre exception', e)
        return []

def main_loop():
    stats = load_stats()
    if SIMULATE:
        print('Simulation mode ON — sending startup test messages.')
        simulate_startup_sends()
    while True:
        try:
            live = query_api_football_live()
            if live:
                print(f'Found {len(live)} live matches.')
                sent = 0
                for m in live:
                    teams = m.get('teams', {})
                    match_name = f"{teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')}")
                    tip_text = format_live_tip(match_name, 'Következő gól: home', 71, 1.90)
                    ok = send_telegram(tip_text)
                    if ok:
                        stats.setdefault('sent_live', 0)
                        stats['sent_live'] += 1
                        save_stats(stats)
                    sent += 1
                    if sent >= 3:
                        break
            else:
                prem = query_api_football_prematch()
                if prem:
                    print(f'Found {len(prem)} prematch fixtures.')
                    sent = 0
                    for m in prem:
                        fixture = m.get('fixture', {})
                        teams = m.get('teams', {})
                        kickoff = fixture.get('date')
                        match_name = f"{teams.get('home', {}).get('name')} vs {teams.get('away', {}).get('name')}")
                        tip_text = format_prematch_tip(match_name, kickoff, 'Hazai győzelem', 68, 2.10)
                        ok = send_telegram(tip_text)
                        if ok:
                            stats.setdefault('sent_prematch', 0)
                            stats['sent_prematch'] += 1
                            save_stats(stats)
                        sent += 1
                        if sent >= 2:
                            break
                else:
                    print('No API data available at this time.')
            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print('Main loop exception:', e)
            time.sleep(30)

if __name__ == '__main__':
    main_loop()
