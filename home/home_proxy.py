"""IPTV Cast: thuis-tussenschakel voor de Beelink.

Waarom? Je IPTV-provider blokkeert Belgische adressen en weigert streams via
Cloudflare. Daarom haalt de Beelink (met de VPN aan) de streams op en geeft
ze door aan de Chromecast, binnen je thuisnetwerk.

    Chromecast ──(thuisnetwerk)──► Beelink :8030 ──(VPN)──► IPTV-server

Endpoints (allemaal met ?k=<APP_KEY>, dezelfde sleutel als de Worker):
    /            status (zonder sleutel)
    /m3u8?url=   HLS-afspeellijst, met alle links herschreven via deze Beelink
    /p?url=      bestand doorgeven (videostukjes, films), met vooruit/terugspoelen
    /api?s=&...  lijsten (player_api.php) via de VPN; de Worker vraagt dit via de tunnel op
    /hls/play?url=&start=   film omzetten (geluid -> AAC, ondertitels -> WebVTT) als HLS
    /hls/<id>/<bestand>     stukjes van die omzetting (id is geheim, geen sleutel nodig)

Starten:  py home/home_proxy.py
"""
import atexit, ipaddress, json, os, re, socket, sys, threading, time, urllib.error, urllib.parse, urllib.request
import convert
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get("IPTV_HOME_PORT", "8030"))
HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, "..", "worker", "secrets.env")
UA = "VLC/3.0.20 LibVLC/3.0.20"  # wordt aanvaard door je provider (getest)
CHUNK = 64 * 1024


def load_key():
    key = os.environ.get("IPTV_APP_KEY", "")
    if not key:
        try:
            for line in open(SECRETS, encoding="utf-8"):
                if line.startswith("APP_KEY="):
                    key = line.split("=", 1)[1].strip()
        except OSError:
            pass
    return key


APP_KEY = load_key()

# Je abonnement staat maar 1 verbinding toe. Als de Chromecast voor dezelfde film
# een nieuw stuk vraagt (bv. bij spoelen), sluiten we eerst de vorige verbinding.
_active = {}
_lock = threading.Lock()


def take_slot(key, resp):
    with _lock:
        old = _active.get(key)
        _active[key] = resp
    if old is not None:
        try:
            old.close()
        except Exception:
            pass


def release_slot(key, resp):
    with _lock:
        if _active.get(key) is resp:
            del _active[key]


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def redact(url):
    """Laat gebruikersnaam/wachtwoord weg uit logregels."""
    return re.sub(r"/(live|movie|series)/[^/]+/[^/]+/", r"/\1/•••/•••/", url)


# ---------- Beveiliging 1: niets binnen je thuisnetwerk opvragen ----------
# Afspeellijsten komen van de provider. Zonder deze controle zou een link in zo'n
# lijst de Beelink iets in je eigen netwerk kunnen laten opvragen (bv. je router).

def is_public_host(host):
    """True als ALLE adressen van deze naam publieke internetadressen zijn."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified or not ip.is_global):
            return False
    return True


class Blocked(Exception):
    pass


def check_target(url):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https"):
        raise Blocked("Alleen http/https is toegestaan")
    if os.environ.get("IPTV_ALLOW_PRIVATE") == "1":
        return  # enkel voor lokale tests met een testfilm
    if not is_public_host(u.hostname):
        raise Blocked("Adres binnen het thuisnetwerk (of onbekend) geweigerd")


class SafeRedirects(urllib.request.HTTPRedirectHandler):
    """Ook bij doorverwijzingen controleren we elk nieuw adres."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_target(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(SafeRedirects)


# ---------- Beveiliging 2: niets ophalen als de VPN uitstaat ----------
# We kijken elke 20 seconden uit welk land de verbinding van deze pc komt.
# Is dat het geblokkeerde land (België) of lukt de controle niet, dan weigeren we
# streams, zodat je thuisadres nooit bij de provider terechtkomt.

BLOCKED_COUNTRY = os.environ.get("IPTV_BLOCKED_COUNTRY", "BE").upper()
VPN_CHECK_EVERY = 20
_vpn = {"country": None, "checked": 0.0}


def check_country():
    try:
        req = urllib.request.Request("https://www.cloudflare.com/cdn-cgi/trace",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            t = dict(l.split("=", 1) for l in r.read().decode().splitlines() if "=" in l)
        country = t.get("loc") or None
    except Exception:
        country = None
    changed = country != _vpn["country"]
    _vpn.update(country=country, checked=time.time())
    if changed:
        if vpn_ok():
            log(f"VPN in orde: verbinding komt uit {country}")
        else:
            log(f"VPN UIT of onbekend (land: {country or '?'}): streams worden geweigerd")
    return country


def vpn_ok():
    c = _vpn["country"]
    return bool(c) and c != BLOCKED_COUNTRY


def vpn_watch():
    while True:
        check_country()
        time.sleep(VPN_CHECK_EVERY)



class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---- hulpjes ----
    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")
        # Chrome (Private Network Access): toestaan dat een https-pagina dit thuisadres aanspreekt
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def reply(self, code, body, ctype="text/plain; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            return self.reply(200, json.dumps({"ok": True, "app": "iptv-cast-home", "vpn": vpn_ok(),
                                               "ffmpeg": bool(convert.FFMPEG)}), "application/json")
        m = re.fullmatch(r"/hls/([0-9a-f]{32})/([^/]+)", u.path)
        if m:
            return self.hls_file(m.group(1), m.group(2))
        if not APP_KEY or q.get("k", [""])[0] != APP_KEY:
            return self.reply(403, '{"error":"Verkeerde sleutel"}', "application/json")
        if u.path == "/api":
            return self.api(q)
        target = q.get("url", [""])[0]
        if not re.match(r"^https?://", target):
            return self.reply(400, '{"error":"Ongeldige url"}', "application/json")
        if time.time() - _vpn["checked"] > VPN_CHECK_EVERY:
            check_country()
        if not vpn_ok():
            return self.reply(503, '{"error":"VPN staat uit op de Beelink: zet de VPN aan"}', "application/json")
        try:
            check_target(target)
        except Blocked as e:
            log(f"geweigerd: {e}")
            return self.reply(403, json.dumps({"error": str(e)}), "application/json")
        try:
            if u.path == "/m3u8":
                return self.playlist(target, q["k"][0])
            if u.path == "/p":
                return self.passthrough(target)
            if u.path == "/hls/play":
                return self.hls_play(target, q)
        except Blocked as e:
            log(f"geweigerd: {e}")
            return self.reply(403, json.dumps({"error": str(e)}), "application/json")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return  # Chromecast heeft afgehaakt (bv. gestopt of gespoeld)
        except Exception as e:
            log(f"fout: {e}")
            try:
                return self.reply(502, '{"error":"%s"}' % str(e).replace('"', "'"), "application/json")
            except Exception:
                return
        self.reply(404, '{"error":"Onbekend adres"}', "application/json")

    # ---- lijsten (voor de Worker, via de tunnel) ----
    def api(self, q):
        server = q.get("s", [""])[0].rstrip("/")
        if not re.match(r"^https?://", server):
            return self.reply(400, '{"error":"Ongeldig serveradres"}', "application/json")
        if time.time() - _vpn["checked"] > VPN_CHECK_EVERY:
            check_country()
        if not vpn_ok():
            return self.reply(503, '{"error":"VPN staat uit op de Beelink"}', "application/json")
        try:
            check_target(server)
        except Blocked as e:
            return self.reply(403, json.dumps({"error": str(e)}), "application/json")
        params = [(k, v) for k, vals in q.items() if k not in ("s", "k") for v in vals]
        req = urllib.request.Request(f"{server}/player_api.php?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": UA})
        try:
            with OPENER.open(req, timeout=60) as r:
                body, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            body, code = e.read(2000), e.code
        except Blocked as e:
            return self.reply(403, json.dumps({"error": str(e)}), "application/json")
        except Exception as e:
            log(f"lijst mislukt ({type(e).__name__})")
            return self.reply(502, json.dumps({"error": f"IPTV-server gaf geen antwoord ({type(e).__name__})"}), "application/json")
        if code != 200:
            log(f"lijst: server gaf {code}")
        try:
            self.reply(code, body, "application/json; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    # ---- omzetting met ffmpeg (geluid en ondertitels voor de Chromecast) ----
    def hls_play(self, target, q):
        try:
            start = max(0, int(float(q.get("start", ["0"])[0] or 0)))
        except ValueError:
            start = 0
        key = q["k"][0]
        # ffmpeg leest via onze eigen /p-route: zo gelden alle beveiligingen ook voor ffmpeg
        input_url = f"http://127.0.0.1:{PORT}/p?" + urllib.parse.urlencode({"k": key, "url": target})
        try:
            langs = [x for x in re.split(r"[,\s]+", q.get("langs", ["nl,en"])[0].lower()) if re.fullmatch(r"[a-z]{2}", x)]
            try:
                manual = max(-30.0, min(30.0, float(q.get("subdelay", ["0"])[0] or 0)))
            except ValueError:
                manual = 0.0
            sess = convert.get_or_start(target, start, input_url, log, langs, manual)
        except Exception as e:
            log(f"omzetting mislukt: {e}")
            return self.reply(502, json.dumps({"error": f"Omzetting mislukt: {e}"}), "application/json")
        if not convert.wait_ready(sess):
            log(f"omzetting kwam niet op gang: {redact(sess.error) or '?'}")
            return self.reply(502, json.dumps({"error": "Omzetting kwam niet op gang"}), "application/json")
        host = self.headers.get("Host") or f"{lan_ip()}:{PORT}"
        body = convert.master_playlist(sess, f"http://{host}/hls/{sess.sid}")
        gap = convert.measure_gap(sess)
        if gap or sess.manual:
            log(f"ondertitels: {gap:.2f}s naar voren (beeld begint vóór het geluid), handmatig {sess.manual:+.1f}s")
        self.reply(200, body, "application/vnd.apple.mpegurl")

    def hls_file(self, sid, name):
        path, sess = convert.file_for(sid, name)
        if not path:
            return self.reply(404, '{"error":"Niet (meer) beschikbaar"}', "application/json")
        with open(path, "rb") as f:
            data = f.read()
        if name.endswith(".vtt"):
            data, ctype = convert.vtt_with_map(data, sess), "text/vtt; charset=utf-8"
        elif name.endswith(".m3u8"):
            ctype = "application/vnd.apple.mpegurl"
        else:
            ctype = "video/mp2t"
        try:
            self.reply(200, data, ctype)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    # ---- HLS-afspeellijst ----
    def playlist(self, target, key):
        req = urllib.request.Request(target, headers={"User-Agent": UA})
        try:
            with OPENER.open(req, timeout=20) as r:
                base, text = r.geturl(), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            log(f"m3u8 {e.code} {redact(target)}")
            return self.reply(502, '{"error":"Server gaf fout %d"}' % e.code, "application/json")
        except Blocked:
            raise
        except Exception as e:  # bv. server verbreekt de verbinding zonder antwoord
            log(f"m3u8 mislukt ({type(e).__name__}: {e}) {redact(target)}")
            return self.reply(502, json.dumps({"error": f"IPTV-server gaf geen antwoord ({type(e).__name__})"}), "application/json")
        if not text.lstrip().startswith("#EXTM3U"):
            return self.reply(502, '{"error":"Geen geldige HLS-afspeellijst"}', "application/json")
        me = f"http://{self.headers.get('Host') or f'{lan_ip()}:{PORT}'}"

        def wrap(link):
            absu = urllib.parse.urljoin(base, link)
            path = "/m3u8" if re.search(r"\.m3u8($|\?)", urllib.parse.urlparse(absu).path, re.I) else "/p"
            return f"{me}{path}?" + urllib.parse.urlencode({"k": key, "url": absu})

        out = []
        for line in text.splitlines():
            t = line.strip()
            if not t:
                out.append(line)
            elif t.startswith("#"):
                out.append(re.sub(r'URI="([^"]+)"', lambda m: f'URI="{wrap(m.group(1))}"', line))
            else:
                out.append(wrap(t))
        self.reply(200, "\n".join(out) + "\n", "application/vnd.apple.mpegurl")

    # ---- bestanden doorgeven ----
    def passthrough(self, target):
        headers = {"User-Agent": UA}
        rng = self.headers.get("Range")
        if rng:
            headers["Range"] = rng
        req = urllib.request.Request(target, headers=headers, method="HEAD" if self.command == "HEAD" else "GET")
        slot = urllib.parse.urlparse(target).path  # één verbinding per film/videostukje
        is_segment = bool(re.search(r"\.(ts|aac|m4s|mp4a)($|\?)", slot, re.I)) and "/movie/" not in slot
        try:
            resp = OPENER.open(req, timeout=30)
        except urllib.error.HTTPError as e:
            log(f"{e.code} {redact(target)}")
            return self.reply(e.code, e.read(2000) or b"", e.headers.get("Content-Type", "text/plain"))
        except Blocked:
            raise
        except Exception as e:
            log(f"mislukt ({type(e).__name__}: {e}) {redact(target)}")
            return self.reply(502, json.dumps({"error": f"IPTV-server gaf geen antwoord ({type(e).__name__})"}), "application/json")
        if not is_segment:
            take_slot(slot, resp)
            log(f"start {resp.status} {redact(target)} {rng or ''}")
        try:
            self.send_response(resp.status)
            self.cors()
            for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified"):
                v = resp.headers.get(h)
                if v:
                    self.send_header(h, v)
            if re.search(r"\.ts($|\?)", slot, re.I):
                self.send_header("Content-Type", "video/mp2t") if not resp.headers.get("Content-Type") else None
            if not resp.headers.get("Content-Length"):
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command == "HEAD":
                return
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)
        finally:
            try:
                resp.close()
            except Exception:
                pass
            if not is_segment:
                release_slot(slot, resp)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    if not APP_KEY:
        sys.exit("Geen APP_KEY gevonden (worker/secrets.env of IPTV_APP_KEY).")
    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"IPTV Cast thuis-tussenschakel draait op http://{lan_ip()}:{PORT}")
    log("Laat dit venster open. Zet de VPN aan op deze pc.")
    check_country()
    threading.Thread(target=vpn_watch, daemon=True).start()
    convert.cleanup_all()  # resten van een vorige keer opruimen
    threading.Thread(target=convert.reaper, args=(log,), daemon=True).start()
    atexit.register(convert.cleanup_all)
    if not convert.FFMPEG:
        log("LET OP: ffmpeg niet gevonden: films zonder omzetting (geen geluid bij E-AC3)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        convert.cleanup_all()
