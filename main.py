
import time, json, os, requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")
MIN_TIP_INTERVAL = int(os.getenv("MIN_TIP_INTERVAL", 600))
SENT_TIPS_FILE = "sent_tips.json"

last_tip_time = 0

def send_telegram_message(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    requests.post(url, data=data)

def load_sent_ids():
    if os.path.exists(SENT_TIPS_FILE):
        with open(SENT_TIPS_FILE) as f:
            return set(json.load(f))
    return set()

def save_sent_ids(ids):
    with open(SENT_TIPS_FILE, "w") as f:
        json.dump(list(ids), f)

sent_ids = load_sent_ids()

while True:
    # Szimuláció (élesnél itt jönne API lekérés)
    fixture_id = 123456  # itt cserélnéd ki élő meccs id-re
    if fixture_id in sent_ids:
        print("🚫 Tipp már elküldve korábban.")
    elif time.time() - last_tip_time < MIN_TIP_INTERVAL:
        print("⏱ Túl korai lenne új tipp, kihagyva.")
    else:
        send_telegram_message("🎯 Új tipp: Team A vs Team B — Gólt szerez következőként a hazai!")
        sent_ids.add(fixture_id)
        save_sent_ids(sent_ids)
        last_tip_time = time.time()
    time.sleep(10)
