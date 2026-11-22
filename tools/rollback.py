"""Zet de productie terug naar een eerdere versie (of weer vooruit naar een latere).

Gebruik (in PowerShell, vanuit de productiemap):
    cd C:\\Users\\maart\\Claude\\Projects\\iptv-cast
    py tools\\rollback.py            -> toont alle versies
    py tools\\rollback.py 1.0.0      -> productie terug naar versie 1.0.0

Wat het doet:
  - app, Worker en thuis-tussenschakel krijgen exact de code van die versie
  - de geschiedenis blijft bewaard (er wordt niets gewist): er komt een nieuwe stap "Teruggezet naar v1.0.0" bij
  - CHANGELOG.md en deze scripts zelf blijven de nieuwste versie
  - dev wordt niet aangeraakt; de volgende release.py zet de dev-versie gewoon weer in productie
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (ROOT, deploy_worker, git, parse_version, prepend_changelog, require_production, restart_proxy, say,
                    versions, wait_for_site)


def main():
    vs = versions()
    if len(sys.argv) < 2:
        say("Productieversies (nieuwste eerst):")
        for tag, date, subj in vs:
            say(f"  {tag:<9} {date}  {subj}")
        say("\nTerugzetten:  py tools\\rollback.py <versie>    bv. py tools\\rollback.py 1.0.0")
        return
    v = parse_version(sys.argv[1])
    require_production()
    tag = f"v{v}"
    if not git("tag", "-l", tag):
        sys.exit(f"Versie {tag} bestaat niet. Bekijk de lijst met:  py tools\\rollback.py")
    git("fetch", "-q", "origin")
    if git("rev-list", "--count", "main..origin/main") != "0":
        sys.exit("GitHub heeft wijzigingen die hier ontbreken. Dat hoort niet; vraag eerst na wat er gebeurd is.")

    changelog = git("show", "HEAD:CHANGELOG.md", check=False)
    say(f"Productie terugzetten naar {tag}…")
    git("read-tree", "-u", "--reset", tag)
    # De versiegeschiedenis en de beheerscripts zelf blijven de nieuwste.
    git("checkout", "HEAD", "--", "tools")
    if changelog:
        prepend_changelog(changelog, f"## Teruggezet naar {tag} ({time.strftime('%Y-%m-%d %H:%M')})")
    git("add", "-A")
    if not git("status", "--porcelain", "--untracked-files=no"):
        sys.exit(f"Productie is al gelijk aan {tag}; niets te doen.")
    git("commit", "-q", "-m", f"Teruggezet naar {tag}")
    git("push", "-q", "origin", "main")
    say("  Naar GitHub gestuurd (de website wordt nu bijgewerkt).")
    deploy_worker()
    restart_proxy()
    app = open(os.path.join(ROOT, "app.js"), encoding="utf-8").read()
    if "const APP_VERSION = " in app:
        wait_for_site(v)
    say(f"\nKlaar: productie draait nu de code van {tag}.")


if __name__ == "__main__":
    main()
