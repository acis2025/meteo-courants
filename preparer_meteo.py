"""
Robot météo — A.C.I.S Marine / Météo Marine Tunisie.
Vérifie toutes les heures si NOAA a publié un nouveau run GFS (toutes les 6 h) et le prend aussitôt, télécharge chez NOAA vent + rafales + vagues sur la zone Tunisie
(16 jours, pas 1 h pendant 5 jours puis 3 h) et publie deux fichiers :
  Tunisie_1h.grb2 : complet (1 h puis 3 h)
  Tunisie_3h.grb2 : allégé (3 h partout)
et, à part (pour ne pas gêner les anciennes versions de l'appli), la pluie et les nuages :
  Ciel_1h.grb2 / Ciel_3h.grb2 : PRATE (intensité de pluie sur chaque pas) + TCDC (nébulosité totale, %)
Les téléphones téléchargent un seul fichier de quelques Mo au lieu de centaines de petites demandes.
"""
import datetime as dt
import os
import sys

import grib2
import grib_download as gd
from grib2_ecrire import message

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


def ecrire_ciel(brut: str, dossier: str, ref: dt.datetime) -> None:
    """NOAA donne la pluie en moyenne depuis le début d'une fenêtre (0-1 h, 0-2 h… remise à zéro toutes les 6 h) :
    on reconstitue le cumul depuis le début du run, puis l'intensité réelle sur chaque pas (mm/h)."""
    champs = grib2.lire_fichier(brut)
    pluies = sorted((c for c in champs if c.code == "PRATE"), key=lambda c: (c.echeance_h, c.debut_h))
    if not pluies:
        raise RuntimeError("aucune pluie (PRATE) dans le fichier NOAA")
    g = pluies[0]
    n = g.ni * g.nj
    cumul = {0.0: [0.0] * n}                       # mm cumulés depuis le run
    for c in pluies:
        a, b = c.debut_h, c.echeance_h
        if b in cumul or b <= a:
            continue
        base = cumul.get(a)
        if base is None:                           # fenêtre dont le début manque : on part du dernier cumul connu
            avant = max(h for h in cumul if h < b)
            base, a = cumul[avant], avant
        cumul[b] = [x + max(0.0, v or 0.0) * 3600 * (b - a) for x, v in zip(base, c.valeurs)]
    nuages = {}
    for c in champs:                               # nébulosité : la valeur instantanée si NOAA la donne
        if c.code == "TCDC":
            inst = c.debut_h == c.echeance_h
            if c.echeance_h not in nuages or (inst and not nuages[c.echeance_h][0]):
                nuages[c.echeance_h] = (inst, c)
    heures = sorted(h for h in cumul if h > 0)

    def ecrire(nom: str, garder) -> None:
        sortie, precedente = bytearray(), 0.0
        for h in heures:
            if not garder(h):
                continue
            ecart = h - precedente
            mm_h = [max(0.0, (x - y) / ecart) for x, y in zip(cumul[h], cumul[precedente])]
            precedente = h
            minutes = int(round(h * 60))
            sortie += message(0, 1, 7, ref, minutes, g.lat1, g.lon1, g.dlat, g.dlon, g.ni, g.nj,
                              [v / 3600 for v in mm_h], decimales=7)          # kg/m²/s (unité GRIB)
            if h in nuages:
                t = nuages[h][1]
                sortie += message(0, 6, 1, ref, minutes, t.lat1, t.lon1, t.dlat, t.dlon, t.ni, t.nj,
                                  [None if v is None else max(0.0, min(100.0, v)) for v in t.valeurs], decimales=0,
                                  niveau=10)                                   # atmosphère entière
        open(os.path.join(dossier, nom + ".part"), "wb").write(bytes(sortie))
        os.replace(os.path.join(dossier, nom + ".part"), os.path.join(dossier, nom))
        print(nom, round(len(sortie) / 1e6, 2), "Mo")

    ecrire("Ciel_1h.grb2", lambda h: True)
    ecrire("Ciel_3h.grb2", lambda h: abs(h / 3 - round(h / 3)) < 1e-6)


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
    try:                                             # pluie + nuages : un échec ne bloque pas le vent et les vagues
        ciel = gd.modele_ciel(16 * 24, 1, 3)
        fc = tel.telecharger(ciel, os.path.join(dossier, "ciel_brut.grb2"), run=run)
        ecrire_ciel(fc, dossier, dt.datetime.strptime(run[0] + run[1], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc))
    except Exception as e:                           # noqa: BLE001
        print("Pluie et nuages non préparés :", e)
        for f in ("Ciel_1h.grb2", "Ciel_3h.grb2"):
            if os.path.exists(os.path.join(dossier, f)):
                os.remove(os.path.join(dossier, f))
    open(os.path.join(dossier, "run.txt"), "w").write(f"{run[0]}{run[1]}")
    print("Run", run)


if __name__ == "__main__":
    main()
