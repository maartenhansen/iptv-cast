"""Testserver voor lokaal uitproberen, ZONDER echte IPTV-login.

Serveert de app op http://localhost:8010 en doet zich voor als de Worker
(/api, /subs/search, /subs/file) met verzonnen films, series en zenders.
Vul in de app bij Instellingen als Worker-adres http://localhost:8010 in
en als sleutel "dev". Casten zelf kan hier niet getest worden.

Starten:  py dev/dev_server.py
"""
import json, os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEV = os.path.basename(ROOT).endswith("-dev")  # map iptv-cast-dev = ontwikkelversie
PORT = 8011 if DEV else 8010

LIVE_CATS = [{"category_id": str(i), "category_name": n} for i, n in enumerate(["NL | Algemeen", "BE | Vlaams", "Sport"], 1)]
VOD_CATS = [{"category_id": str(i), "category_name": n} for i, n in enumerate(["Nieuw toegevoegd", "Actie", "Komedie"], 10)]
SER_CATS = [{"category_id": str(i), "category_name": n} for i, n in enumerate(["Drama", "Documentaires"], 20)]

LIVE = [{"stream_id": c * 100 + j, "name": f"{cat['category_name'].split('|')[-1].strip()} Kanaal {j}",
         "stream_icon": "", "category_id": cat["category_id"]}
        for c, cat in enumerate(LIVE_CATS, 1) for j in range(1, 9)]
VOD = [{"stream_id": 1000 + c * 100 + j, "name": f"NL | Voorbeeldfilm {cat['category_name']} {j} ({2015 + j % 10})",
        "stream_icon": "", "container_extension": "mp4" if j % 3 else "mkv", "category_id": cat["category_id"]}
       for c, cat in enumerate(VOD_CATS, 1) for j in range(1, 25)]
SERIES = [{"series_id": 5000 + c * 10 + j, "name": f"Voorbeeldserie {cat['category_name']} {j}", "cover": "",
           "tmdb": "", "category_id": cat["category_id"]}
          for c, cat in enumerate(SER_CATS, 1) for j in range(1, 7)]


def api(q):
    a = q.get("action", [""])[0]
    cat = q.get("category_id", [""])[0]
    pick = lambda lst: [x for x in lst if not cat or x["category_id"] == cat]
    if a == "":
        return {"user_info": {"auth": 1, "status": "Active", "exp_date": "1798761600", "max_connections": "2",
                              "allowed_output_formats": ["m3u8", "ts"]}, "server_info": {}}
    if a == "get_live_categories": return LIVE_CATS
    if a == "get_vod_categories": return VOD_CATS
    if a == "get_series_categories": return SER_CATS
    if a == "get_live_streams": return pick(LIVE)
    if a == "get_vod_streams": return pick(VOD)
    if a == "get_series": return pick(SERIES)
    if a == "get_vod_info":
        v = next((x for x in VOD if str(x["stream_id"]) == q.get("vod_id", [""])[0]), VOD[0])
        return {"info": {"name": v["name"], "plot": "Een verzonnen film om de app mee te testen.", "genre": "Drama",
                         "duration": "01:52:00", "releasedate": "2021-05-01", "tmdb_id": "603"},
                "movie_data": {"stream_id": v["stream_id"], "name": v["name"], "container_extension": v["container_extension"]}}
    if a == "get_series_info":
        s = next((x for x in SERIES if str(x["series_id"]) == q.get("series_id", [""])[0]), SERIES[0])
        eps = {str(se): [{"id": f"{s['series_id']}{se}{e:02d}", "episode_num": e, "season": se,
                          "title": f"Aflevering {e}", "container_extension": "mp4",
                          "info": {"plot": "Verzonnen aflevering.", "duration": "00:45:00"}}
                         for e in range(1, 7)] for se in (1, 2)}
        return {"info": {"name": s["name"], "plot": "Een verzonnen serie.", "genre": "Drama", "tmdb": "1399",
                         "releaseDate": "2019-01-01"}, "episodes": eps}
    return {"error": "onbekende actie"}


SUBS = [{"id": 111, "lang": "nl", "release": "Voorbeeld.1080p.WEB-DL", "downloads": 900, "hi": False},
        {"id": 112, "lang": "nl", "release": "Voorbeeld.720p.BluRay", "downloads": 300, "hi": False},
        {"id": 211, "lang": "en", "release": "Example.1080p.WEB-DL", "downloads": 5000, "hi": True}]


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw): super().__init__(*a, directory=ROOT, **kw)

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query, keep_blank_values=True)
        if u.path in ("/api", "/subs/search", "/subs/file", "/p", "/m3u8") and q.get("k", [""])[0] != "dev":
            return self.send_json({"error": "Verkeerde sleutel"}, 403)
        if u.path == "/api": return self.send_json(api(q))
        if u.path == "/subs/search": return self.send_json(SUBS)
        if u.path == "/subs/file":
            b = b"WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nTestondertitel\n"
            self.send_response(200); self.send_header("Content-Type", "text/vtt"); self.end_headers(); return self.wfile.write(b)
        if u.path in ("/p", "/m3u8"): return self.send_json({"error": "niet beschikbaar in testserver"}, 404)
        return super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    print(f"Testserver op http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
