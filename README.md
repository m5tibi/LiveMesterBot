# LiveMesterBot Cloud-ready package (v1)

## Tartalom
- main.py : a bot fő scriptje (szimulációs mód + API-Football integráció)
- requirements.txt : szükséges könyvtárak
- Procfile : Railway/Heroku futtatáshoz
- .env : konfiguráció (tokenek és kulcsok)

## Telepítés (röviden)
1. Hozz létre egy új GitHub repo-t és töltsd fel a csomagot.
2. Menj Railway.app -> New Project -> Deploy from GitHub Repo
3. Állítsd be a környezeti változókat (ha nem szeretnéd a .env-ben tárolni): BOT_TOKEN, CHAT_ID, API_FOOTBALL_KEY
4. A bot indulása után a szimulációs üzenetek megjelennek, majd a valós API-adatokat figyeli.

## Megjegyzés
- A csomag tartalmaz egy szimulációs indítást, hogy azonnal láss teszt üzeneteket a csatornádon.
- A valódi tipp logika demonstrációs jellegű; éles használat előtt javasolt finomhangolás.
