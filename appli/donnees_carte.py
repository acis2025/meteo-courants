"""
Prépare les données de la carte (JSON compact) à partir d'un fichier GRIB2,
et fabrique la page HTML de la carte (moteur commun à l'appli Android et aux prototypes).

Format : chaque champ est un tableau Int16 (petit-boutiste) encodé en base64,
toutes échéances à la suite ; -32768 = absent (terre pour les vagues).
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(ICI))
import grib2  # noqa: E402

ECHELLES = {"UGRD": 10, "VGRD": 10, "GUST": 10, "HTSGW": 100, "PERPW": 10, "DIRPW": 1,
            "PRATE": 100, "TCDC": 1}          # pluie en mm/h ×100, nébulosité en %
CONVERSION = {"PRATE": 3600}                  # kg/m²/s -> mm/h
GRILLE = {"HTSGW": "vagues", "PERPW": "vagues", "DIRPW": "vagues", "PRATE": "ciel", "TCDC": "ciel"}
COURANTS = {"UOGRD": 100, "VOGRD": 100}       # autre modèle (Copernicus) : recalé sur les heures GFS
MAREE = "DSLM"                                # hauteur d'eau horaire (Copernicus), gardée heure par heure
ABSENT = -32768


def _grille(c) -> dict:
    return {"lat1": c.lat1, "lon1": c.lon1, "dlat": c.dlat, "dlon": c.dlon, "ni": c.ni, "nj": c.nj}


def _courants(courants: list, prev, echeances: list, sortie: dict):
    """Courants Copernicus (autre heure de référence) : interpolés dans le temps aux heures de la carte."""
    import datetime as dt
    for code, f in COURANTS.items():
        serie = sorted((c.date_prevue, c) for c in courants if c.code == code)
        if not serie:
            return
        modele = serie[0][1]
        n, brut, utile = modele.ni * modele.nj, bytearray(), 0
        for e in echeances:
            t = prev.date_ref + dt.timedelta(hours=e)
            avant = max((x for x in serie if x[0] <= t), default=None, key=lambda x: x[0])
            apres = min((x for x in serie if x[0] >= t), default=None, key=lambda x: x[0])
            if avant is None or apres is None:
                vals = [None] * n                      # hors de la période couverte par les courants
            elif apres[0] == avant[0]:
                vals = avant[1].valeurs
            else:
                r = (t - avant[0]) / (apres[0] - avant[0])
                vals = [None if a is None or b is None else a + (b - a) * r
                        for a, b in zip(avant[1].valeurs, apres[1].valeurs)]
            utile += avant is not None and apres is not None
            vals = [ABSENT if v is None else max(-32767, min(32767, int(round(v * f)))) for v in vals]
            brut += struct.pack("<%dh" % len(vals), *vals)
        if not utile:
            return
        sortie["grilles"].setdefault("courant", _grille(modele))
        sortie["champs"][code] = {"g": "courant", "f": f, "b64": base64.b64encode(bytes(brut)).decode()}


def _maree(serie: list, sortie: dict) -> None:
    """Hauteur d'eau heure par heure (cm) : courbe de marée au point touché et couche « Marée »."""
    import datetime as dt
    serie = sorted(serie, key=lambda c: c.date_prevue)
    t0 = serie[0].date_prevue
    heures = {int(round((c.date_prevue - t0).total_seconds() / 3600)): c for c in serie}
    n = max(heures) + 1
    modele = serie[0]
    brut = bytearray()
    for h in range(n):
        c = heures.get(h)
        vals = c.valeurs if c is not None else [None] * (modele.ni * modele.nj)
        vals = [ABSENT if v is None else max(-32767, min(32767, int(round(v * 100)))) for v in vals]
        brut += struct.pack("<%dh" % len(vals), *vals)
    sortie["maree"] = {"g": _grille(modele), "t0": t0.strftime("%Y-%m-%dT%H:%MZ"), "n": n, "f": 100,
                       "b64": base64.b64encode(bytes(brut)).decode()}


def construire(champs: list, pas_max_h: float = None, garder=None) -> dict:
    courants = [c for c in champs if c.code in COURANTS]
    maree = [c for c in champs if c.code == MAREE]
    champs = [c for c in champs if c.code not in COURANTS and c.code != MAREE]
    prev = grib2.Previsions(champs)
    echeances = [e for e in prev.echeances if (pas_max_h is None or e <= pas_max_h)]
    if garder:
        echeances = [e for e in echeances if garder(e)]
    sortie = {"run": prev.date_ref.strftime("%Y-%m-%dT%H:%MZ"), "pas": echeances,
              "grilles": {}, "champs": {}}
    for code, f in ECHELLES.items():
        premiers = [prev.par_echeance[e].get(code) for e in echeances]
        presents = [i for i, c in enumerate(premiers) if c is not None]
        # Une échéance manquante (coupure réseau…) ne doit pas faire disparaître toute la couche :
        # elle est recalculée entre ses deux voisines (direction : la plus proche), sinon laissée vide.
        if not presents or len(presents) < len(premiers) / 2:
            continue
        g = GRILLE.get(code, "vent")
        conv = CONVERSION.get(code, 1)
        modele = premiers[presents[0]]
        sortie["grilles"].setdefault(g, _grille(modele))
        n = len(modele.valeurs)
        brut = bytearray()
        for i, c in enumerate(premiers):
            if c is not None:
                vals = c.valeurs
            else:
                avant = max((p for p in presents if p < i), default=None)
                apres = min((p for p in presents if p > i), default=None)
                if avant is not None and apres is not None and code != "DIRPW":
                    t = (echeances[i] - echeances[avant]) / (echeances[apres] - echeances[avant])
                    a, b = premiers[avant].valeurs, premiers[apres].valeurs
                    vals = [None if x is None or y is None else x + (y - x) * t for x, y in zip(a, b)]
                elif avant is not None or apres is not None:
                    proche = min((p for p in (avant, apres) if p is not None), key=lambda p: abs(echeances[p] - echeances[i]))
                    vals = premiers[proche].valeurs
                else:
                    vals = [None] * n
            vals = [ABSENT if v is None else max(-32767, min(32767, int(round(v * conv * f)))) for v in vals]
            brut += struct.pack("<%dh" % len(vals), *vals)
        sortie["champs"][code] = {"g": g, "f": f, "b64": base64.b64encode(bytes(brut)).decode()}
    if courants:
        _courants(courants, prev, echeances, sortie)
    if maree:
        _maree(maree, sortie)
    return sortie


def page_html(donnees: dict, client: str = "", options: dict = None) -> str:
    gabarit = None
    try:                                           # carte de la mise à jour en ligne, s'il y en a une
        import maj_en_ligne
        b = maj_en_ligne.ressource("carte.html")
        gabarit = b.decode("utf-8") if b is not None else None
    except ImportError:
        pass
    if gabarit is None:
        gabarit = open(os.path.join(ICI, "carte.html"), encoding="utf-8").read()
    cfg = {"client": client}
    cfg.update(options or {})
    logo = os.path.join(os.path.dirname(ICI), "ressources", "logo_256.png")
    if not os.path.exists(logo):
        logo = os.path.join(os.path.dirname(ICI), "ressources", "logo_96.png")
    if os.path.exists(logo):
        cfg["logo"] = "data:image/png;base64," + base64.b64encode(open(logo, "rb").read()).decode()
    return (gabarit.replace("/*__DONNEES__*/null", json.dumps(donnees, separators=(",", ":")))
                   .replace("/*__CONFIG__*/null", json.dumps(cfg, ensure_ascii=False)))
