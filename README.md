# IPTV Cast

Een app voor je gsm (PWA) die je Xtream-IPTV naar je Chromecast stuurt, met ondertitels.

- **Films en series**: ondertitels van OpenSubtitles (Nederlands en Engels), of die uit de stream zelf (knop CC tijdens het kijken). Onthoudt waar je gebleven was.
- **Live tv**: zenders per categorie, zoeken, één tik en het speelt af op tv.
- Bediening op je gsm: pauze, spoelen, volume, ondertitels aan/uit/groter.

## Hoe het werkt

```
gsm (app) ──► Cloudflare Worker ──► jouw IPTV-server (http)
                    ▲
Chromecast ─────────┘   (live tv en ondertitels lopen via de Worker,
                         films indien nodig ook)
```

De app draait op https (GitHub Pages). Je IPTV-server draait op http, en een https-app mag niet met een http-server praten. De Chromecast heeft voor live tv en ondertitels ook speciale headers nodig (CORS) die IPTV-servers meestal niet meesturen. De Worker is een klein gratis stukje code bij Cloudflare dat dit oplost.

## Installeren (eenmalig)

### 1. OpenSubtitles (voor ondertitels)
1. Maak een gratis account op https://www.opensubtitles.com.
2. Ga naar je profiel › **API consumers** › **New consumer**, geef een naam (bv. IPTV Cast) en kopieer de **API key**.

### 2. Cloudflare Worker (de tussenschakel)
1. Maak een gratis account op https://dash.cloudflare.com.
2. Ga naar **Workers & Pages** › **Create** › **Create Worker**, noem hem `iptv-cast` en klik **Deploy**.
3. Klik **Edit code**, vervang alles door de inhoud van [`worker/worker.js`](worker/worker.js) en klik **Deploy**.
4. Ga naar **Settings** › **Variables and Secrets** en voeg toe (type *Secret*):
   | Naam | Waarde |
   |---|---|
   | `APP_KEY` | een lange zelfverzonnen code (bv. 30 willekeurige letters en cijfers) |
   | `OS_API_KEY` | de API key van OpenSubtitles |
   | `OS_USERNAME` | je OpenSubtitles-gebruikersnaam (optioneel, geeft meer downloads per dag) |
   | `OS_PASSWORD` | je OpenSubtitles-wachtwoord (optioneel) |
5. Noteer het adres van je Worker, bv. `https://iptv-cast.jouwnaam.workers.dev`.

### 3. De app op je gsm
1. Open de app-link in **Chrome** op je gsm.
2. Ga naar **Instellingen** en vul in: serveradres, gebruikersnaam en wachtwoord van je Xtream-login, plus het Worker-adres en je `APP_KEY`.
3. Tik **Opslaan en verbinding testen**.
4. Chrome-menu (⋮) › **Toevoegen aan startscherm**.

Je gegevens blijven alleen op je gsm (in de app) en gaan enkel naar je eigen Worker.

## Goed om te weten
- Je gsm en Chromecast moeten op hetzelfde wifi-netwerk zitten. Casten vanuit een web-app werkt enkel in **Chrome op Android** (niet op iPhone).
- De Chromecast speelt **mp4** het best af. **mkv** lukt enkel op nieuwere modellen (Chromecast met Google TV). Ondertitels die *in* een mkv-bestand zitten, kan de Chromecast niet tonen; gebruik dan OpenSubtitles.
- Live tv vraagt dat je abonnement het formaat **m3u8** toestaat (dat zie je na "verbinding testen").
- Gratis Cloudflare-plan: 100.000 verzoeken per dag. Dat volstaat ruim voor live tv en ondertitels. Films gaan standaard rechtstreeks naar de Chromecast en alleen via de Worker als dat niet lukt.
- Sommige IPTV-providers blokkeren verzoeken van servers zoals Cloudflare. Krijg je in Instellingen een fout terwijl je login klopt, dan is dat waarschijnlijk de oorzaak.

## Versies: dev, productie en terugzetten

| | Map | Branch | Adres |
|---|---|---|---|
| Ontwikkeling | `iptv-cast-dev` | `dev` | https://maartenhansen.github.io/iptv-cast-dev/ (oranje DEV-label) |
| Productie | `iptv-cast` | `main` | https://stream.hansen-tools.be |

- Nieuwe dingen worden altijd eerst in **dev** gebouwd en getest (`py dev/deploy_dev.py` zet de dev-site online).
- **In productie zetten** als nieuwe versie (in PowerShell, vanuit de map `iptv-cast`):
  `py tools\release.py 1.2.0`
  Dit maakt van de dev-versie versie 1.2.0, zet ze online (app, Worker en thuis-tussenschakel) en schrijft de wijzigingen in [CHANGELOG.md](CHANGELOG.md).
- **Terugzetten** naar een eerdere versie:
  `py tools\rollback.py` toont alle versies, `py tools\rollback.py 1.1.0` zet de productie terug naar 1.1.0.
  Er gaat niets verloren: later gewoon opnieuw `release.py` (of `rollback.py` naar een nieuwere versie).
- Het versienummer van de app staat onderaan bij **Instellingen**.

## Lokaal testen (zonder echte login)
```
py dev/dev_server.py
```
Open http://localhost:8010 en vul als Worker-adres `http://localhost:8010` in en als sleutel `dev`. De gegevens zijn verzonnen; casten kan hier niet.
