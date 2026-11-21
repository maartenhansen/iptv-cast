"""IPTV Cast: alles op de Beelink onzichtbaar op de achtergrond laten draaien.

Start (zonder vensters):
  - de thuis-tussenschakel   (home_proxy.py: lijsten, films, omzetting)
  - de Cloudflare Tunnel      (cloudflared: verbinding met de Worker)
en start ze opnieuw als ze stoppen. Meldingen komen in home\\logs\\.

Wordt automatisch gestart bij het aanmelden (via de Windows-map Opstarten).
Handmatig starten:  pyw home\\autostart.pyw
Stoppen: Taakbeheer › 'pythonw.exe' en 'cloudflared.exe' beëindigen.
"""
import os, socket, subprocess, sys, threading, time

HOME = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HOME)
LOGS = os.path.join(HOME, "logs")
CLOUDFLARED = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
MAX_LOG = 5 * 1024 * 1024

os.makedirs(LOGS, exist_ok=True)


def logfile(name):
    path = os.path.join(LOGS, name)
    try:
        if os.path.getsize(path) > MAX_LOG:  # niet eindeloos laten groeien
            os.replace(path, path + ".old")
    except OSError:
        pass
    return open(path, "a", encoding="utf-8", errors="replace", buffering=1)


SUP = logfile("autostart.log")


def note(msg):
    SUP.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def single_instance():
    """Voorkomt dat het opstartprogramma twee keer draait."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 8039))
        s.listen(1)
        return s
    except OSError:
        return None


def keep_running(name, cmd, log_name):
    delay = 5
    while True:
        out = logfile(log_name)
        note(f"{name} starten")
        started = time.time()
        try:
            p = subprocess.Popen(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            code = p.wait()
            note(f"{name} gestopt (code {code})")
        except Exception as e:
            note(f"{name} kon niet starten: {e}")
        out.close()
        # snel opnieuw crashen? dan langer wachten (max 5 min)
        delay = 5 if time.time() - started > 120 else min(delay * 2, 300)
        time.sleep(delay)


def main():
    lock = single_instance()
    if lock is None:
        note("draait al: tweede start genegeerd")
        return
    py = sys.executable  # pythonw.exe
    jobs = [("tussenschakel", [py, "-u", os.path.join(HOME, "home_proxy.py")], "tussenschakel.log")]
    token = os.path.join(HOME, "tunnel.token")
    if os.path.exists(CLOUDFLARED) and os.path.exists(token):
        jobs.append(("tunnel", [CLOUDFLARED, "tunnel", "--no-autoupdate", "run", "--token-file", token], "tunnel.log"))
    else:
        note("tunnel niet gestart: cloudflared of home\\tunnel.token ontbreekt")
    for name, cmd, log_name in jobs:
        threading.Thread(target=keep_running, args=(name, cmd, log_name), daemon=True).start()
    while True:
        time.sleep(3600)


main()
