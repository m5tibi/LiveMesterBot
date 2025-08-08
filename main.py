
import time, json, os, requests
from datetime import datetime
from pytz import timezone
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")
MIN_TIP_INTERVAL = int(os.getenv("MIN_TIP_INTERVAL", 600))
SENT_TIPS_FILE = "sent_tips.json"
STATS_FILE = "stats.json"
BUDAPEST_TZ = timezone("Europe/Budapest")

last_tip_time = 0

def send_telegram_message(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    requests.post(url, data=data)

def load_json(filename):
    if os.path.exists(filename):
        with open(filename) as f:
            return json.load(f)
    return [] if filename == STATS_FILE else set()

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

sent_ids = set(load_json(SENT_TIPS_FILE))
stats = load_json(STATS_FILE)

def log_stat(fixture_id, match, tip_text):
    timestamp = datetime.now(BUDAPEST_TZ).strftime("%Y-%m-%d %H:%M")
    stats.append({
        "id": fixture_id,
        "match": match,
        "tip": tip_text,
        "time": timestamp,
        "result": "pending"
    })
    save_json(STATS_FILE, stats)

while True:
    fixture_id = 123456
    match = "Ferencváros vs MTK"
    tip_text = "2. félidőben több mint 1 gól"

    if fixture_id in sent_ids:
        print("🚫 Tipp már elküldve korábban.")
    elif time.time() - last_tip_time < MIN_TIP_INTERVAL:
        print("⏱ Túl korai lenne új tipp, kihagyva.")
    else:
        msg = f"⏸ Félidős tipp!\\ Meccs: {match}\\n🔮 Tipp: {tip_text}\\n🕒 Tipp időpontja: {datetime.now(BUDAPEST_TZ).strftime('%Y-%m-%d %H:%M')}"
🏟 Meccs: {match}
🔮 Tipp: {tip_text}
🕒 Tipp időpontja: {datetime.now(BUDAPEST_TZ).strftime('%Y-%m-%d %H:%M')}"
        send_telegram_message(msg)
        sent_ids.add(fixture_id)
        save_json(SENT_TIPS_FILE, list(sent_ids))
        log_stat(fixture_id, match, tip_text)
        last_tip_time = time.time()
    time.sleep(10)
