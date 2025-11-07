# LiveMesterBot (TEST) – GitHub-ready

Valós idejű, élő foci-meccs figyelő és value jelző bot (Telegram értesítésekkel).
Ez a repo **GitHub-kompatibilis**, nincsenek benne titkok; a kulcsokat `.env`-ben vagy GitHub Secrets-ben kell megadni.

## Funkciók (v2.1)
- RapidAPI → API-Football integráció
- Hívásszegény üzem: aktív időablak, csúcsidő gyorsítás, Top-N statlekérés, per-fixture cooldown, 429 backoff
- Budapest időzóna, 3 perces alap ciklus (csúcsban 90 mp)
- Telegram üzenetküldés
- Naplózás CSV-be

## Gyors indítás (lokálisan, Windows)
```bash
git clone <REPO_URL>
cd LiveMesterBot
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
copy .env.template .env
# Szerkeszd a .env fájlt (kulcsok + chat_id)
python livemesterbot.py
```

> **Megjegyzés:** A bot 10:00–23:00 között aktív, csúcsidőben (18–22) gyorsabb ciklussal fut.

## Környezeti változók (.env)
Másold a `.env.template` fájlt `.env` néven és töltsd ki:

```
# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# RapidAPI (API-Football)
RAPIDAPI_KEY=
RAPIDAPI_HOST=api-football-v1.p.rapidapi.com

# Időzítés és limitek
POLL_SECONDS=180
ACTIVE_HOURS_START=10:00
ACTIVE_HOURS_END=23:00
PEAK_HOURS_START=18:00
PEAK_HOURS_END=22:00
PEAK_POLL_SECONDS=90
MAX_FIXTURES_PER_CYCLE=10
PEAK_MAX_FIXTURES_PER_CYCLE=15
STATS_COOLDOWN_MIN=6

TIMEZONE=Europe/Budapest
```

## GitHub Actions (időzített futtatás – nem 24/7)
A folyamatos élő futtatáshoz **VPS/felhő** ajánlott. Időzített batch futásokhoz használhatod az alábbi workflow-t:

1. Tedd a fájlt `.github/workflows/bot.yml` néven a repóba.
2. Add hozzá a **Secrets**-et a repóban:
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `RAPIDAPI_KEY`

```yaml
name: LiveMesterBot (batch)
on:
  schedule:
    - cron: "*/15 9-22 * * *"   # 15 percenként 10:00–23:00 CET körül
  workflow_dispatch:

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: python livemesterbot.py
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          RAPIDAPI_KEY: ${{ secrets.RAPIDAPI_KEY }}
          RAPIDAPI_HOST: api-football-v1.p.rapidapi.com
          POLL_SECONDS: 60
          ACTIVE_HOURS_START: "10:00"
          ACTIVE_HOURS_END: "23:00"
          PEAK_HOURS_START: "18:00"
          PEAK_HOURS_END: "22:00"
          PEAK_POLL_SECONDS: 60
          MAX_FIXTURES_PER_CYCLE: 5
          PEAK_MAX_FIXTURES_PER_CYCLE: 8
          STATS_COOLDOWN_MIN: 6
          TIMEZONE: Europe/Budapest
```

> **Figyelem:** Actions jobok max. ~6 óráig futhatnak folyamatosan; ez nem 24/7 bot.

## Render / Railway (24/7 futtatás)
- Kapcsold össze a repót a szolgáltatással.
- Állítsd be az **Environment Variables**-t a `.env` alapján.
- Start command: `python livemesterbot.py`

## Biztonság
- **Soha** ne commitold a `.env`-et.
- Titkok mindig a helyi `.env`-ben vagy GitHub Secrets-ben legyenek.
- Ha gyanús aktivitást látsz, azonnal *rotáld* a kulcsokat.

## Licenc
Zárt / privát használatra. További terjesztéshez egyeztessünk.
