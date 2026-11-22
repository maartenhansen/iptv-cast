"""Zet de huidige dev-versie in productie als een nieuwe, genummerde versie.

Gebruik (in PowerShell, vanuit de productiemap):
    cd C:\\Users\\maart\\Claude\\Projects\\iptv-cast
    py tools\\release.py 1.2.0

Wat het doet:
  1. productie (main) krijgt exact de inhoud van dev
  2. versienummer in de app + een stukje in CHANGELOG.md
  3. git-tag v1.2.0, en alles naar GitHub (=> stream.hansen-tools.be)
  4. productie-Worker online zetten en de thuis-tussenschakel herstarten
  5. dev bijwerken zodat die het versienummer en de changelog ook heeft
Terugzetten naar een oudere versie: zie tools\\rollback.py.
"""
import os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (ROOT, deploy_worker, dev_worktree, git, parse_version, prepend_changelog, require_production,
                    restart_proxy, say, set_version, versions, wait_for_site)


def main():
    if len(sys.argv) < 2:
        last = versions()[0][0] if versions() else "geen"
        sys.exit(f"Gebruik: py tools\\release.py <versie>   (laatste versie: {last})")
    v = parse_version(sys.argv[1])
    require_production()
    if git("tag", "-l", f"v{v}"):
        sys.exit(f"Versie v{v} bestaat al. Kies een hoger nummer.")
    git("fetch", "-q", "origin")
    if git("rev-list", "--count", "main..origin/main") != "0":
        sys.exit("GitHub heeft wijzigingen die hier ontbreken. Dat hoort niet; vraag eerst na wat er gebeurd is.")

    changes = [s for s in git("log", "--no-merges", "--reverse", "--format=%s", "main..dev").splitlines()
               if s and not s.startswith(("Versie ", "Teruggezet naar "))]
    if not changes:
        sys.exit("dev bevat niets nieuws ten opzichte van productie.")
    old_changelog = git("show", "main:CHANGELOG.md", check=False) or git("show", "dev:CHANGELOG.md", check=False) or ""

    say(f"Versie {v} maken met {len(changes)} wijziging(en) uit dev…")
    # Productie krijgt exact de inhoud van dev (ook na een eerdere rollback).
    git("merge", "--no-ff", "--no-commit", "-X", "theirs", "dev", check=False)
    git("read-tree", "-u", "--reset", "dev")
    set_version(v)
    entry = f"## v{v} ({time.strftime('%Y-%m-%d')})\n" + "\n".join(f"- {c}" for c in changes)
    prepend_changelog(old_changelog, entry)
    git("add", "-A")
    if not os.path.exists(os.path.join(ROOT, ".git", "MERGE_HEAD")):
        sys.exit("Samenvoegen met dev mislukt. Er is niets online gezet.")
    git("commit", "-q", "-m", f"Versie {v}\n\n" + "\n".join(f"- {c}" for c in changes))
    git("tag", "-a", f"v{v}", "-m", f"Versie {v}: " + "; ".join(changes)[:300])
    say(f"  Versie v{v} vastgelegd.")

    git("push", "-q", "origin", "main", f"v{v}")
    say("  Naar GitHub gestuurd (de website wordt nu bijgewerkt).")
    deploy_worker()
    restart_proxy()

    dev = dev_worktree()
    if dev:
        r = subprocess.run(["git", "merge", "-q", "--ff-only", "main"], cwd=dev, capture_output=True, text=True)
        if r.returncode == 0:
            subprocess.run(["git", "push", "-q", "origin", "dev"], cwd=dev, capture_output=True)
            say("  dev bijgewerkt met het versienummer en de changelog.")
        else:
            say(f"  Let op: dev kon niet automatisch bijgewerkt worden: {r.stderr.strip()}")
    wait_for_site(v)
    say(f"\nKlaar: versie {v} staat in productie.")


if __name__ == "__main__":
    main()
