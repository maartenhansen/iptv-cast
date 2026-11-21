"""Testschermpje: waarom weigert de IPTV-server (403)?

Start:  py dev/diagnose.py   en open http://localhost:8020

Je vult je Xtream-gegevens in op het scherm; die blijven op deze pc
(en gaan enkel naar je IPTV-server en je eigen Worker). In de resultaten
worden gebruikersnaam en wachtwoord weggelaten.
"""
import json, os, re, socket, time, urllib.parse, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode

PORT = 8020
HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, "..", "worker", "secrets.env")
DEFAULT_WORKER = "https://iptv-cast.hansen-tools.workers.dev"

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



HOME = "http://127.0.0.1:8030"


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
    enc = urllib.parse.quote
    # 2. Live: eerste zender, rechtstreeks vanaf thuis (via de Worker weigert de provider streams)
    try:
        streams = get_json(api(action="get_live_streams", category_id=cats["live"][0]["category_id"]), secrets) if cats["live"] else []
        st = streams[0] if streams else None
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
        if self.path != "/run":
            return self.send(404, "niet gevonden", "text/plain")
        d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        res = run(d.get("server", ""), d.get("user", ""), d.get("pw", ""), d.get("worker") or DEFAULT_WORKER)
        self.send(200, json.dumps(res), "application/json")


if __name__ == "__main__":
    print(f"Testschermpje op http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
