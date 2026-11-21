# Start de ONTWIKKELversie van het Beelink-programma (poort 8031) op de achtergrond.
# Niet in de map Opstarten zetten: alleen nodig tijdens het ontwikkelen.
import os, runpy, sys
sys.argv = [sys.argv[0], "--dev"]
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "autostart.pyw"), run_name="__main__")
