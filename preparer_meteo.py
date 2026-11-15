"""
Robot météo — A.C.I.S Marine / Météo Marine Tunisie.
Vérifie toutes les heures si NOAA a publié un nouveau run GFS (toutes les 6 h) et le prend aussitôt, télécharge chez NOAA vent + rafales + vagues sur la zone Tunisie
(16 jours, pas 1 h pendant 5 jours puis 3 h) et publie deux fichiers :
  Tunisie_1h.grb2 : complet (1 h puis 3 h)
  Tunisie_3h.grb2 : allégé (3 h partout)
Les téléphones téléchargent un seul fichier de quelques Mo au lieu de centaines de petites demandes.
"""
import datetime as dt
import os
import sys

import grib_download as gd

UNITES = {0: 60, 1: 3600, 2: 86400, 10: 3 * 3600, 11: 6 * 3600, 12: 12 * 3600, 13: 1}


def echeance(m: bytes) -> float:
    """Échéance (heures) lue dans la section 4 du message GRIB2."""
    p = 16
    while p < len(m) - 4:
        lg = int.from_bytes(m[p:p + 4], "big")
        if m[p + 4] == 4:
            return int.from_bytes(m[p + 18:p + 22], "big") * UNITES.get(m[p + 17], 3600) / 3600
        p += lg
    return 0.0


def messages(octets: bytes):
    """Découpe un fichier GRIB2 en messages (octets bruts)."""
    i = 0
    while True:
        j = octets.find(b"GRIB", i)
        if j < 0 or j + 16 > len(octets):
            return
        lg = int.from_bytes(octets[j + 8:j + 16], "big")
        yield j, octets[j:j + lg]
        i = j + lg


def main():
    dossier = sys.argv[1] if len(sys.argv) > 1 else "."
    maintenant = dt.datetime.now(dt.timezone.utc)
    tel = gd.Telechargeur(maintenant, log=print)
    vent = gd.modele_gfs(16 * 24, 1, 3)
    vagues = gd.modele_vagues(16 * 24, 1, "0p25", 3)
    run = tel.dernier_run(vagues)                    # même run pour le vent et les vagues
    deja = open(os.path.join(dossier, "run_publie.txt")).read().strip() if os.path.exists(os.path.join(dossier, "run_publie.txt")) else ""
    if deja == f"{run[0]}{run[1]}":
        print("Run", run, "déjà publié : rien à faire")
        open(os.path.join(dossier, "rien_a_faire"), "w").close()
        return
    f1 = tel.telecharger(vent, os.path.join(dossier, "vent.grb2"), run=run)
    f2 = tel.telecharger(vagues, os.path.join(dossier, "vagues.grb2"), run=run)
    complet = os.path.join(dossier, "Tunisie_1h.grb2")
    gd.fusionner([f1, f2], complet)
    brut = open(complet, "rb").read()
    garde = bytearray()
    for debut, m in messages(brut):
        if echeance(m) % 3 == 0:
            garde += m
    open(os.path.join(dossier, "Tunisie_3h.grb2"), "wb").write(bytes(garde))
    for f in ("Tunisie_1h.grb2", "Tunisie_3h.grb2"):
        print(f, round(os.path.getsize(os.path.join(dossier, f)) / 1e6, 2), "Mo")
    open(os.path.join(dossier, "run.txt"), "w").write(f"{run[0]}{run[1]}")
    print("Run", run)


if __name__ == "__main__":
    main()
