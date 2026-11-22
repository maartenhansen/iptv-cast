"""Gedeelde hulpjes voor release.py en rollback.py (versiebeheer van IPTV Cast).

Werkwijze:
  - Ontwikkelen gebeurt op de branch `dev` (map iptv-cast-dev).
  - Productie = branch `main` (map iptv-cast) = wat op stream.hansen-tools.be staat.
  - Elke productieversie krijgt een git-tag vX.Y.Z en een stukje in CHANGELOG.md.
"""
import os, re, shutil, subprocess, sys, time, urllib.request

SITE = "https://stream.hansen-tools.be"
TEST = os.environ.get("IPTV_RELEASE_TEST") == "1"  # alleen voor het testen van deze scripts: niets online zetten


def run(*cmd, cwd=None, check=True, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if check and r.returncode != 0:
        sys.exit(f"\nMislukt: {' '.join(cmd)}\n{(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def git(*args, check=True):
    return run("git", *args, cwd=ROOT, check=check)


def toplevel():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("Start dit script vanuit de productiemap (iptv-cast).")
    return os.path.normpath(r.stdout.strip())


ROOT = toplevel()


def say(msg):
    print(msg, flush=True)


def require_production():
    """Alleen vanuit de productiemap (branch main), zonder openstaande wijzigingen."""
    branch = git("branch", "--show-current")
    if branch != "main":
        sys.exit(f"Dit script werkt alleen in de productiemap op branch 'main' (nu: {branch}).\n"
                 f"Ga eerst naar die map, bv.:  cd C:\\Users\\maart\\Claude\\Projects\\iptv-cast")
    if git("status", "--porcelain", "--untracked-files=no"):
        sys.exit("Er zijn niet-opgeslagen wijzigingen in de productiemap. Die mogen daar niet zijn; los dit eerst op.")


def parse_version(v):
    v = v.strip().lstrip("vV")
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        sys.exit(f"Ongeldig versienummer '{v}'. Gebruik bv. 1.2.0")
    return v


def versions():
    """Alle productieversies, nieuwste eerst: [(tag, datum, omschrijving)]."""
    out = git("for-each-ref", "--sort=-creatordate", "--format=%(refname:short)|%(creatordate:short)|%(contents:subject)",
              "refs/tags/v*")
    return [tuple(l.split("|", 2)) for l in out.splitlines() if l]


def set_version(v):
    """Zet het versienummer in de app (zichtbaar onder Instellingen) en in de cache-naam van de service worker."""
    for name, pat, rep in [("app.js", r"const APP_VERSION = '[^']*';", f"const APP_VERSION = '{v}';"),
                           ("sw.js", r"const CACHE = '[^']*';", f"const CACHE = 'iptv-cast-{v}';")]:
        p = os.path.join(ROOT, name)
        s = open(p, encoding="utf-8").read()
        s2, n = re.subn(pat, rep, s, count=1)
        if not n:
            sys.exit(f"Versienummer niet gevonden in {name}.")
        open(p, "w", encoding="utf-8", newline="\n").write(s2)


def prepend_changelog(base_text, entry):
    head = "# Versies van IPTV Cast\n\n"
    body = base_text[len(head):] if base_text.startswith(head) else base_text
    open(os.path.join(ROOT, "CHANGELOG.md"), "w", encoding="utf-8", newline="\n").write(head + entry.rstrip() + "\n\n" + body.lstrip())


def deploy_worker():
    if TEST:
        return say("  (test: Worker niet online gezet)")
    cf = shutil.which("cf.cmd") or shutil.which("cf")
    if not cf:
        sys.exit("cf niet gevonden. Start dit script in een gewoon PowerShell-venster (Terminal), niet vanuit een ander programma.")
    env = {k: v for k, v in os.environ.items() if k != "IPTV_ENV"}  # zonder IPTV_ENV = productie-Worker "iptv-cast"
    say("  Worker online zetten (dit duurt ongeveer een halve minuut)…")
    r = subprocess.run([cf, "deploy", "--secrets-file", "secrets.env"], cwd=os.path.join(ROOT, "worker"), env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"Worker online zetten mislukt:\n{(r.stderr or r.stdout)[-2000:]}")
    m = re.search(r"Current Version ID: (\S+)", r.stdout + r.stderr)
    say(f"  Worker online{f' (versie {m.group(1)[:8]})' if m else ''}.")


def restart_proxy():
    """Herstart de thuis-tussenschakel van deze map; het opstartprogramma start hem binnen ~5 s opnieuw."""
    if TEST:
        return say("  (test: tussenschakel niet herstart)")
    me = os.path.join(ROOT, "home", "home_proxy.py").lower()
    ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
          "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }")
    out = run("powershell", "-NoProfile", "-Command", ps, check=False)
    pids = [l.split("|", 1)[0] for l in out.splitlines() if "|" in l and me in l.split("|", 1)[1].lower()]
    for pid in pids:
        run("taskkill", "/PID", pid, "/F", check=False)
    say(f"  Thuis-tussenschakel herstart ({len(pids)} proces{'sen' if len(pids) != 1 else ''})."
        if pids else "  Thuis-tussenschakel draaide niet; het opstartprogramma start hem bij de volgende start van de Beelink.")


def wait_for_site(v):
    if TEST:
        return
    say(f"  Wachten tot {SITE} versie {v} toont (kan 1 à 2 minuten duren)…")
    for _ in range(40):
        try:
            req = urllib.request.Request(f"{SITE}/app.js?v={time.time()}", headers={"Cache-Control": "no-cache"})
            if f"const APP_VERSION = '{v}';" in urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "replace"):
                return say(f"  {SITE} toont versie {v}.")
        except Exception:
            pass
        time.sleep(6)
    say(f"  Let op: {SITE} toont versie {v} nog niet. Controleer het binnen enkele minuten opnieuw.")


def dev_worktree():
    out = git("worktree", "list", "--porcelain")
    path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[9:]
        elif line == "branch refs/heads/dev":
            return os.path.normpath(path)
    return None
