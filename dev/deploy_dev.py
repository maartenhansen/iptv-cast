"""Zet de ONTWIKKELversie van de app online op een aparte GitHub-site:
    https://maartenhansen.github.io/iptv-cast-dev/

Neemt de app-bestanden uit deze map (branch dev), voegt een duidelijk DEV-label toe,
laat het productiedomein (CNAME) weg en publiceert naar de repo iptv-cast-dev.
De productie (stream.hansen-tools.be) wordt hier nooit door aangeraakt.

Gebruik:  py dev/deploy_dev.py
"""
import os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "maartenhansen/iptv-cast-dev"
FILES = ["index.html", "app.js", "styles.css", "sw.js", "manifest.webmanifest", "icons"]


def run(*cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"Mislukt: {' '.join(cmd)}\n{r.stderr.strip()}")
    return r.stdout.strip()


def main():
    branch = run("git", "branch", "--show-current", cwd=ROOT)
    if branch != "dev":
        sys.exit(f"Dit script publiceert alleen vanaf de branch 'dev' (nu: {branch}).")
    commit = run("git", "rev-parse", "--short", "HEAD", cwd=ROOT)
    dirty = bool(run("git", "status", "--porcelain", "--", *FILES, cwd=ROOT))
    out = tempfile.mkdtemp(prefix="iptvcast-devsite-")
    for f in FILES:
        src = os.path.join(ROOT, f)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, os.path.join(out, f))

    label = f"DEV {commit}{'+' if dirty else ''}"
    # Duidelijk herkenbaar als ontwikkelversie
    idx = open(os.path.join(out, "index.html"), encoding="utf-8").read()
    idx = idx.replace("<title>IPTV Cast</title>", "<title>IPTV Cast DEV</title>")
    idx = idx.replace('<meta name="theme-color" content="#111318">', '<meta name="theme-color" content="#b45309">')
    idx = idx.replace("</body>", f'<div style="position:fixed;top:calc(env(safe-area-inset-top) + 4px);right:56px;z-index:50;'
                                 f'background:#b45309;color:#fff;font:700 11px system-ui;padding:3px 7px;border-radius:6px">{label}</div>\n</body>')
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(idx)
    man = open(os.path.join(out, "manifest.webmanifest"), encoding="utf-8").read()
    man = man.replace('"name": "IPTV Cast"', '"name": "IPTV Cast DEV"').replace('"short_name": "IPTV Cast"', '"short_name": "IPTV DEV"')
    man = re.sub(r'"theme_color": "[^"]+"', '"theme_color": "#b45309"', man)
    open(os.path.join(out, "manifest.webmanifest"), "w", encoding="utf-8").write(man)
    sw = open(os.path.join(out, "sw.js"), encoding="utf-8").read()
    sw = re.sub(r"const CACHE = '([^']+)';", lambda m: f"const CACHE = '{m.group(1)}-dev-{commit}';", sw)
    open(os.path.join(out, "sw.js"), "w", encoding="utf-8").write(sw)
    open(os.path.join(out, ".nojekyll"), "w").close()

    run("git", "init", "-q", "-b", "main", cwd=out)
    run("git", "add", "-A", cwd=out)
    run("git", "-c", "user.name=maartenhansen", "-c", "user.email=103786464+maartenhansen@users.noreply.github.com",
        "commit", "-q", "-m", f"Dev-site van iptv-cast {label}", cwd=out)
    run("git", "push", "-q", "-f", f"https://github.com/{REPO}.git", "main", cwd=out)
    shutil.rmtree(out, ignore_errors=True)
    print(f"Gepubliceerd: {label} -> https://maartenhansen.github.io/iptv-cast-dev/")


if __name__ == "__main__":
    main()
