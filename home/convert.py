"""Omzetting met ffmpeg, zodat de Chromecast films mét geluid en ondertitels afspeelt.

Films van je provider zijn mkv-bestanden met:
  - beeld H.264          -> blijft ongewijzigd (kopiëren, kost bijna geen rekenkracht)
  - geluid E-AC3 5.1     -> wordt AAC stereo (dat speelt de Chromecast wel af)
  - ondertitels SRT/ASS  -> worden WebVTT-sporen (te kiezen met CC in de app)

ffmpeg leest de film via de eigen /p-doorgeefroute van de Beelink (dus met alle
beveiligingen en de VPN-controle) en schrijft een HLS-stream in stukjes naar een
tijdelijke map. De Chromecast haalt die stukjes op.

Je abonnement staat maar 1 verbinding toe, dus er draait altijd maar één omzetting
tegelijk. Wordt er 90 seconden niets meer opgevraagd, dan stopt de omzetting.
"""
import glob, json, os, re, shutil, subprocess, tempfile, threading, time, uuid

ROOT = os.path.join(tempfile.gettempdir(), "iptvcast-hls")
IDLE_SECONDS = 90
MAX_SUBS = 3

TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
CAST_AUDIO = {"aac", "mp3"}
LANG2 = {"dut": "nl", "nld": "nl", "nl": "nl", "eng": "en", "en": "en", "fre": "fr", "fra": "fr", "fr": "fr",
         "ger": "de", "deu": "de", "de": "de", "spa": "es", "es": "es", "ita": "it", "it": "it", "por": "pt",
         "pt": "pt", "tur": "tr", "tr": "tr", "ara": "ar", "ar": "ar", "pol": "pl", "pl": "pl"}
LANG_NAME = {"nl": "Nederlands", "en": "English", "fr": "Français", "de": "Deutsch", "es": "Español",
             "it": "Italiano", "pt": "Português", "tr": "Türkçe", "ar": "العربية", "pl": "Polski"}


def find_tool(name):
    exe = shutil.which(name)
    if exe:
        return exe
    la = os.environ.get("LOCALAPPDATA", "")
    hits = glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", name + ".exe"))
    return hits[0] if hits else None


FFMPEG, FFPROBE = find_tool("ffmpeg"), find_tool("ffprobe")


class Session:
    def __init__(self, key, start):
        self.sid = uuid.uuid4().hex
        self.key = key
        self.start = start
        self.dir = os.path.join(ROOT, self.sid)
        self.proc = None
        self.last = time.time()
        self.subs = []       # [{"name":..., "lang":..., "file":...}]
        self.variant = None  # bestandsnaam van de beeld+geluid-afspeellijst
        self.error = ""

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.alive():
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        shutil.rmtree(self.dir, ignore_errors=True)


_sessions = {}
_lock = threading.Lock()


def probe_streams(input_url, timeout=45):
    cmd = [FFPROBE, "-v", "error", "-rw_timeout", "20000000", "-probesize", "5000000",
           "-analyzeduration", "5000000", "-show_streams", "-of", "json", input_url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffprobe: " + (r.stderr.strip().splitlines() or ["mislukt"])[-1][:200])
    return json.loads(r.stdout or "{}").get("streams", [])


def build_command(sess, input_url, streams, langs=()):
    v = [s for s in streams if s.get("codec_type") == "video"]
    a = [s for s in streams if s.get("codec_type") == "audio"]
    if not v:
        raise RuntimeError("geen beeldspoor gevonden")
    # standaard-geluidsspoor (of het eerste)
    audio = next((s for s in a if (s.get("disposition") or {}).get("default")), a[0] if a else None)
    a_idx = a.index(audio) if audio else None
    subs = [s for s in streams if s.get("codec_type") == "subtitle" and (s.get("codec_name") or "").lower() in TEXT_SUBS]
    def lang_of(s):
        t = str((s.get("tags") or {}).get("language", "")).lower()
        return LANG2.get(t, t[:2])
    # eerst je voorkeurtalen (in volgorde), dan de rest
    order = {l: i for i, l in enumerate(langs)}
    subs = sorted(subs, key=lambda s: order.get(lang_of(s), len(order)))[:MAX_SUBS]
    s_rel = [[x for x in streams if x.get("codec_type") == "subtitle"].index(s) for s in subs]

    audio_codec = ["-c:a", "copy"] if audio and (audio.get("codec_name") or "").lower() in CAST_AUDIO and int(audio.get("channels") or 2) <= 2 \
        else ["-c:a", "aac", "-ac", "2", "-b:a", "192k"]

    maps, vsm = [], []
    groups = max(1, len(subs))  # per ondertitelspoor één (gekopieerde) beeld+geluid-variant: zo wil ffmpeg het
    for g in range(groups):
        maps += ["-map", "0:v:0"]
        if a_idx is not None:
            maps += ["-map", f"0:a:{a_idx}"]
        part = f"v:{g}" + (f",a:{g}" if a_idx is not None else "")
        if subs:
            maps += ["-map", f"0:s:{s_rel[g]}"]
            part += f",s:{g},sgroup:subs"
        vsm.append(part + f",name:g{g}")

    sess.subs = []
    for g, s in enumerate(subs):
        tags = s.get("tags") or {}
        lang = LANG2.get(str(tags.get("language", "")).lower(), str(tags.get("language", ""))[:2].lower() or "und")
        title = str(tags.get("title", "")).strip()
        name = LANG_NAME.get(lang, lang.upper() or f"Ondertitel {g + 1}")
        if title and title.lower() not in name.lower():
            name += f" ({title[:20]})"
        sess.subs.append({"name": name, "lang": lang, "file": f"g{g}_vtt.m3u8"})
    sess.variant = "g0.m3u8"

    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-rw_timeout", "30000000", "-reconnect", "1", "-reconnect_on_network_error", "1", "-reconnect_delay_max", "5",
           # niet sneller lezen dan nodig: vlotte start, daarna 1,5x afspeelsnelheid
           "-readrate", "1.5", "-readrate_initial_burst", "45",
           "-ss", str(max(0, int(sess.start))), "-i", input_url,
           *maps, "-c:v", "copy", *audio_codec]
    if subs:
        cmd += ["-c:s", "webvtt"]
    cmd += ["-muxdelay", "0", "-muxpreload", "0",
            "-f", "hls", "-hls_time", "6", "-hls_list_size", "0", "-hls_playlist_type", "event",
            "-hls_segment_filename", os.path.join(sess.dir, "%v_%05d.ts"),
            "-var_stream_map", " ".join(vsm), os.path.join(sess.dir, "%v.m3u8")]
    return cmd


def get_or_start(target_url, start, input_url, log, langs=()):
    """Geeft een lopende omzetting voor deze film+startpunt, of start een nieuwe (en stopt de oude)."""
    if not FFMPEG or not FFPROBE:
        raise RuntimeError("ffmpeg niet gevonden op de Beelink")
    key = (target_url, int(start))
    with _lock:
        for s in list(_sessions.values()):
            if s.key == key and (s.alive() or os.path.exists(os.path.join(s.dir, s.variant or "x"))):
                s.last = time.time()
                return s
        for s in list(_sessions.values()):  # maar 1 verbinding: oude omzetting stoppen
            s.stop()
            _sessions.pop(s.sid, None)
        sess = Session(key, start)
        os.makedirs(sess.dir, exist_ok=True)
        _sessions[sess.sid] = sess
    streams = probe_streams(input_url)
    cmd = build_command(sess, input_url, streams, langs)
    errlog = open(os.path.join(sess.dir, "ffmpeg.log"), "w", encoding="utf-8", errors="replace")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    sess.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errlog, creationflags=flags)
    log(f"omzetting gestart vanaf {fmt_time(start)} · ondertitels: {', '.join(s['name'] for s in sess.subs) or 'geen'}")
    return sess


def wait_ready(sess, timeout=40):
    """Wacht tot er minstens één videostukje klaar is."""
    end = time.time() + timeout
    playlist = os.path.join(sess.dir, sess.variant)
    while time.time() < end:
        try:
            if ".ts" in open(playlist, encoding="utf-8").read():
                return True
        except OSError:
            pass
        if not sess.alive():
            break
        time.sleep(0.4)
    try:
        sess.error = open(os.path.join(sess.dir, "ffmpeg.log"), encoding="utf-8", errors="replace").read().strip()[-300:]
    except OSError:
        pass
    return False


def master_playlist(sess, base_url):
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS"]
    for s in sess.subs:
        lines.append(f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{s["name"]}",LANGUAGE="{s["lang"]}",'
                     f'DEFAULT=NO,AUTOSELECT=NO,FORCED=NO,URI="{base_url}/{s["file"]}"')
    inf = '#EXT-X-STREAM-INF:BANDWIDTH=6000000,CODECS="avc1.640028,mp4a.40.2"'
    if sess.subs:
        inf += ',SUBTITLES="subs"'
    lines += [inf, f"{base_url}/{sess.variant}", ""]
    return "\n".join(lines)


def file_for(sid, rel):
    """Pad naar een bestand van een omzetting (of None). Houdt de omzetting 'wakker'."""
    sess = _sessions.get(sid)
    if not sess or not re.fullmatch(r"[A-Za-z0-9_]+\.(m3u8|ts|vtt)", rel):
        return None, None
    sess.last = time.time()
    path = os.path.join(sess.dir, rel)
    return (path if os.path.isfile(path) else None), sess


def vtt_with_map(data):
    """Zet expliciet hoe ondertiteltijd en beeldtijd overeenkomen (beeld begint op 0)."""
    text = data.decode("utf-8", "replace")
    if "X-TIMESTAMP-MAP" not in text:
        text = re.sub(r"^WEBVTT[^\n]*\n", "WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:0,LOCAL:00:00:00.000\n", text, count=1)
    return text.encode("utf-8")


def reaper(log):
    while True:
        time.sleep(15)
        with _lock:
            for s in list(_sessions.values()):
                if time.time() - s.last > IDLE_SECONDS:
                    s.stop()
                    _sessions.pop(s.sid, None)
                    log("omzetting gestopt (niet meer bekeken)")


def cleanup_all():
    with _lock:
        for s in list(_sessions.values()):
            s.stop()
        _sessions.clear()
    shutil.rmtree(ROOT, ignore_errors=True)


def fmt_time(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}"
