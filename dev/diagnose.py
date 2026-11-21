"""Testschermpje: waarom weigert de IPTV-server (403)?

Start:  py dev/diagnose.py   en open http://localhost:8020

Je vult je Xtream-gegevens in op het scherm; die blijven op deze pc
(en gaan enkel naar je IPTV-server en je eigen Worker). In de resultaten
worden gebruikersnaam en wachtwoord weggelaten.
"""
import json, os, re, socket, time, urllib.parse, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode

PORT = 8021
HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, "..", "worker", "secrets.env")
DEFAULT_WORKER = "https://iptv-cast-dev.hansen-tools.workers.dev"

UAS = {
    "VLC (zoals de Worker)": "VLC/3.0.20 LibVLC/3.0.20",
    "IPTV Smarters": "IPTVSmartersPlayer",
    "Chrome-browser": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36",
    "Geen (Python standaard)": None,
}


def app_key():
    try:
        for line in open(SECRETS, encoding="utf-8"):
            if line.startswith("APP_KEY="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def fetch(url, ua, secrets):
    req = urllib.request.Request(url)
    if ua:
        req.add_header("User-Agent", ua)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            code, body, ctype, final = r.status, r.read(4000), r.headers.get("Content-Type", ""), r.geturl()
    except urllib.error.HTTPError as e:
        code, body, ctype, final = e.code, e.read(4000), e.headers.get("Content-Type", ""), e.geturl()
    except Exception as e:  # netwerkfout, time-out, ...
        return {"status": "fout", "info": mask(str(e), secrets), "ms": int((time.time() - t0) * 1000)}
    return {"status": code, "info": summarize(body, ctype, secrets), "ms": int((time.time() - t0) * 1000),
            "redirect": mask(final, secrets) if final != url else ""}


def summarize(body, ctype, secrets):
    text = body.decode("utf-8", "replace")
    try:
        d = json.loads(text)
        if isinstance(d, dict) and "user_info" in d:
            u = d.get("user_info") or {}
            return (f"Xtream-antwoord: auth={u.get('auth')} status={u.get('status')} "
                    f"formaten={u.get('allowed_output_formats')} max_verbindingen={u.get('max_connections')}")
        if isinstance(d, dict) and "error" in d:
            return "Worker zegt: " + mask(str(d["error"]), secrets)
        return "JSON: " + mask(text[:200], secrets)
    except ValueError:
        pass
    title = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
    if title:
        return f"HTML-pagina: {title.group(1).strip()[:120]}"
    return f"{ctype or 'onbekend type'}: " + mask(re.sub(r"\s+", " ", text)[:160], secrets)


def mask(s, secrets):
    for v in secrets:
        if v:
            s = s.replace(v, "•••").replace(urllib.parse.quote(v, safe=""), "•••")
    return s


def row(test, status, info, ms=0):
    return {"via": "Netwerk", "test": test, "status": status, "info": info, "ms": ms}


def network_checks(server):
    rows, flags = [], {"country": "?", "dns_blocked": False, "port_open": None}
    u = urllib.parse.urlparse(server)
    host, port = u.hostname or "", u.port or (443 if u.scheme == "https" else 80)
    ua = {"User-Agent": UAS["Chrome-browser"]}
    # 1. Uit welk land komt je verbinding? (toont of de VPN aanstaat)
    try:
        req = urllib.request.Request("https://www.cloudflare.com/cdn-cgi/trace", headers=ua)
        with urllib.request.urlopen(req, timeout=10) as r:
            t = dict(l.split("=", 1) for l in r.read().decode().splitlines() if "=" in l)
        flags["country"] = t.get("loc", "?")
        rows.append(row("Je verbinding komt uit", "OK", f"land: {flags['country']} (staat je VPN aan, dan is dit meestal het VPN-land)"))
    except Exception as e:
        rows.append(row("Je verbinding komt uit", "fout", str(e)))
    # 2. DNS: geeft je internetprovider hetzelfde adres als een onafhankelijke DNS (Cloudflare 1.1.1.1)?
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
        rows.append(row("DNS (adresboek)", "OK", "Serveradres is een IP-adres, dus geen DNS nodig"))
        sys_ips = [host]
    else:
        sys_ips, sys_err, doh_ips = [], "", []
        try:
            sys_ips = sorted({a[4][0] for a in socket.getaddrinfo(host, None, socket.AF_INET)})
        except Exception as e:
            sys_err = str(e)
        try:
            req = urllib.request.Request("https://cloudflare-dns.com/dns-query?" + urlencode({"name": host, "type": "A"}),
                                         headers={**ua, "Accept": "application/dns-json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                doh_ips = sorted({a["data"] for a in json.load(r).get("Answer", []) if a.get("type") == 1})
        except Exception:
            pass
        if not sys_ips and doh_ips:
            flags["dns_blocked"] = True
            rows.append(row("DNS (adresboek)", "geblokkeerd", f"Je eigen verbinding vindt het adres niet ({sys_err[:60]}), 1.1.1.1 wel: {', '.join(doh_ips)}"))
        elif sys_ips and doh_ips and set(sys_ips).isdisjoint(doh_ips):
            flags["dns_blocked"] = True
            rows.append(row("DNS (adresboek)", "verschillend", f"Je verbinding zegt {', '.join(sys_ips)}, 1.1.1.1 zegt {', '.join(doh_ips)}: mogelijk blokkeert je provider dit adres"))
        elif sys_ips:
            rows.append(row("DNS (adresboek)", "OK", f"{', '.join(sys_ips)} (zelfde als 1.1.1.1)" if doh_ips else ", ".join(sys_ips)))
        else:
            rows.append(row("DNS (adresboek)", "fout", f"Adres niet gevonden, ook niet via 1.1.1.1. Klopt het serveradres? {sys_err[:60]}"))
    # 3. Kan deze pc de server op zijn poort bereiken?
    if sys_ips:
        t0 = time.time()
        try:
            socket.create_connection((sys_ips[0], port), timeout=8).close()
            flags["port_open"] = True
            rows.append(row(f"Poort {port} bereikbaar", "OK", f"verbinding met {sys_ips[0]}:{port} gelukt", int((time.time() - t0) * 1000)))
        except Exception as e:
            flags["port_open"] = False
            rows.append(row(f"Poort {port} bereikbaar", "fout", f"{sys_ips[0]}:{port}: {e}", int((time.time() - t0) * 1000)))
    return rows, flags



def get_json(url, secrets):
    req = urllib.request.Request(url, headers={"User-Agent": UAS["Chrome-browser"]})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def peek(url, ua, secrets, rng=True):
    """Haalt enkel de eerste paar KB op (telt kort als 1 verbinding)."""
    req = urllib.request.Request(url)
    if ua:
        req.add_header("User-Agent", ua)
    if rng:
        req.add_header("Range", "bytes=0-4095")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            head = r.read(4096)
            return r.status, r.headers.get("Content-Type", ""), head, int((time.time() - t0) * 1000), ""
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read(400), int((time.time() - t0) * 1000), ""
    except Exception as e:
        return "fout", "", b"", int((time.time() - t0) * 1000), mask(str(e), secrets)


def live_direct(url, secrets):
    """Live-zender rechtstreeks vanaf thuis, zoals de Chromecast het zonder Worker zou doen.
    Voor HLS op de Chromecast moet de server ook een CORS-header sturen."""
    rows = []
    ua = UAS["Chrome-browser"]
    for step in range(3):  # afspeellijst -> (eventueel) sub-afspeellijst -> videostukje
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Origin": "https://www.gstatic.com"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                code, final, hdr, body = r.status, r.geturl(), r.headers, r.read(4096)
        except urllib.error.HTTPError as e:
            rows.append({"via": "App-stappen (thuis)", "test": "Live rechtstreeks", "status": e.code,
                         "info": summarize(e.read(400), e.headers.get("Content-Type", ""), secrets), "ms": int((time.time() - t0) * 1000)})
            return rows
        except Exception as e:
            rows.append({"via": "App-stappen (thuis)", "test": "Live rechtstreeks", "status": "fout", "info": mask(str(e), secrets), "ms": 0})
            return rows
        ms = int((time.time() - t0) * 1000)
        cors = hdr.get("Access-Control-Allow-Origin")
        host = urllib.parse.urlparse(final).hostname
        is_list = body.lstrip().startswith(b"#EXTM3U")
        what = "afspeellijst" if is_list else f"videostukje ({hdr.get('Content-Type') or '?'}, {len(body)} bytes)"
        rows.append({"via": "App-stappen (thuis)", "test": f"Live rechtstreeks: {what}", "status": code,
                     "info": f"server: {host} · CORS voor Chromecast: {cors or 'NEE'}", "ms": ms})
        if not is_list:
            return rows
        links = [l.strip() for l in body.decode("utf-8", "replace").splitlines() if l.strip() and not l.startswith("#")]
        if not links:
            return rows
        url = urllib.parse.urljoin(final, links[0])
    return rows



HOME = "http://127.0.0.1:8031"


def home_up():
    try:
        with urllib.request.urlopen(HOME + "/", timeout=3) as r:
            return b"iptv-cast-home" in r.read()
    except Exception:
        return False


def via_home(live, film, ext, key, secrets):
    """Zelfde stappen als de Chromecast, maar via de thuis-tussenschakel op de Beelink."""
    rows = []
    R = lambda test, status, info, ms=0: rows.append({"via": "Via Beelink", "test": test, "status": status, "info": info, "ms": ms})
    if live:
        url = HOME + "/m3u8?" + urlencode({"k": key, "url": live})
        for step in range(3):
            code, ctype, head, ms, err = peek(url, UAS["Chrome-browser"], secrets, rng=False)
            if code != 200:
                R("Live", code, err or summarize(head, ctype, secrets), ms)
                break
            if head.lstrip().startswith(b"#EXTM3U"):
                links = [l.strip() for l in head.decode("utf-8", "replace").splitlines() if l.strip() and not l.startswith("#")]
                R("Live: afspeellijst", 200, f"geldige HLS-lijst, {len(links)} links", ms)
                if not links:
                    break
                url = links[0].replace(urllib.parse.urlparse(links[0]).netloc, "127.0.0.1:8030", 1) if "8030" in links[0] else links[0]
            else:
                R("Live: videostukje", code, f"{ctype or '?'}, {len(head)} bytes", ms)
                break
        time.sleep(3)
    if film:
        code, ctype, head, ms, err = peek(HOME + "/p?" + urlencode({"k": key, "url": film}), UAS["Chrome-browser"], secrets)
        R(f"Film (.{ext})", code, err or f"{ctype or '?'}, {len(head)} bytes", ms)
    return rows



SEPARATOR = re.compile(r"^[\s#*=\-_|~.]{3,}|[#*=]{3,}")


def pick_channels(api, cats, secrets, want=2):
    """Kies echte zenders (geen scheidingsregels zoals '##### NL #####') uit een categorie met genoeg zenders."""
    for c in cats[:15]:
        try:
            lst = get_json(api(action="get_live_streams", category_id=c["category_id"]), secrets)
        except Exception:
            continue
        real = [x for x in lst if x.get("stream_id") and not SEPARATOR.search(x.get("name", ""))]
        if len(real) >= want:
            return real[:want], c.get("category_name", "?")
    return [], "?"


def live_variants(server, user, pw, api, cats, secrets):
    rows = []
    enc = urllib.parse.quote
    chans, catname = pick_channels(api, cats, secrets)
    if not chans:
        return [{"via": "Live-varianten", "test": "Zenders kiezen", "status": "fout", "info": "geen bruikbare zenders gevonden", "ms": 0}]
    u, p = enc(user, safe=""), enc(pw, safe="")
    for n, ch in enumerate(chans):
        sid = ch["stream_id"]
        variants = [("/live/…/ID.m3u8", f"{server}/live/{u}/{p}/{sid}.m3u8"),
                    ("/live/…/ID.ts", f"{server}/live/{u}/{p}/{sid}.ts")]
        if n == 0:
            variants.append(("/…/ID (oud formaat)", f"{server}/{u}/{p}/{sid}"))
        for label, url in variants:
            time.sleep(2)
            code, ctype, head, ms, err = peek(url, UAS["VLC (zoals de Worker)"], secrets, rng=False)
            if head.lstrip().startswith(b"#EXTM3U"):
                what = "HLS-afspeellijst ✓"
            elif head[:1] == b"G" or "mp2t" in (ctype or ""):
                what = f"videostroom ✓ ({ctype or '?'})"
            else:
                what = err or summarize(head, ctype, secrets)
            rows.append({"via": "Live-varianten", "test": f"{ch.get('name','?')[:30]} · {label}", "status": code, "info": what, "ms": ms})
    rows.insert(0, {"via": "Live-varianten", "test": "Gekozen categorie", "status": "OK", "info": catname[:60], "ms": 0})
    return rows


def flow_row(test, status, info, ms=0):
    return {"via": "App-stappen (Worker)", "test": test, "status": status, "info": info, "ms": ms}


def app_flow(server, user, pw, worker, key):
    secrets = [user, pw, key]
    rows = []
    w = worker.rstrip("/")
    api = lambda **p: w + "/api?" + urlencode({"k": key, "s": server, "username": user, "password": pw, **p})
    # 1. Categorieën (wat de app als eerste ophaalt per tab)
    cats = {}
    for kind, action in (("live", "get_live_categories"), ("vod", "get_vod_categories"), ("series", "get_series_categories")):
        r = fetch(api(action=action), UAS["Chrome-browser"], secrets)
        try:
            data = get_json(api(action=action), secrets) if r["status"] == 200 else []
        except Exception:
            data = []
        cats[kind] = data if isinstance(data, list) else []
        r["info"] = f"{len(cats[kind])} categorieën" if r["status"] == 200 and cats[kind] else r["info"]
        rows.append({"via": "App-stappen (Worker)", "test": f"Lijst: {action}", **r})
    rows += live_variants(server, user, pw, api, cats["live"], secrets)
    time.sleep(3)
    enc = urllib.parse.quote
    # 2. Live: eerste zender, rechtstreeks vanaf thuis (via de Worker weigert de provider streams)
    try:
        chosen, _ = pick_channels(api, cats["live"], secrets, want=1)
        st = chosen[0] if chosen else None
    except Exception as e:
        st, streams = None, []
        rows.append(flow_row("Live: zenderlijst", "fout", mask(str(e), secrets)))
    live = None
    if st:
        live = f"{server}/live/{enc(user, safe='')}/{enc(pw, safe='')}/{st['stream_id']}.m3u8"
        if not home_up():
            time.sleep(2)
            rows += live_direct(live, secrets)
    # 3. Film: eerste film, rechtstreeks vanaf thuis
    try:
        vods = get_json(api(action="get_vod_streams", category_id=cats["vod"][0]["category_id"]), secrets) if cats["vod"] else []
        v = vods[0] if vods else None
    except Exception as e:
        v = None
        rows.append(flow_row("Film: filmlijst", "fout", mask(str(e), secrets)))
    film, ext = None, ""
    if v:
        ext = v.get("container_extension") or "mp4"
        film = f"{server}/movie/{enc(user, safe='')}/{enc(pw, safe='')}/{v['stream_id']}.{ext}"
    if home_up():
        rows += via_home(live, film, ext, key, secrets)
    elif film:
        time.sleep(3)
        code, ctype, head, ms, err = peek(film, UAS["Chrome-browser"], secrets)
        rows.append({"via": "App-stappen (thuis)", "test": f"Film (.{ext}) rechtstreeks (zoals de Chromecast)",
                     "status": code, "info": err or f"{ctype or '?'}, {len(head)} bytes", "ms": ms})
    return rows


def run(server, user, pw, worker):
    server = server.strip().rstrip("/")
    if not re.match(r"^https?://", server):
        server = "http://" + server
    secrets = [user, pw]
    api = f"{server}/player_api.php?" + urlencode({"username": user, "password": pw})
    out, flags = network_checks(server)
    for label, ua in UAS.items():
        out.append({"via": "Thuis (deze pc)", "test": f"Login, speler: {label}", **fetch(api, ua, secrets)})
    out.append({"via": "Thuis (deze pc)", "test": "Server zonder login (bereikbaar?)",
                **fetch(f"{server}/player_api.php", UAS["VLC (zoals de Worker)"], secrets)})
    key = app_key()
    if not key:
        out.append({"via": "Worker", "test": "Login", "status": "fout", "info": "Geen APP_KEY gevonden in worker/secrets.env", "ms": 0})
    else:
        wurl = worker.rstrip("/") + "/api?" + urlencode({"k": key, "s": server, "username": user, "password": pw})
        out.append({"via": "Worker (Cloudflare)", "test": "Login, speler: VLC", **fetch(wurl, UAS["Chrome-browser"], secrets + [key])})
        wurl2 = worker.rstrip("/") + "/api?" + urlencode({"k": key, "s": server})
        out.append({"via": "Worker (Cloudflare)", "test": "Server zonder login", **fetch(wurl2, UAS["Chrome-browser"], secrets + [key])})
        out += app_flow(server, user, pw, worker, key)
    return {"server": server, "results": out, **flags}


# ---------- Formaatcontrole van een film of serie (ffprobe) ----------
import glob, shutil, subprocess

_lists = {}


def find_ffprobe():
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    la = os.environ.get("LOCALAPPDATA", "")
    for pat in (os.path.join(la, "Microsoft", "WinGet", "Links", "ffprobe.exe"),
                os.path.join(la, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", "ffprobe.exe")):
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    return None


def home_json(server, user, pw, **params):
    """Rechtstreeks vanaf deze pc (met VPN), niet via de Worker."""
    url = f"{server}/player_api.php?" + urlencode({"username": user, "password": pw, **params})
    req = urllib.request.Request(url, headers={"User-Agent": UAS["VLC (zoals de Worker)"]})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def norm_server(server):
    server = server.strip().rstrip("/")
    return server if re.match(r"^https?://", server) else "http://" + server


def search(server, user, pw, q):
    server = norm_server(server)
    key = (server, user)
    if key not in _lists:
        _lists[key] = {"vod": home_json(server, user, pw, action="get_vod_streams"),
                       "series": home_json(server, user, pw, action="get_series")}
    words = [w for w in re.split(r"\s+", q.lower().strip()) if w]
    out = []
    for kind in ("vod", "series"):
        for it in _lists[key][kind]:
            name = str(it.get("name", ""))
            if all(w in name.lower() for w in words):
                out.append({"kind": kind, "id": it.get("stream_id") if kind == "vod" else it.get("series_id"),
                            "ext": it.get("container_extension", ""), "name": name})
                if len(out) >= 15:
                    return out
    return out


TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
IMAGE_SUBS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
CAST_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac"}
CAST_VIDEO = {"h264": "ja", "vp8": "ja", "vp9": "ja", "hevc": "enkel Chromecast met Google TV",
              "av1": "enkel nieuwere modellen"}


def probe(server, user, pw, kind, item_id, ext):
    server = norm_server(server)
    secrets = [user, pw]
    exe = find_ffprobe()
    if not exe:
        return {"error": "ffprobe niet gevonden. Is ffmpeg geïnstalleerd? Herstart dan het testschermpje."}
    enc = urllib.parse.quote
    if kind == "series":
        info = home_json(server, user, pw, action="get_series_info", series_id=item_id)
        eps = info.get("episodes") or {}
        if not eps:
            return {"error": "Geen afleveringen gevonden"}
        season = sorted(eps.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)[0]
        ep = eps[season][0]
        e_ext = ep.get("container_extension") or "mkv"
        url = f"{server}/series/{enc(user, safe='')}/{enc(pw, safe='')}/{ep['id']}.{e_ext}"
        label = f"Seizoen {season}, aflevering {ep.get('episode_num', 1)} (.{e_ext})"
    else:
        url = f"{server}/movie/{enc(user, safe='')}/{enc(pw, safe='')}/{item_id}.{ext or 'mkv'}"
        label = f"Film (.{ext})"
    cmd = [exe, "-v", "error", "-user_agent", UAS["VLC (zoals de Worker)"], "-rw_timeout", "20000000",
           "-probesize", "5000000", "-analyzeduration", "5000000",
           "-show_streams", "-show_format", "-of", "json", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"error": "ffprobe duurde te lang (server antwoordt niet?)"}
    if r.returncode != 0:
        return {"error": mask(r.stderr.strip()[:300] or "ffprobe mislukt", secrets)}
    d = json.loads(r.stdout or "{}")
    rows = []
    for st in d.get("streams", []):
        t, c = st.get("codec_type"), (st.get("codec_name") or "?").lower()
        tags = st.get("tags", {}) or {}
        if t == "video":
            ok = CAST_VIDEO.get(c, "nee")
            detail = f"{st.get('width')}x{st.get('height')} {st.get('profile') or ''}".strip()
        elif t == "audio":
            ok = "ja" if c in CAST_AUDIO else "NEE: moet omgezet worden naar AAC"
            detail = f"{st.get('channels', '?')} kanalen {st.get('channel_layout') or ''}".strip()
        elif t == "subtitle":
            ok = ("niet ingebouwd, maar om te zetten (tekst)" if c in TEXT_SUBS
                  else "niet ingebouwd, en BEELD-ondertitel: moeilijk" if c in IMAGE_SUBS else "onbekend")
            detail = ""
        else:
            continue
        flags = [k for k, v in (st.get("disposition") or {}).items() if v and k in ("default", "forced")]
        rows.append({"type": {"video": "Beeld", "audio": "Geluid", "subtitle": "Ondertitel"}[t], "codec": c,
                     "lang": tags.get("language", ""), "title": tags.get("title", "")[:40], "detail": detail,
                     "flags": ", ".join(flags), "cast": ok})
    fmt = d.get("format", {}) or {}
    return {"label": label, "container": fmt.get("format_name", "?"),
            "duration": round(float(fmt.get("duration", 0) or 0) / 60), "streams": rows}



PAGE = """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>IPTV-test</title>
<style>
:root{--bg:#111318;--s:#1b1e26;--l:#2f3542;--t:#eef0f4;--m:#9aa3b2;--a:#7c6cff;--ok:#4ade80;--bad:#ff6b6b}
body{margin:0;background:var(--bg);color:var(--t);font:15px/1.45 system-ui,sans-serif;padding:16px}
main{max-width:860px;margin:auto} h1{font-size:20px} p{color:var(--m)}
form{display:grid;gap:10px;background:var(--s);padding:16px;border-radius:12px}
label{display:grid;gap:4px;font-size:13px;color:var(--m)}
input{min-height:42px;padding:8px 12px;background:var(--bg);color:var(--t);border:1px solid var(--l);border-radius:8px;font:inherit}
button{min-height:46px;border:0;border-radius:10px;background:var(--a);color:#fff;font:inherit;font-weight:600;cursor:pointer}
button:disabled{opacity:.5}
table{width:100%;border-collapse:collapse;margin-top:16px;font-size:14px}
th,td{text-align:left;padding:8px;border-bottom:1px solid var(--l);vertical-align:top}
th{color:var(--m);font-weight:500} .ok{color:var(--ok);font-weight:700} .bad{color:var(--bad);font-weight:700}
#verdict{margin-top:16px;padding:12px;border-radius:10px;background:var(--s);border-left:3px solid var(--a)}
</style></head><body><main>
<h1>IPTV-verbindingstest</h1>
<p><b>Heb je normaal een VPN nodig voor je IPTV? Zet die dan eerst aan op deze pc.</b> Vul dezelfde gegevens in als in de app. Ze blijven op deze pc en gaan enkel naar je IPTV-server en je eigen Worker. Gebruikersnaam en wachtwoord worden in de resultaten verborgen.</p>
<form id="f">
<label>Serveradres<input name="server" placeholder="http://provider.com:8080" required></label>
<label>Gebruikersnaam<input name="user" autocomplete="off" required></label>
<label>Wachtwoord<input name="pw" type="password" autocomplete="off" required></label>
<label>Worker-adres<input name="worker" value="__WORKER__"></label>
<button>Test starten</button>
</form>
<div id="verdict" hidden></div>
<table id="t" hidden><thead><tr><th>Via</th><th>Test</th><th>Resultaat</th><th>Details</th></tr></thead><tbody></tbody></table>
<section id="fmt" style="margin-top:28px">
<h2 style="font-size:18px">Formaat van een film of serie</h2>
<p>Vul eerst de gegevens hierboven in en zet je VPN aan. Er wordt maar een klein stukje van het bestand gelezen.</p>
<form id="sf" style="grid-template-columns:1fr auto;align-items:end">
<label>Naam (of een deel ervan)<input name="q" placeholder="bv. Primetime" required></label>
<button style="padding:0 18px">Zoeken</button>
</form>
<div id="hits" style="display:grid;gap:6px;margin-top:10px"></div>
<div id="probe"></div>
</section>
</main><script>
const f=document.getElementById('f'),tb=document.querySelector('#t tbody');
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
f.onsubmit=async e=>{e.preventDefault();const b=f.querySelector('button');b.disabled=true;b.textContent='Bezig met testen…';
 try{const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(f)))});
 const d=await r.json();document.getElementById('t').hidden=false;
 tb.innerHTML=d.results.map(x=>{const good=x.status===200&&/auth=1/.test(x.info);const reach=x.status===200||x.status===206||x.status==='OK';
  return `<tr><td>${esc(x.via)}</td><td>${esc(x.test)}</td><td class="${good||reach?'ok':'bad'}">${esc(x.status)}</td><td>${esc(x.info)}${x.redirect?'<br><small>doorverwezen naar '+esc(x.redirect)+'</small>':''} <small>(${x.ms} ms)</small></td></tr>`}).join('');
 verdict(d.results,d);
 }catch(err){alert('Test mislukt: '+err)}finally{b.disabled=false;b.textContent='Opnieuw testen'}};
function verdict(rs,d){const v=document.getElementById('verdict');v.hidden=false;
 const home=rs.filter(r=>r.via.startsWith('Thuis')&&r.test.startsWith('Login'));
 const homeOk=home.filter(r=>/auth=1/.test(r.info)).map(r=>r.test.replace('Login, speler: ',''));
 const w=rs.find(r=>r.via.startsWith('Worker')&&r.test.startsWith('Login'));const wOk=w&&/auth=1/.test(w.info);
 let msg;
 if(wOk) msg='✓ Alles werkt, ook via de Worker. Het probleem zit dan elders (bv. een typfout in de app).';
 else if(!homeOk.length) msg='✗ Ook vanaf je eigen pc lukt inloggen niet. Controleer serveradres, gebruikersnaam en wachtwoord.';
 else if(homeOk.includes('VLC (zoals de Worker)')) msg='Vanaf je thuisverbinding werkt het (ook als VLC), via Cloudflare niet → je provider blokkeert Cloudflare/datacenters. Oplossing: de tussenschakel thuis laten draaien.';
 else msg='Thuis werkt het enkel met: '+homeOk.join(', ')+' → je provider kijkt naar het type speler. Oplossing: de Worker een andere speler-identiteit geven.';
 let net='Je verbinding komt uit: '+d.country+'. ';
 if(d.dns_blocked) net+='Je internetprovider lijkt de server te blokkeren via DNS (daarom heb je een VPN nodig). Dat raakt de Worker niet: die gebruikt zijn eigen DNS. ';
 if(d.port_open===false) net+='De server is vanaf deze pc niet bereikbaar op zijn poort (staat je VPN aan?). ';
 const bad=x=>!(x.status===200||x.status===206);
 const wStream=rs.filter(x=>x.via==='App-stappen (Worker)'&&/^(Live|Film)/.test(x.test));
 const direct=rs.filter(x=>x.via==='App-stappen (thuis)'||/rechtstreeks/.test(x.test));
 const hb=rs.filter(x=>x.via==='Via Beelink');
 if(hb.length){ v.textContent=net+(hb.some(bad)?'✗ Via de Beelink lukt nog niet alles (zie tabel). Staat de VPN aan op deze pc?':'✓ Via de Beelink werken live tv en films. Klaar om te casten!'); return; }
 if(wOk&&wStream.some(bad)) msg='Inloggen en lijsten werken via de Worker, maar streams via de Worker worden geweigerd. '
   +(direct.length&&!direct.some(bad)?'Rechtstreeks vanaf thuis werken de streams wel.':'Ook rechtstreeks lukt niet alles (zie tabel).');
 v.textContent=net+msg;}
const creds=()=>{const d=Object.fromEntries(new FormData(f));return {server:d.server,user:d.user,pw:d.pw}};
document.getElementById('sf').onsubmit=async e=>{e.preventDefault();const hits=document.getElementById('hits');
 hits.innerHTML='<p>Zoeken… (de eerste keer duurt dit even: alle lijsten worden geladen)</p>';document.getElementById('probe').innerHTML='';
 try{const r=await fetch('/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...creds(),q:new FormData(e.target).get('q')})});
  const d=await r.json(); if(d.error) throw new Error(d.error);
  hits.innerHTML=d.length?d.map((x,i)=>'<button type="button" data-i="'+i+'" style="text-align:left;padding:0 12px;background:var(--s);border:1px solid var(--l)">'+(x.kind==='vod'?'🎬 ':'📺 ')+esc(x.name)+(x.ext?' <small>(.'+esc(x.ext)+')</small>':'')+'</button>').join(''):'<p>Niets gevonden.</p>';
  hits.onclick=ev=>{const b=ev.target.closest('[data-i]');if(b)probe(d[+b.dataset.i])};
 }catch(err){hits.innerHTML='<p class="bad">'+esc(err.message)+'</p>'}};
async function probe(x){const pr=document.getElementById('probe');pr.innerHTML='<p>Formaat nakijken…</p>';
 try{const r=await fetch('/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...creds(),kind:x.kind,id:x.id,ext:x.ext})});
  const d=await r.json(); if(d.error) throw new Error(d.error);
  pr.innerHTML='<p><b>'+esc(x.name)+'</b> · '+esc(d.label)+' · container: '+esc(d.container)+' · '+d.duration+' min</p>'
   +'<table><thead><tr><th>Spoor</th><th>Formaat</th><th>Taal</th><th>Naam</th><th>Details</th><th>Chromecast?</th></tr></thead><tbody>'
   +d.streams.map(s=>'<tr><td>'+s.type+'</td><td>'+esc(s.codec)+'</td><td>'+esc(s.lang)+'</td><td>'+esc(s.title)+(s.flags?' <small>('+esc(s.flags)+')</small>':'')+'</td><td>'+esc(s.detail)+'</td><td class="'+(s.cast==='ja'?'ok':'bad')+'">'+esc(s.cast)+'</td></tr>').join('')
   +'</tbody></table>';
 }catch(err){pr.innerHTML='<p class="bad">'+esc(err.message)+'</p>'}}

</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # geen gegevens in het logboek
        pass

    def send(self, code, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self.send(200, PAGE.replace("__WORKER__", DEFAULT_WORKER), "text/html; charset=utf-8")
        self.send(404, "niet gevonden", "text/plain")

    def do_POST(self):
        d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path in ("/search", "/probe"):
            try:
                if self.path == "/search":
                    res = search(d.get("server", ""), d.get("user", ""), d.get("pw", ""), d.get("q", ""))
                else:
                    res = probe(d.get("server", ""), d.get("user", ""), d.get("pw", ""),
                                d.get("kind"), d.get("id"), d.get("ext", ""))
            except Exception as e:
                res = {"error": mask(f"{type(e).__name__}: {e}", [d.get("user", ""), d.get("pw", "")])}
            return self.send(200, json.dumps(res), "application/json")
        if self.path != "/run":
            return self.send(404, "niet gevonden", "text/plain")
        res = run(d.get("server", ""), d.get("user", ""), d.get("pw", ""), d.get("worker") or DEFAULT_WORKER)
        self.send(200, json.dumps(res), "application/json")


if __name__ == "__main__":
    print(f"Testschermpje op http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
