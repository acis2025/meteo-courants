"""
Lecteur GRIB2 en Python pur (aucune bibliothèque externe) — Météo Marine Tunisie.

Gère ce que produit le découpage NOAA NOMADS :
  - grille régulière latitude/longitude (modèle de grille 3.0)
  - produit à une échéance (modèle 4.0) ou moyenne/cumul sur un intervalle (modèle 4.8 : on prend la fin)
  - « simple packing » (modèle de représentation 5.0) avec ou sans masque (bitmap, section 6)
Les autres encodages lèvent une erreur explicite.

Usage :
    champs = lire_fichier("Tunisie_complet_….grb2")
    for c in champs: print(c.nom, c.date_prevue, c.valeurs[:5])
"""
from __future__ import annotations

import datetime as dt
import math
import struct

# (discipline, catégorie, numéro) -> (code court, libellé, unité)
PARAMETRES = {
    (0, 2, 2): ("UGRD", "Vent U (10 m)", "m/s"),
    (0, 2, 3): ("VGRD", "Vent V (10 m)", "m/s"),
    (0, 2, 22): ("GUST", "Rafales", "m/s"),
    (0, 3, 1): ("PRMSL", "Pression mer", "Pa"),
    (0, 1, 7): ("PRATE", "Précipitations", "kg/m²/s"),
    (0, 6, 1): ("TCDC", "Nébulosité totale", "%"),
    (10, 3, 1): ("DSLM", "Niveau de la mer (marée)", "m"),
    (10, 0, 3): ("HTSGW", "Hauteur significative des vagues", "m"),
    (10, 0, 4): ("WVDIR", "Direction mer du vent", "°"),
    (10, 0, 5): ("WVHGT", "Hauteur mer du vent", "m"),
    (10, 0, 6): ("WVPER", "Période mer du vent", "s"),
    (10, 0, 10): ("DIRPW", "Direction principale des vagues", "°"),
    (10, 0, 11): ("PERPW", "Période principale des vagues", "s"),
    (10, 1, 2): ("UOGRD", "Courant U (surface)", "m/s"),
    (10, 1, 3): ("VOGRD", "Courant V (surface)", "m/s"),
}
_UNITE_TEMPS = {0: 60, 1: 3600, 2: 86400, 10: 3 * 3600, 11: 6 * 3600, 12: 12 * 3600, 13: 1}


class ErreurGrib(Exception):
    pass


class Champ:
    """Un champ 2D : grille ni × nj, valeurs en ordre ligne (None = terre/absent)."""
    __slots__ = ("code", "nom", "unite", "niveau", "date_ref", "echeance_h", "debut_h", "ni", "nj",
                 "lat1", "lon1", "dlat", "dlon", "valeurs")

    @property
    def date_prevue(self) -> dt.datetime:
        return self.date_ref + dt.timedelta(hours=self.echeance_h)

    def lat(self, j: int) -> float:
        return self.lat1 + j * self.dlat

    def lon(self, i: int) -> float:
        return self.lon1 + i * self.dlon

    def valeur(self, i: int, j: int):
        return self.valeurs[j * self.ni + i]

    def interpoler(self, lat: float, lon: float):
        """Valeur bilinéaire au point (lat, lon) ; None si hors grille ou sur terre."""
        x = (lon - self.lon1) / self.dlon
        y = (lat - self.lat1) / self.dlat
        i0, j0 = int(math.floor(x)), int(math.floor(y))
        if not (0 <= i0 < self.ni and 0 <= j0 < self.nj):
            return None
        i1, j1 = min(i0 + 1, self.ni - 1), min(j0 + 1, self.nj - 1)
        fx, fy = x - i0, y - j0
        coins = [(self.valeur(i0, j0), (1 - fx) * (1 - fy)), (self.valeur(i1, j0), fx * (1 - fy)),
                 (self.valeur(i0, j1), (1 - fx) * fy), (self.valeur(i1, j1), fx * fy)]
        somme = sum(p for v, p in coins if v is not None)
        if somme < 0.25:
            return None
        return sum(v * p for v, p in coins if v is not None) / somme

    def __repr__(self):
        return f"<{self.code} +{self.echeance_h}h {self.ni}x{self.nj}>"


def _sections(b: bytes, debut: int, fin: int):
    p = debut + 16
    while p < fin - 4:
        lg = int.from_bytes(b[p:p + 4], "big")
        if lg < 5:
            raise ErreurGrib("section invalide")
        yield b[p + 4], b[p:p + lg]
        p += lg


def _signe(v: int, nbits: int) -> int:
    """Entiers signés GRIB2 : bit de poids fort = signe."""
    m = 1 << (nbits - 1)
    return -(v & (m - 1)) if v & m else v


def _depaqueter(donnees: bytes, n: int, nbits: int) -> list:
    if nbits == 0:
        return [0] * n
    grand = int.from_bytes(donnees, "big")
    total = len(donnees) * 8
    masque = (1 << nbits) - 1
    return [(grand >> (total - (k + 1) * nbits)) & masque for k in range(n)]


def lire_message(b: bytes, debut: int, longueur: int) -> Champ:
    disc = b[debut + 6]
    if b[debut + 7] != 2:
        raise ErreurGrib("seul GRIB édition 2 est pris en charge")
    s = {}
    for num, sec in _sections(b, debut, debut + longueur):
        s.setdefault(num, sec)
    s1, s3, s4, s5, s7 = s[1], s[3], s[4], s[5], s[7]
    s6 = s.get(6)

    # Section 1 : date de référence
    an, mo, jo, h, mi, se = struct.unpack(">HBBBBB", s1[12:19])
    date_ref = dt.datetime(an, mo, jo, h, mi, se, tzinfo=dt.timezone.utc)

    # Section 3 : grille lat/lon (modèle 3.0)
    if int.from_bytes(s3[12:14], "big") != 0:
        raise ErreurGrib("grille non lat/lon")
    ni = int.from_bytes(s3[30:34], "big")
    nj = int.from_bytes(s3[34:38], "big")
    la1 = _signe(int.from_bytes(s3[46:50], "big"), 32) / 1e6
    lo1 = _signe(int.from_bytes(s3[50:54], "big"), 32) / 1e6
    la2 = _signe(int.from_bytes(s3[55:59], "big"), 32) / 1e6
    lo2 = _signe(int.from_bytes(s3[59:63], "big"), 32) / 1e6
    di = int.from_bytes(s3[63:67], "big") / 1e6
    dj = int.from_bytes(s3[67:71], "big") / 1e6
    scan = s3[71]
    if scan & 0x20:
        raise ErreurGrib("ordre de balayage par colonnes non pris en charge")

    # Section 4 : produit (modèle 4.0 = instant ; 4.8 = moyenne/cumul : échéance = fin de l'intervalle)
    modele = int.from_bytes(s4[7:9], "big")
    if modele not in (0, 8):
        raise ErreurGrib("modèle de produit non pris en charge")
    cat, num = s4[9], s4[10]
    unite = s4[17]
    ech = int.from_bytes(s4[18:22], "big") * _UNITE_TEMPS.get(unite, 3600) / 3600
    debut = ech
    if modele == 8 and len(s4) >= 41:
        fin = dt.datetime(int.from_bytes(s4[34:36], "big"), s4[36], s4[37], s4[38], s4[39], s4[40], tzinfo=dt.timezone.utc)
        ech = (fin - date_ref).total_seconds() / 3600
    niveau = s4[22]

    # Section 5 : simple packing (modèle 5.0)
    npts = int.from_bytes(s5[5:9], "big")
    if int.from_bytes(s5[9:11], "big") != 0:
        raise ErreurGrib("encodage non pris en charge (seul le simple packing l'est)")
    R = struct.unpack(">f", s5[11:15])[0]
    E = _signe(int.from_bytes(s5[15:17], "big"), 16)
    D = _signe(int.from_bytes(s5[17:19], "big"), 16)
    nbits = s5[19]
    brut = _depaqueter(s7[5:], npts, nbits)
    f2, f10 = 2.0 ** E, 10.0 ** D
    vals = [(R + x * f2) / f10 for x in brut]

    # Section 6 : masque terre/mer éventuel
    total = ni * nj
    if s6 is not None and s6[5] == 0:
        bits = _depaqueter(s6[6:], total, 1)
        it = iter(vals)
        vals = [next(it) if bit else None for bit in bits]
    elif len(vals) != total:
        raise ErreurGrib("nombre de points incohérent")

    # Orientation : on range toujours du sud au nord et d'ouest en est
    dlat = dj if scan & 0x40 else -dj
    lignes = [vals[j * ni:(j + 1) * ni] for j in range(nj)]
    if scan & 0x80:
        lignes = [l[::-1] for l in lignes]
        lo1, lo2 = lo2, lo1
    if dlat < 0:
        lignes.reverse()
        la1, dlat = la2, -dlat
    c = Champ()
    code, nom, u = PARAMETRES.get((disc, cat, num), (f"P{disc}.{cat}.{num}", f"Paramètre {disc}.{cat}.{num}", ""))
    c.code, c.nom, c.unite, c.niveau = code, nom, u, niveau
    c.date_ref, c.echeance_h, c.debut_h = date_ref, ech, debut
    c.ni, c.nj, c.lat1, c.lon1, c.dlat, c.dlon = ni, nj, la1, lo1, dlat, di
    c.valeurs = [v for l in lignes for v in l]
    return c


def lire_octets(b: bytes) -> list:
    champs, i = [], 0
    while True:
        j = b.find(b"GRIB", i)
        if j < 0 or j + 16 > len(b):
            break
        lg = int.from_bytes(b[j + 8:j + 16], "big")
        if b[j + lg - 4:j + lg] != b"7777":
            raise ErreurGrib(f"message tronqué à l'octet {j}")
        try:
            champs.append(lire_message(b, j, lg))
        except ErreurGrib:
            pass        # message d'un type inconnu (ajouté plus tard au fichier) : ignoré, le reste est lu
        i = j + lg
    return champs


def lire_fichier(chemin: str) -> list:
    with open(chemin, "rb") as f:
        return lire_octets(f.read())


class Previsions:
    """Regroupe les champs par échéance : prev.pas -> {"UGRD": Champ, ...}."""

    def __init__(self, champs: list):
        self.par_echeance: dict = {}
        for c in champs:
            self.par_echeance.setdefault(c.echeance_h, {})[c.code] = c
        self.echeances = sorted(self.par_echeance)
        c0 = champs[0]
        self.date_ref = c0.date_ref

    def date(self, k: int) -> dt.datetime:
        return self.date_ref + dt.timedelta(hours=self.echeances[k])

    def point(self, k: int, lat: float, lon: float) -> dict:
        """Prévision au point touché (vent en nœuds, direction d'où vient le vent)."""
        ch = self.par_echeance[self.echeances[k]]
        out = {}
        if "UGRD" in ch and "VGRD" in ch:
            u, v = ch["UGRD"].interpoler(lat, lon), ch["VGRD"].interpoler(lat, lon)
            if u is not None and v is not None:
                out["vent_kt"] = math.hypot(u, v) * 1.943844
                out["vent_dir"] = (math.degrees(math.atan2(-u, -v)) + 360) % 360
        for code, cle, fac in (("GUST", "rafales_kt", 1.943844), ("HTSGW", "vagues_m", 1),
                               ("PERPW", "periode_s", 1), ("DIRPW", "vagues_dir", 1)):
            if code in ch:
                x = ch[code].interpoler(lat, lon)
                if x is not None:
                    out[cle] = x * fac
        return out
