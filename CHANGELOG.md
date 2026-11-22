# Versies van IPTV Cast

## v1.1.0 (2026-11-22)
- Dev-omgeving: Worker-config prod/dev, dev-stand opstartprogramma (poort 8031), publicatiescript dev-site
- Live tv kijken op de gsm (hls.js), via Worker -> tunnel -> Beelink
- Live tv: zenderlijst zoals voorheen, tik geeft keuze casten of kijken op gsm
- Live kijken: duidelijkere foutmelding als de Beelink/VPN niet bereikbaar is
- Live tv: keuze casten of openen in VLC; kijken in de app (via Beelink) weer verwijderd
- Films en series: knop Openen in VLC naast casten
- Service worker: altijd nieuwste versie ophalen (geen verouderde browsercache)
- Versiebeheer: versienummer in de app, CHANGELOG, tools/release.py en tools/rollback.py; hulpscripts herkennen dev zelf
- Versiebeheer: korte tagomschrijving, uitleg in README

## v1.0.0 (2026-11-21)
- Eerste productieversie op stream.hansen-tools.be: films, series en live tv casten naar de Chromecast via de Beelink (met VPN), met geluid, ingebouwde en OpenSubtitles-ondertitels.