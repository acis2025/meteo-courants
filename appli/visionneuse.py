"""
ACIS MétéoNav — visionneuse météo du programme PC (Météo Marine Tunisie).

Affiche le fichier GRIB2 téléchargé, à la marque A.C.I.S et sans rien installer (aucun autre logiciel) :
Python + tkinter seulement (Windows 7 à 11, aucun navigateur nécessaire).
  - couches : vent, rafales, vagues, courant, pluie, nuages (mêmes couleurs que l'appli téléphone) ;
  - barbules de vent et flèches (vagues, courant) avec leur valeur écrite à côté ;
  - MARGE DE SÉCURITÉ : quand le robot « Précision météo » a prouvé (30 jours de relevés réels)
    que les vents forts sont sous-estimés à une échéance, la valeur est suivie de « (+N) » ;
  - curseur de temps (heure de Tunisie), animation, zoom molette, déplacement à la souris,
    fiche complète du point cliqué.
Le fichier GRIB n'est jamais modifié : la carte montre la prévision NOAA telle quelle.

Essai : python visionneuse.py Tunisie_complet_….grb2
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sys
import threading
import time

import grib2

KT = 1.943844
TUNISIE = dt.timedelta(hours=1)                   # UTC+1 toute l'année
BLEU = "#1b265e"
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]

# ------------------------------------------------------------------ couleurs (identiques à l'appli téléphone)
ECH_VENT = [(0, "#5a2a96"), (5, "#3f56d8"), (10, "#2fb35a"), (15, "#c9df1a"), (20, "#f6b400"),
            (25, "#f15a1d"), (30, "#d1132b"), (35, "#a3002e"), (40, "#76002e"), (50, "#420020")]
ECH_VAGUES = [(0, "#5a2a96"), (0.5, "#3f56d8"), (1, "#2fa4c8"), (1.5, "#2fb35a"), (2, "#c9df1a"),
              (2.5, "#f6b400"), (3, "#f15a1d"), (4, "#d1132b"), (5, "#76002e"), (6, "#420020")]
ECH_COURANT = [(0, "#24306e"), (0.25, "#3f56d8"), (0.5, "#2fa4c8"), (0.75, "#2fb35a"), (1, "#c9df1a"),
               (1.5, "#f6b400"), (2, "#f15a1d"), (2.5, "#d1132b"), (3, "#76002e")]
ECH_PLUIE = [(0, "#1a2046"), (0.1, "#9be7ff"), (0.5, "#4fb6ff"), (1, "#2f6bff"), (2, "#2fb35a"), (5, "#f6d200"),
             (10, "#f15a1d"), (20, "#d1132b"), (50, "#8b00a8")]
ECH_NUAGES = [(0, "#1a2046"), (25, "#47506f"), (50, "#7d849c"), (75, "#b6bac8"), (100, "#eceef3")]
ECH_MAREE = [(-1, "#1d2f8f"), (-0.5, "#2f6bd8"), (-0.2, "#8fc2f0"), (0, "#eef1f6"), (0.2, "#f7c49e"),
             (0.5, "#e8643a"), (1, "#a3122b")]

COUCHES = {   # nom : (libellé, échelle, unité, graduations de la légende, échelle « régulière » ?)
    "vent": ("Vent", ECH_VENT, "nds", [0, 5, 10, 15, 20, 25, 30, 35, 40, 50], False),
    "rafales": ("Rafales", ECH_VENT, "nds", [0, 5, 10, 15, 20, 25, 30, 35, 40, 50], False),
    "vagues": ("Vagues", ECH_VAGUES, "m", [0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 6], False),
    "courant": ("Courant", ECH_COURANT, "nds", [0, 0.25, 0.5, 0.75, 1, 1.5, 2, 2.5, 3], False),
    "pluie": ("Pluie", ECH_PLUIE, "mm/h", [0, 0.1, 0.5, 1, 2, 5, 10, 20, 50], True),
    "nuages": ("Nuages", ECH_NUAGES, "%", [0, 25, 50, 75, 100], False),
    "maree": ("Marée", ECH_MAREE, "m", [-1, -0.5, -0.2, 0, 0.2, 0.5, 1], True),
}

VILLES = [("Tunis", 36.806, 10.181), ("Sfax", 34.740, 10.760), ("Sousse", 35.826, 10.636), ("Kélibia", 36.847, 11.093),
          ("Bizerte", 37.274, 9.873), ("Gabès", 33.881, 10.098), ("Houmt Souk", 33.875, 10.857),
          ("Mahdia", 35.505, 11.062), ("Kerkennah", 34.71, 11.25), ("Hammamet", 36.40, 10.61),
          ("Tabarka", 36.955, 8.758), ("Zarzis", 33.504, 11.112), ("Monastir", 35.777, 10.826),
          ("Pantelleria", 36.83, 11.94), ("Lampedusa", 35.50, 12.60), ("Chebba", 35.237, 11.115),
          ("Mahrès", 34.53, 10.50), ("Ben Gardane", 33.14, 11.22), ("Zouara", 32.93, 12.08), ("Tripoli", 32.89, 13.19),
          ("Marsala", 37.80, 12.44), ("Palerme", 38.116, 13.361), ("La Valette", 35.899, 14.514),
          ("Cagliari", 39.217, 9.113), ("Annaba", 36.900, 7.766), ("Trapani", 38.017, 12.514)]
ZONES = [("Toute la Tunisie", 35.6, 10.8, 1.0), ("Nord · Tabarka – Bizerte", 37.15, 9.3, 2.2),
         ("Golfe de Tunis · Cap Bon", 36.95, 10.6, 2.4), ("Golfe de Hammamet", 36.15, 10.85, 2.2),
         ("Sahel · Sousse – Mahdia", 35.6, 11.0, 2.2), ("Sfax · Kerkennah", 34.75, 10.95, 2.2),
         ("Golfe de Gabès · Djerba", 33.75, 10.75, 2.0), ("Large · Pantelleria – Lampedusa", 36.0, 12.1, 1.6),
         ("Toute la zone", None, None, 0.0)]

URL_PRECISION = "https://github.com/acis2025/meteo-courants/releases/download/verif/precision.json"
MARGE_RELEVES_MIN = 40            # vents forts observés nécessaires avant d'afficher une marge
MARGE_SEUIL_KT = 12               # la marge s'affiche à partir de ce vent prévu


# ------------------------------------------------------------------ affichage net (zoom Windows 125 %, 150 %…)
ECH = 1.0          # facteur du zoom Windows (1.5 à 150 %) : toutes les tailles en pixels sont multipliées par lui


def activer_dpi() -> None:
    """À appeler AVANT de créer la 1re fenêtre Tk : le programme gère lui-même le zoom de Windows,
    le texte est net (sinon Windows agrandit l'image de la fenêtre, qui devient floue)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)          # Windows 8.1 et plus
        except Exception:                                           # noqa: BLE001
            ctypes.windll.user32.SetProcessDPIAware()               # Windows 7
    except Exception:                                               # noqa: BLE001
        pass


def regler_echelle(fenetre) -> float:
    """Mesure le zoom de Windows sur une fenêtre Tk (96 points par pouce = 100 %)."""
    global ECH
    try:
        ECH = max(1.0, min(3.0, float(fenetre.winfo_fpixels("1i")) / 96.0))
    except Exception:                                               # noqa: BLE001
        ECH = 1.0
    return ECH


def S(n: float) -> int:
    """Taille en pixels adaptée au zoom de Windows."""
    return int(round(n * ECH))


# ------------------------------------------------------------------ outils
def _hex(c: str) -> tuple:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def palette(ech: list, reguliere: bool = False, n: int = 256) -> tuple:
    """(table de n couleurs « #rrggbb », fonction valeur -> indice)."""
    pts = [(i / (len(ech) - 1) if reguliere else v, _hex(c)) for i, (v, c) in enumerate(ech)]
    vmax = pts[-1][0]
    table = []
    for q in range(n):
        v = q / (n - 1) * vmax
        a, b = pts[0], pts[-1]
        for z in range(len(pts) - 1):
            if pts[z][0] <= v <= pts[z + 1][0]:
                a, b = pts[z], pts[z + 1]
                break
        f = 0 if b[0] == a[0] else (v - a[0]) / (b[0] - a[0])
        table.append("#%02x%02x%02x" % tuple(int(a[1][r] + (b[1][r] - a[1][r]) * f) for r in range(3)))
    if reguliere:
        vals = [v for v, _ in ech]

        def indice(v):
            if v <= vals[0]:
                return 0
            for i in range(len(vals) - 1):
                if v <= vals[i + 1]:
                    return int((i + (v - vals[i]) / (vals[i + 1] - vals[i])) / (len(vals) - 1) * (n - 1))
            return n - 1
    else:
        def indice(v):
            return max(0, min(n - 1, int(v / vmax * (n - 1))))
    return table, indice


def dm(v: float, pos: str, neg: str) -> str:
    """Degrés + minutes à 3 décimales, lettre devant (format A.C.I.S) : « N34°38.808 »."""
    a = round(abs(v) * 60000) / 60000                  # arrondi au millième de minute
    d = int(a)
    m = (a - d) * 60
    if m >= 59.9995:                                   # 59.9996' -> degré suivant
        d, m = d + 1, 0.0
    return f"{pos if v >= 0 else neg}{d}°{m:06.3f}"


def position(la: float, lo: float) -> str:
    """Position au format A.C.I.S : « N34°38.808-E10°45.341 »."""
    return f"{dm(la, 'N', 'S')}-{dm(lo, 'E', 'W')}"


def cardinal(deg: float) -> str:
    noms = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]
    return noms[int((deg % 360) / 22.5 + 0.5) % 16]


# ------------------------------------------------------------------ soleil et lune (mêmes formules que la carte téléphone)
RAD = math.pi / 180
J2000 = 2451545
OBL = 23.4397 * RAD


def jours2000(t: float) -> float:
    """t : secondes UTC depuis 1970 -> jours depuis J2000."""
    return t / 86400 - 0.5 + 2440588 - J2000


def _ecl_eq(l, b):
    return {"ra": math.atan2(math.sin(l) * math.cos(OBL) - math.tan(b) * math.sin(OBL), math.cos(l)),
            "dec": math.asin(math.sin(b) * math.cos(OBL) + math.cos(b) * math.sin(OBL) * math.sin(l))}


def soleil_pos(d: float) -> dict:
    M = RAD * (357.5291 + 0.98560028 * d)
    C = RAD * (1.9148 * math.sin(M) + 0.02 * math.sin(2 * M) + 0.0003 * math.sin(3 * M))
    return _ecl_eq(M + C + RAD * 102.9372 + math.pi, 0)


def lune_pos(d: float) -> dict:
    """Position de la Lune (éléments + 12 termes de perturbation, ~2')."""
    n = d + 1.5

    def deg(x):
        return (x % 360) * RAD
    N, i, w = deg(125.1228 - 0.0529538083 * n), 5.1454 * RAD, deg(318.0634 + 0.1643573223 * n)
    M, e, a = deg(115.3654 + 13.0649929509 * n), 0.0549, 60.2666
    Ms, ws = deg(356.047 + 0.9856002585 * n), deg(282.9404 + 4.70935e-5 * n)
    E = M + e * math.sin(M) * (1 + e * math.cos(M))
    for _ in range(5):
        E = E - (E - e * math.sin(E) - M) / (1 - e * math.cos(E))
    x, y = a * (math.cos(E) - e), a * math.sqrt(1 - e * e) * math.sin(E)
    r, v = math.hypot(x, y), math.atan2(y, x)
    xe = r * (math.cos(N) * math.cos(v + w) - math.sin(N) * math.sin(v + w) * math.cos(i))
    ye = r * (math.sin(N) * math.cos(v + w) + math.cos(N) * math.sin(v + w) * math.cos(i))
    ze = r * math.sin(v + w) * math.sin(i)
    lon, lat = math.atan2(ye, xe), math.atan2(ze, math.hypot(xe, ye))
    Ls, Lm = Ms + ws, M + w + N
    D, F, S = Lm - Ls, Lm - N, math.sin
    lon += RAD * (-1.274 * S(M - 2 * D) + 0.658 * S(2 * D) - 0.186 * S(Ms) - 0.059 * S(2 * M - 2 * D)
                  - 0.057 * S(M - 2 * D + Ms) + 0.053 * S(M + 2 * D) + 0.046 * S(2 * D - Ms) + 0.041 * S(M - Ms)
                  - 0.035 * S(D) - 0.031 * S(M + Ms) - 0.015 * S(2 * F - 2 * D) + 0.011 * S(M - 4 * D))
    lat += RAD * (-0.173 * S(F - 2 * D) - 0.055 * S(M - F - 2 * D) - 0.046 * S(M + F - 2 * D)
                  + 0.033 * S(F + 2 * D) + 0.017 * S(2 * M + F))
    p = _ecl_eq(lon, lat)
    p["dist"] = (r - 0.58 * math.cos(M - 2 * D) - 0.46 * math.cos(2 * D)) * 6378.14
    return p


def _hauteur_astre(p, d, la, lo):
    H = RAD * (280.16 + 360.9856235 * d) + RAD * lo - p["ra"]
    f = RAD * la
    return math.asin(math.sin(f) * math.sin(p["dec"]) + math.cos(f) * math.cos(p["dec"]) * math.cos(H))


def lever_coucher(pos, seuil_deg, t0: float, la: float, lo: float) -> tuple:
    """Lever / coucher pendant [t0, t0 + 24 h] (secondes UTC) : pas de 10 min puis interpolation."""
    lev = cou = prec = None
    for m in range(0, 1441, 10):
        t = t0 + m * 60
        d = jours2000(t)
        q = pos(d)
        seuil = (0.7275 * math.asin(6378.14 / q["dist"]) - 0.5667 * RAD) if seuil_deg is None else seuil_deg * RAD
        h = _hauteur_astre(q, d, la, lo) - seuil
        if prec is not None and (prec[1] < 0) != (h < 0):
            tc = prec[0] + (t - prec[0]) * prec[1] / (prec[1] - h)
            if h > 0 and lev is None:
                lev = tc
            elif h < 0 and cou is None:
                cou = tc
        prec = (t, h)
    return lev, cou


def phase_lune(t: float) -> tuple:
    """(fraction éclairée, phase 0..1 : 0 = nouvelle, 0,5 = pleine)."""
    d = jours2000(t)
    s, m, sd = soleil_pos(d), lune_pos(d), 149598000
    phi = math.acos(max(-1.0, min(1.0, math.sin(s["dec"]) * math.sin(m["dec"])
                                  + math.cos(s["dec"]) * math.cos(m["dec"]) * math.cos(s["ra"] - m["ra"]))))
    inc = math.atan2(sd * math.sin(phi), m["dist"] - sd * math.cos(phi))
    ang = math.atan2(math.cos(s["dec"]) * math.sin(s["ra"] - m["ra"]),
                     math.sin(s["dec"]) * math.cos(m["dec"]) - math.cos(s["dec"]) * math.sin(m["dec"]) * math.cos(s["ra"] - m["ra"]))
    return (1 + math.cos(inc)) / 2, 0.5 + 0.5 * inc * (-1 if ang < 0 else 1) / math.pi


def nom_lune(frac: float, phase: float) -> str:
    monte = phase < 0.5
    if frac < 0.03:
        return "nouvelle lune"
    if frac > 0.97:
        return "pleine lune"
    if abs(frac - 0.5) < 0.06:
        return "premier quartier" if monte else "dernier quartier"
    if frac < 0.5:
        return "lune croissante" if monte else "dernier croissant"
    return "gibbeuse croissante" if monte else "gibbeuse décroissante"


def type_maree(phase: float) -> tuple:
    """Vive-eau ~1,5 jour après la nouvelle et la pleine lune ; morte-eau après les quartiers."""
    x = ((phase * 29.53 - 1.5) % 14.765) / 14.765
    if x < 0.135 or x > 0.865:
        return "vive-eau", "grande marée"
    if abs(x - 0.5) < 0.135:
        return "morte-eau", "petite marée"
    return ("marée moyenne", "vers la morte-eau") if x < 0.5 else ("marée moyenne", "vers la vive-eau")


def extremes(serie: list, a: float, b: float) -> list:
    """Pleines (haut=True) et basses mers d'une série horaire, entre les heures a et b : [(t, v, haut)]."""
    n = len(serie)

    def lisse(i):
        return (serie[max(0, i - 1)] + serie[i] + serie[min(n - 1, i + 1)]) / 3
    out = []
    for i in range(max(1, math.floor(a) - 1), min(n - 2, math.ceil(b) + 1) + 1):
        p, c, s = lisse(i - 1), lisse(i), lisse(i + 1)
        if math.isnan(p + c + s):
            continue
        haut, bas = c >= p and c > s, c <= p and c < s
        if not haut and not bas:
            continue
        y0, y1, y2 = serie[i - 1], serie[i], serie[i + 1]
        den = y0 - 2 * y1 + y2
        dx = max(-0.5, min(0.5, 0.5 * (y0 - y2) / den)) if den else 0
        v, t = y1 - 0.25 * (y0 - y2) * dx, i + dx
        if out:
            dt_, dv_, dh_ = out[-1]
            if dh_ == haut:
                if (v > dv_) if haut else (v < dv_):
                    out[-1] = (t, v, haut)
                continue
            if abs(v - dv_) < 0.03 or t - dt_ < 3:          # petite oscillation : ignorée
                continue
        out.append((t, v, haut))
    return [e for e in out if a <= e[0] < b]


def hm(t) -> str:
    """Secondes UTC -> « HH:MM » heure de Tunisie (— si inconnu)."""
    if t is None:
        return "—"
    return (dt.datetime.fromtimestamp(t, dt.timezone.utc) + TUNISIE).strftime("%H:%M")


def signe(v: float) -> str:
    return ("+" if v >= 0 else "−") + f"{abs(v):.2f}"


def lire_cotes() -> tuple:
    """Côtes et frontières de la carte du téléphone (Natural Earth 10 m) : (terres, frontières) en [[lat, lon], …]."""
    texte = None
    try:                                           # carte.html de la mise à jour en ligne, s'il y en a une
        import maj_en_ligne
        b = maj_en_ligne.ressource("carte.html")
        texte = b.decode("utf-8") if b is not None else None
    except Exception:                              # noqa: BLE001
        pass
    if texte is None:
        ici = os.path.dirname(os.path.abspath(__file__))
        base = getattr(sys, "_MEIPASS", ici)
        for p in (os.path.join(ici, "carte.html"), os.path.join(ici, "carte", "carte.html"),
                  os.path.join(base, "carte", "carte.html"), os.path.join(base, "ressources", "carte.html")):
            if os.path.exists(p):
                texte = open(p, encoding="utf-8").read()
                break
    if texte is None:
        return [], []
    out = []
    for nom in ("TERRES", "FRONTIERES"):
        m = re.search(r"const " + nom + r" = (\[.*?\]);\n", texte, re.S)
        try:
            out.append(json.loads(m.group(1)) if m else [])
        except ValueError:
            out.append([])
    return out[0], out[1]


def dessiner_drapeau_tunisie(cv, x0: float, y0: float, larg: float, haut: float) -> None:
    """Drapeau de la Tunisie (rouge, disque blanc, croissant et étoile rouges), proportions 3:2."""
    rouge = "#e70013"
    cv.create_rectangle(x0, y0, x0 + larg, y0 + haut, fill=rouge, outline="#7a0010")
    cx, cy = x0 + larg / 2, y0 + haut / 2
    R = haut / 4                                         # disque blanc : diamètre = moitié de la hauteur
    cv.create_oval(cx - R, cy - R, cx + R, cy + R, fill="white", outline="white")
    r1 = R * 0.75                                        # croissant : disque rouge moins un disque blanc décalé
    cv.create_oval(cx - r1, cy - r1, cx + r1, cy + r1, fill=rouge, outline=rouge)
    r2, dx = R * 0.6, R * 0.25
    cv.create_oval(cx + dx - r2, cy - r2, cx + dx + r2, cy + r2, fill="white", outline="white")
    sx, re, ri = cx + R * 0.2, R * 0.45, R * 0.18        # étoile à cinq branches, une pointe vers le croissant
    pts = []
    for i in range(10):
        a = math.radians(180 + i * 36)
        r = re if i % 2 == 0 else ri
        pts += (sx + r * math.cos(a), cy + r * math.sin(a))
    cv.create_polygon(pts, fill=rouge, outline="")


# ------------------------------------------------------------------ données
class Grille:
    """Valeurs d'un champ aux nœuds (liste, None = absent) + interpolation bilinéaire rapide."""
    __slots__ = ("lat1", "lon1", "dlat", "dlon", "ni", "nj", "v")

    def __init__(self, modele, valeurs):
        self.lat1, self.lon1, self.dlat, self.dlon = modele.lat1, modele.lon1, modele.dlat, modele.dlon
        self.ni, self.nj, self.v = modele.ni, modele.nj, valeurs

    def en(self, lat: float, lon: float):
        x = (lon - self.lon1) / self.dlon
        y = (lat - self.lat1) / self.dlat
        i0, j0 = int(math.floor(x)), int(math.floor(y))
        if i0 < 0 or j0 < 0 or i0 >= self.ni or j0 >= self.nj:
            return None
        i1, j1 = min(i0 + 1, self.ni - 1), min(j0 + 1, self.nj - 1)
        fx, fy = x - i0, y - j0
        v, ni = self.v, self.ni
        s = p = 0.0
        for val, w in ((v[j0 * ni + i0], (1 - fx) * (1 - fy)), (v[j0 * ni + i1], fx * (1 - fy)),
                       (v[j1 * ni + i0], (1 - fx) * fy), (v[j1 * ni + i1], fx * fy)):
            if val is not None:
                s += val * w
                p += w
        return s / p if p >= 0.25 else None

    def proche(self, lat: float, lon: float):
        i = int(round((lon - self.lon1) / self.dlon))
        j = int(round((lat - self.lat1) / self.dlat))
        if 0 <= i < self.ni and 0 <= j < self.nj:
            return self.v[j * self.ni + i]
        return None


class DonneesVides:
    """Pas encore de fichier météo (1er lancement, fichier abîmé) : la carte s'ouvre quand même (côtes, lune,
    soleil, bouton Télécharger) et se remplit dès qu'un fichier arrive (Visionneuse.charger)."""
    vide = True

    def __init__(self):
        self.chemin = ""
        self.run = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
        self.echeances = [0]
        self.par = {0: {}}
        self.courants = {}
        self.zone = (30.0, 39.0, 7.0, 14.0)
        self.mer = None
        self.maree, self.maree_t0, self.maree_fichier = [], None, None

    def date(self, k: int) -> dt.datetime:
        return self.run

    def est_mer(self, lat: float, lon: float) -> bool:
        return True

    def grille(self, couche: str, k: int):
        return None

    def vent(self, k, lat, lon):
        return None

    def courant(self, k, lat, lon):
        return None

    def direction_vagues(self, k, lat, lon):
        return None

    def fiche(self, k, lat, lon) -> dict:
        return {}

    def serie_maree(self, lat, lon):
        return None

    def heure_maree(self, t) -> float:
        return -1


class Donnees:
    vide = False

    """Le fichier GRIB2 téléchargé, rangé par heure de prévision."""

    def __init__(self, chemin: str):
        champs = grib2.lire_fichier(chemin)
        if not champs:
            raise ValueError("fichier GRIB vide ou illisible")
        self.chemin = chemin
        gfs = [c for c in champs if c.code not in ("UOGRD", "VOGRD", "DSLM")]
        cour = [c for c in champs if c.code in ("UOGRD", "VOGRD")]
        prev = grib2.Previsions(gfs or champs)
        self.run = prev.date_ref.replace(tzinfo=dt.timezone.utc) if prev.date_ref.tzinfo is None else prev.date_ref
        self.echeances = [e for e in prev.echeances if "UGRD" in prev.par_echeance[e] or "HTSGW" in prev.par_echeance[e]]
        self.par = prev.par_echeance
        self.courants = {}                         # date UTC -> {"UOGRD": Champ, "VOGRD": Champ}
        for c in cour:
            t = c.date_prevue.replace(tzinfo=dt.timezone.utc) if c.date_prevue.tzinfo is None else c.date_prevue
            self.courants.setdefault(t, {})[c.code] = c
        self._cache = {}
        g = [c for c in gfs if c.code in ("UGRD", "HTSGW")] or champs
        lats = [c.lat1 for c in g] + [c.lat1 + (c.nj - 1) * c.dlat for c in g]
        lons = [c.lon1 for c in g] + [c.lon1 + (c.ni - 1) * c.dlon for c in g]
        self.zone = (min(lats), max(lats), min(lons), max(lons))
        self.mer = None                            # masque mer = nœuds où GFS-Wave a une valeur
        for e in self.echeances:
            h = self.par[e].get("HTSGW")
            if h is not None:
                self.mer = Grille(h, [None if x is None else 1 for x in h.valeurs])
                break
        self._charger_maree([c for c in champs if c.code == "DSLM"])

    # ---------------- marée (hauteur d'eau Copernicus, une grille par heure)
    def _charger_maree(self, champs_maree: list) -> None:
        """Marée du fichier, sinon du fichier marée rangé à côté (Maree_….grb2 ou marees_tunisie.grb2)."""
        self.maree, self.maree_t0, self.maree_fichier = [], None, None
        if not champs_maree:
            ici = os.path.dirname(os.path.abspath(self.chemin))
            try:
                noms = os.listdir(ici)
            except OSError:
                noms = []
            cands = sorted((n for n in noms if n.lower().startswith("maree_") and n.lower().endswith(".grb2")),
                           key=lambda n: os.path.getmtime(os.path.join(ici, n)), reverse=True)
            cands += [n for n in noms if n.lower() == "marees_tunisie.grb2"]
            for n in cands:
                try:
                    champs_maree = [c for c in grib2.lire_fichier(os.path.join(ici, n)) if c.code == "DSLM"]
                except Exception:                  # noqa: BLE001
                    champs_maree = []
                if champs_maree:
                    self.maree_fichier = n
                    break
        if not champs_maree:
            return

        def utc(c):
            return c.date_prevue.replace(tzinfo=dt.timezone.utc) if c.date_prevue.tzinfo is None else c.date_prevue
        par_date = {utc(c): c for c in champs_maree}
        self.maree_t0 = min(par_date)
        n = int((max(par_date) - self.maree_t0).total_seconds() // 3600) + 1
        self.maree = [par_date.get(self.maree_t0 + dt.timedelta(hours=h)) for h in range(n)]

    def heure_maree(self, t: dt.datetime) -> float:
        return (t - self.maree_t0).total_seconds() / 3600 if self.maree else -1

    def _maree_brut(self, i: int, j: int, h: int):
        c = self.maree[h]
        return None if c is None else c.valeurs[j * c.ni + i]

    def maree_a(self, la: float, lo: float, h: float):
        """Hauteur d'eau (m) au point à l'heure fractionnaire h ; None sur terre / hors période."""
        if not self.maree or h < 0 or h > len(self.maree) - 1:
            return None
        g = next((c for c in self.maree if c is not None), None)
        x, y = (lo - g.lon1) / g.dlon, (la - g.lat1) / g.dlat
        i0, j0 = math.floor(x), math.floor(y)
        if i0 < 0 or j0 < 0 or i0 >= g.ni or j0 >= g.nj:
            return None
        i1, j1, fx, fy = min(i0 + 1, g.ni - 1), min(j0 + 1, g.nj - 1), x - i0, y - j0
        h0 = math.floor(h)
        h1, ft = min(h0 + 1, len(self.maree) - 1), h - h0
        s = p = 0.0
        for i, j, w in ((i0, j0, (1 - fx) * (1 - fy)), (i1, j0, fx * (1 - fy)), (i0, j1, (1 - fx) * fy), (i1, j1, fx * fy)):
            a, b = self._maree_brut(i, j, h0), self._maree_brut(i, j, h1)
            if a is not None and b is not None:
                s += (a + (b - a) * ft) * w
                p += w
        return None if p < 0.5 else s / p

    def serie_maree(self, la: float, lo: float):
        """Série horaire au point (au bord de la côte : point de mer le plus proche, jusqu'à ~3 mailles)."""
        if not self.maree:
            return None
        n = len(self.maree)
        if self.maree_a(la, lo, 0) is not None:
            return [self.maree_a(la, lo, h) for h in range(n)]
        g = next(c for c in self.maree if c is not None)
        x, y = (lo - g.lon1) / g.dlon, (la - g.lat1) / g.dlat
        best, db = None, 1e9
        for j in range(round(y) - 3, round(y) + 4):
            for i in range(round(x) - 3, round(x) + 4):
                if i < 0 or j < 0 or i >= g.ni or j >= g.nj or self._maree_brut(i, j, 0) is None:
                    continue
                d = (i - x) ** 2 + (j - y) ** 2
                if d < db:
                    db, best = d, (i, j)
        if not best:
            return None
        out = []
        for h in range(n):
            v = self._maree_brut(best[0], best[1], h)
            out.append(float("nan") if v is None else v)
        return out

    def date(self, k: int) -> dt.datetime:
        return self.run + dt.timedelta(hours=self.echeances[k])

    def est_mer(self, lat: float, lon: float) -> bool:
        return self.mer is None or self.mer.proche(lat, lon) is not None

    def _courant(self, k: int):
        if not self.courants:
            return None
        t = self.date(k)
        meilleur = min(self.courants, key=lambda d: abs((d - t).total_seconds()))
        if abs((meilleur - t).total_seconds()) > 3 * 3600:
            return None
        c = self.courants[meilleur]
        return (c["UOGRD"], c["VOGRD"]) if "UOGRD" in c and "VOGRD" in c else None

    def grille(self, couche: str, k: int):
        """Grille scalaire de la couche à l'échéance k (vent en nœuds, pluie en mm/h…), ou None."""
        cle = (couche, k)
        if cle in self._cache:
            return self._cache[cle]
        ch = self.par[self.echeances[k]]
        g = None
        if couche == "vent" and "UGRD" in ch and "VGRD" in ch:
            u, v = ch["UGRD"], ch["VGRD"]
            g = Grille(u, [None if a is None or b is None else math.hypot(a, b) * KT for a, b in zip(u.valeurs, v.valeurs)])
        elif couche == "rafales" and "GUST" in ch:
            g = Grille(ch["GUST"], [None if a is None else a * KT for a in ch["GUST"].valeurs])
        elif couche == "vagues" and "HTSGW" in ch:
            g = Grille(ch["HTSGW"], ch["HTSGW"].valeurs)
        elif couche == "pluie" and "PRATE" in ch:
            g = Grille(ch["PRATE"], [None if a is None else a * 3600 for a in ch["PRATE"].valeurs])
        elif couche == "nuages" and "TCDC" in ch:
            g = Grille(ch["TCDC"], ch["TCDC"].valeurs)
        elif couche == "maree" and self.maree:
            h = self.heure_maree(self.date(k))
            if 0 <= h <= len(self.maree) - 1:
                h0 = math.floor(h)
                h1, ft = min(h0 + 1, len(self.maree) - 1), h - h0
                a, b = self.maree[h0], self.maree[h1]
                if a is not None and b is not None:
                    g = Grille(a, [None if x is None or y is None else x + (y - x) * ft
                                   for x, y in zip(a.valeurs, b.valeurs)])
        elif couche == "courant":
            c = self._courant(k)
            if c:
                u, v = c
                g = Grille(u, [None if a is None or b is None else math.hypot(a, b) * KT for a, b in zip(u.valeurs, v.valeurs)])
        elif couche in ("UGRD", "VGRD", "DIRPW", "PERPW") and couche in ch:
            g = Grille(ch[couche], ch[couche].valeurs)
        elif couche in ("UOGRD", "VOGRD"):
            c = self._courant(k)
            if c:
                m = c[0] if couche == "UOGRD" else c[1]
                g = Grille(m, m.valeurs)
        if len(self._cache) > 400:
            self._cache.clear()
        self._cache[cle] = g
        return g

    def vent(self, k: int, lat: float, lon: float):
        u, v = self.grille("UGRD", k), self.grille("VGRD", k)
        if u is None or v is None:
            return None
        a, b = u.en(lat, lon), v.en(lat, lon)
        if a is None or b is None:
            return None
        return math.hypot(a, b) * KT, (math.degrees(math.atan2(-a, -b)) + 360) % 360

    def courant(self, k: int, lat: float, lon: float):
        u, v = self.grille("UOGRD", k), self.grille("VOGRD", k)
        if u is None or v is None:
            return None
        a, b = u.en(lat, lon), v.en(lat, lon)
        if a is None or b is None:
            return None
        return math.hypot(a, b) * KT, (math.degrees(math.atan2(a, b)) + 360) % 360      # vitesse, VERS où il porte

    def direction_vagues(self, k: int, lat: float, lon: float):
        g = self.grille("DIRPW", k)
        return None if g is None else g.proche(lat, lon)

    def fiche(self, k: int, lat: float, lon: float) -> dict:
        out = {}
        w = self.vent(k, lat, lon)
        if w:
            out["vent"] = w
        for nom in ("rafales", "vagues", "pluie", "nuages"):
            g = self.grille(nom, k)
            x = g.en(lat, lon) if g else None
            if x is not None:
                out[nom] = x
        p = self.grille("PERPW", k)
        if p and p.en(lat, lon) is not None:
            out["periode"] = p.en(lat, lon)
        d = self.direction_vagues(k, lat, lon)
        if d is not None:
            out["dir_vagues"] = d
        c = self.courant(k, lat, lon)
        if c:
            out["courant"] = c
        return out


# ------------------------------------------------------------------ marge de sécurité (robot « Précision météo »)
def dossier_app() -> str:
    try:
        import licence
        return licence.dossier_app()
    except Exception:                               # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".meteomarinetn")


def lire_reglages() -> dict:
    """Réglages de la carte (fenêtre Paramètres), gardés d'une ouverture à l'autre."""
    try:
        d = json.load(open(os.path.join(dossier_app(), "carte_reglages.json"), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:                               # noqa: BLE001
        return {}


def ecrire_reglages(d: dict) -> None:
    try:
        os.makedirs(dossier_app(), exist_ok=True)
        with open(os.path.join(dossier_app(), "carte_reglages.json"), "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


def charger_precision(log=None) -> dict:
    """precision.json du robot (copie gardée sur le PC si pas d'Internet). {} si rien."""
    copie = os.path.join(dossier_app(), "precision.json")
    try:
        import grib_download as gd
        r = gd.SessionHttp().get(URL_PRECISION + "?t=" + str(int(time.time() // 3600)), 20)
        if r.status_code == 200:
            d = json.loads(r.content.decode("utf-8"))
            try:
                os.makedirs(os.path.dirname(copie), exist_ok=True)
                with open(copie, "w", encoding="utf-8") as f:
                    json.dump(d, f)
            except OSError:
                pass
            return d
    except Exception as e:                          # noqa: BLE001
        if log:
            log(f"Précision : pas de mise à jour ({e})")
    try:
        return json.load(open(copie, encoding="utf-8"))
    except Exception:                               # noqa: BLE001
        return {}


def marges(precision: dict) -> dict:
    """{jour d'échéance: nœuds à AJOUTER aux vents forts} — seulement si prouvé par assez de relevés.
    On n'enlève jamais rien : la marge ne sert qu'à ne pas sous-estimer un coup de vent."""
    out = {}
    for cle, s in (precision.get("par_echeance") or {}).items():
        try:
            j = int(cle.replace("J+", ""))
        except ValueError:
            continue
        b = s.get("biais_vent_fort_kt")
        if b is not None and s.get("n_vent_fort", 0) >= MARGE_RELEVES_MIN and b <= -1:
            out[j] = int(round(-b))
    return out


# ------------------------------------------------------------------ bouton « Télécharger » (ancre + mot centré)
class _Frise:
    """Frise du temps (comme XyGrib) : les jours en clair (« ven. 27 »), les heures dessous, un trait par prévision,
    le trait rouge = maintenant, le curseur jaune = l'heure affichée. Clic ou glisser pour changer d'heure."""

    def __init__(self, parent, vis):
        import tkinter as tk
        self.vis = vis
        self.k = 0
        self.cv = tk.Canvas(parent, height=S(48), bg="#eef0f6", highlightthickness=0, cursor="hand2")
        self.cv.bind("<Configure>", lambda e: self.dessiner())
        self.cv.bind("<Button-1>", self._clic)
        self.cv.bind("<B1-Motion>", self._clic)

    def pack(self, **kw):
        self.cv.pack(**kw)

    def config(self, **_):                       # (compatibilité avec l'ancien curseur)
        self.dessiner()

    def set(self, k: int):
        self.k = k
        self.dessiner()

    def _bornes(self):
        d = self.vis.d
        heures = [d.date(i) + TUNISIE for i in range(len(d.echeances))]
        return heures, heures[0], heures[-1]

    def _x(self, t, t0, t1):
        g, dr = S(12), max(S(13), self.cv.winfo_width() - S(12))
        tot = (t1 - t0).total_seconds() or 1.0
        return g + (dr - g) * (t - t0).total_seconds() / tot

    def _clic(self, e):
        if self.vis.d.vide:
            return
        heures, t0, t1 = self._bornes()
        k = min(range(len(heures)), key=lambda i: abs(self._x(heures[i], t0, t1) - e.x))
        if k != self.k:
            self.vis.aller(k)

    def dessiner(self):
        cv = self.cv
        cv.delete("all")
        L = cv.winfo_width()
        if L < S(60):
            return
        haut, bas = S(19), S(31)                     # la bande des jours
        if self.vis.d.vide or len(self.vis.d.echeances) < 2:
            cv.create_rectangle(S(12), haut, L - S(12), bas, fill="#dfe3f2", outline="")
            cv.create_text(L / 2, (haut + bas) / 2, text="Pas encore de météo", fill="#6b7290",
                           font=("Segoe UI", 9, "bold"))
            return
        heures, t0, t1 = self._bornes()
        # jours : bandes alternées, nom du jour au-dessus, heures dessous
        jour = t0.replace(hour=0, minute=0, second=0, microsecond=0)
        n = 0
        while jour <= t1:
            fin = jour + dt.timedelta(days=1)
            xa, xb = self._x(max(jour, t0), t0, t1), self._x(min(fin, t1), t0, t1)
            cv.create_rectangle(xa, haut, xb, bas, fill="#d3d9ee" if n % 2 else "#e6e9f5", outline="")
            if jour > t0:
                cv.create_line(xa, S(2), xa, bas + S(4), fill="#8a90a8")
            largeur = xb - xa
            if largeur >= S(46):
                txt = f"{JOURS[jour.weekday()]} {jour.day}"
            elif largeur >= S(20):
                txt = f"{jour.day}"
            else:
                txt = ""
            if txt:
                cv.create_text((xa + xb) / 2, S(10), text=txt, fill=BLEU, font=("Segoe UI", 9, "bold"))
            pas = 6 if largeur >= S(110) else (12 if largeur >= S(60) else 0)
            if pas:
                for h in range(pas, 24, pas):
                    t = jour + dt.timedelta(hours=h)
                    if t0 <= t <= t1:
                        x = self._x(t, t0, t1)
                        cv.create_text(x, S(41), text=f"{h:02d}h", fill="#6b7290", font=("Segoe UI", 7))
            jour, n = fin, n + 1
        # un petit trait par prévision disponible
        for t in heures:
            x = self._x(t, t0, t1)
            cv.create_line(x, bas - S(4), x, bas, fill="#8a90a8")
        # maintenant (heure Internet) : trait rouge
        h = self.vis.maintenant()
        if h is not None:
            hn = h + TUNISIE
            if t0 <= hn <= t1:
                x = self._x(hn, t0, t1)
                cv.create_line(x, haut - S(3), x, bas + S(3), fill="#e70013", width=max(2, S(2)))
        # heure affichée : curseur jaune
        x = self._x(heures[min(self.k, len(heures) - 1)], t0, t1)
        cv.create_rectangle(x - S(4), haut - S(5), x + S(4), bas + S(5), fill="#f6b400", outline=BLEU,
                            width=max(1, S(1)))


ROUGE_BOUTON = "#e70013"                                  # rouge du drapeau tunisien


class _BoutonAncre:
    """Bouton blanc : grande ancre A.C.I.S collée au mot, l'ensemble centré dans le bouton, mot en très gras."""
    L, H = 270, 48                                        # (multipliés par le zoom de Windows à l'ouverture)
    ECART = 6                                             # espace entre l'ancre et le mot

    def __init__(self, parent, image, texte: str, commande):
        import tkinter as tk
        self.commande = commande
        # cadre de taille fixe ; dedans, ancre + mot côte à côte, l'ensemble centré (Tk mesure lui-même)
        self.cadre = tk.Frame(parent, width=S(self.L), height=S(self.H), bg="#ffffff", cursor="hand2")
        self.cadre.pack_propagate(False)
        self.groupe = tk.Frame(self.cadre, bg="#ffffff")
        self.groupe.place(relx=0.5, rely=0.5, anchor="center")
        if image is not None:
            self.lbl_img = tk.Label(self.groupe, image=image, bg="#ffffff", bd=0)
        else:
            self.lbl_img = tk.Label(self.groupe, text="⚓", fg=BLEU, bg="#ffffff", bd=0,
                                    font=("Segoe UI Symbol", 22, "bold"))
        self.lbl_img.pack(side="left", padx=(0, S(self.ECART)))
        self.lbl_txt = tk.Label(self.groupe, text=texte, fg=ROUGE_BOUTON, bg="#ffffff", bd=0,
                                font=("Segoe UI Black", 17))      # plus gras que « bold »
        self.lbl_txt.pack(side="left")
        self.morceaux = (self.cadre, self.groupe, self.lbl_img, self.lbl_txt)
        for m in self.morceaux:
            m.bind("<Button-1>", lambda e: self.commande())
            m.bind("<Enter>", lambda e: self._fond("#e3e7f5"))
            m.bind("<Leave>", lambda e: self._fond("#ffffff"))

    def _fond(self, c):
        for m in self.morceaux:
            m.config(bg=c)

    def pack(self, **kw):
        self.cadre.pack(**kw)

    def config(self, text=None, fg=None, cursor=None, **_):
        if text is not None:
            self.lbl_txt.config(text=text)
        if fg is not None:
            self.lbl_txt.config(fg=fg)
        if cursor is not None:
            for m in self.morceaux:
                m.config(cursor=cursor)


# ------------------------------------------------------------------ fenêtre
class Visionneuse:
    def __init__(self, chemin: str, parent=None, titre_extra: str = "", version: str = "", maintenant=None,
                 hote: dict = None):
        """hote (programme principal) : {"telecharger": f(), "abonnement": f() -> (nom, détail, couleur),
        "activer": f(), "parametres": f(cadre, fenêtre), "quitter": f()} — la carte devient la fenêtre principale."""
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.ttk = ttk
        self.hote = hote or {}
        self.d = Donnees(chemin) if chemin else DonneesVides()     # sans fichier : carte vide, prête à se remplir
        self.terres, self.frontieres = lire_cotes()
        self.precision, self.marges = {}, {}
        self.version = version
        # heure Internet du programme (l'horloge du PC client est souvent reculée avant 2025) ;
        # à défaut, l'horloge du PC si elle est plausible (après le run du fichier)
        pc = dt.datetime.now(dt.timezone.utc)
        self.t0_heure, self.t0_pc = (maintenant, time.time()) if maintenant else \
            ((pc, time.time()) if pc >= self.d.run - dt.timedelta(hours=1) else (None, None))
        # heure affichée au-dessus des flèches : UNIQUEMENT l'heure Internet (jamais l'horloge du PC)
        self.heure_net = (maintenant if maintenant.tzinfo else maintenant.replace(tzinfo=dt.timezone.utc)) \
            if maintenant else None
        self.heure_net_mono = time.monotonic()
        if parent is None:
            activer_dpi()                          # carte lancée seule : texte net
        self.win = tk.Toplevel(parent) if parent is not None else tk.Tk()
        w = self.win
        regler_echelle(w)
        w.title(f"ACIS MétéoNav — Météo Marine Tunisie{titre_extra}")
        sl, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        L, H = min(S(1100), sl - S(40)), min(S(720), sh - S(90))
        w.geometry(f"{L}x{H}+{max(0, (sl - L) // 2)}+{max(0, (sh - S(60) - H) // 2)}")
        w.minsize(S(640), S(460))
        if hote:                                   # fenêtre du programme : plein écran dès l'ouverture
            try:
                w.state("zoomed")
            except Exception:                      # noqa: BLE001
                pass
        self.couche = tk.StringVar(value="vent")
        reg = lire_reglages()
        self.chiffres = tk.BooleanVar(value=reg.get("valeurs", True))
        self.marge_on = tk.BooleanVar(value=reg.get("marge", True))
        self.villes_on = tk.BooleanVar(value=reg.get("villes", True))
        self.vitesse = tk.IntVar(value=reg.get("vitesse_ms", 700))
        # où chaque couche est colorée (mer / terre / partout) et flèches du courant en plus de la couche
        self._zones = {c: z for c, z in (reg.get("zones") or {}).items()
                       if c in self.ZONES_COUCHE and z in ("mer", "terre", "partout")}
        self.zone_couleur = tk.StringVar(value="mer")
        self.avec_courant = tk.BooleanVar(value=bool(reg.get("avec_courant", False)))
        self._fen_param = None
        self.k = 0
        self.lecture = None
        self.repere = None
        self.survol = None
        self._survol_prevu = False
        self.vue = {"lat": 35.6, "lon": 10.8, "z": 1.0}
        self.W, self.H = 800, 500
        self._img = None
        self._img_cle = None
        self._drag = None

        haut = tk.Frame(w, bg=BLEU)
        haut.pack(side="top", fill="x")
        # lune (phase à l'heure affichée) ; au survol, sa fiche s'affiche dans le panneau de droite
        self.lune_survol = False
        self._lune_cache = {}
        self.cv_lune = tk.Canvas(haut, width=S(34), height=S(34), bg=BLEU, highlightthickness=0, cursor="hand2")
        self.cv_lune.pack(side="left", padx=(8, 0), pady=2)
        self.cv_lune.bind("<Enter>", lambda e: self._survol_lune(True))
        self.cv_lune.bind("<Leave>", lambda e: self._survol_lune(False))
        tk.Label(haut, text="ACIS MétéoNav", bg=BLEU, fg="white", font=("Segoe UI", 12, "bold")).pack(side="left", padx=(6, 12))
        self.cadre_couches = tk.Frame(haut, bg=BLEU)
        self.cadre_couches.pack(side="left")
        self._boutons_couches()
        self.cb_marge = None                       # « Valeurs » et « Marge de sécurité » : dans Paramètres (drapeau)
        self.texte_marge = "Marge de sécurité sur les vents forts"
        drapeau = tk.Canvas(haut, width=S(50), height=S(34), bg=BLEU, highlightthickness=0, cursor="hand2")
        drapeau.pack(side="right", padx=(12, 10), pady=2)            # isolé, en haut à droite
        dessiner_drapeau_tunisie(drapeau, S(1), S(1), S(48), S(32))
        drapeau.bind("<Button-1>", lambda e: self.ouvrir_parametres())   # le drapeau ouvre les paramètres
        self.zone = ttk.Combobox(haut, values=[z[0] for z in ZONES], state="readonly", width=22 if hote else 26)
        self.zone.set(ZONES[0][0])
        self.zone.pack(side="right", padx=6)
        self.zone.bind("<<ComboboxSelected>>", self._zone)
        self.lbl_abo = self.btn_dl = None
        if self.hote:                              # programme : bouton Télécharger (toujours là), ancre A.C.I.S
            self._ancre = self._icone_ancre("#ffffff", BLEU, S(46), angle=25)    # grande ancre inclinée
            self.btn_dl = _BoutonAncre(haut, self._ancre, "Télécharger", lambda: self.hote["telecharger"]())
            self.btn_dl.pack(side="right", padx=(4, 8), pady=3)

        bas = tk.Frame(w, bg="#eef0f6")
        bas.pack(side="bottom", fill="x")
        ligne = tk.Frame(bas, bg="#eef0f6")
        ligne.pack(fill="x", padx=6, pady=(4, 0))
        self.lbl_date = tk.Label(ligne, text="", font=("Segoe UI", 11, "bold"), fg=BLEU, bg="#eef0f6", width=24, anchor="w")
        self.lbl_date.pack(side="left", padx=8)
        self.curseur = _Frise(ligne, self)          # frise du temps : jours et heures, comme XyGrib
        self.curseur.pack(side="left", fill="x", expand=True)
        ligne2 = tk.Frame(bas, bg="#eef0f6")
        ligne2.pack(fill="x", padx=6)
        # à droite de la légende : où colorer (mer / terre / partout) et « + flèches du courant »
        self.cadre_options = tk.Frame(ligne2, bg="#eef0f6")
        self.cadre_options.pack(side="right", padx=(6, 0))
        self.cadre_zone = tk.Frame(self.cadre_options, bg="#eef0f6")
        tk.Label(self.cadre_zone, text="Couleurs :", bg="#eef0f6", fg=BLEU,
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 4))
        for val, lib in (("mer", "Mer"), ("terre", "Terre"), ("partout", "Partout")):
            tk.Radiobutton(self.cadre_zone, text=lib, value=val, variable=self.zone_couleur, indicatoron=False,
                           command=self._zone_change, font=("Segoe UI", 9, "bold"), bg="#dfe3f2", fg=BLEU,
                           selectcolor="#f6b400", relief="flat", offrelief="flat", padx=8, pady=1,
                           cursor="hand2").pack(side="left", padx=1)
        self.cb_courant = tk.Checkbutton(self.cadre_options, text="+ Flèches du courant", variable=self.avec_courant,
                                         command=self._options_change, bg="#eef0f6", fg=BLEU, activebackground="#eef0f6",
                                         font=("Segoe UI", 9, "bold"), cursor="hand2")
        self.legende = tk.Canvas(ligne2, height=S(30), bg="#eef0f6", highlightthickness=0)
        self.legende.pack(side="left", fill="x", expand=True)
        self._options_couche()
        pied = tk.Frame(bas, bg=BLEU)
        pied.pack(fill="x")
        self.lbl_info = tk.Label(pied, text="", bg=BLEU, fg="#7CFC9A", font=("Segoe UI", 9, "bold"))
        self.lbl_info.pack(side="left", padx=8, pady=3)
        tk.Label(pied, text=f"Météo Marine Tunisie · {version or 'v1.2026'} · créé par Claude @ A.C.I.S",
                 bg=BLEU, fg="#c9d2ff", font=("Segoe UI", 8, "italic")).pack(side="right", padx=8)
        # téléchargement en cours (programme) : texte + barre de progression
        self.lbl_statut = tk.Label(pied, text="", bg=BLEU, fg="#ffd166", font=("Segoe UI", 9, "bold"))
        self.lbl_statut.pack(side="left", padx=8)
        self.barre = ttk.Progressbar(pied, mode="determinate", length=S(160))

        centre = tk.Frame(w)
        centre.pack(fill="both", expand=True)
        droite = tk.Frame(centre, bg="#f7f8fc", width=S(330))
        droite.pack(side="right", fill="y")
        droite.pack_propagate(False)
        # bas du panneau : A.C.I.S (toujours en bas)
        tk.Label(droite, text="A.C.I.S Marine\nSfax 22 633 254 · Kélibia 94 596 447",
                 bg=BLEU, fg="white", font=("Segoe UI", 9, "bold"), justify="center",
                 pady=5).pack(side="bottom", fill="x")
        tk.Label(droite, text="NOAA GFS · GFS-Wave · Copernicus Marine",
                 bg="#f7f8fc", fg="#6b7290", font=("Segoe UI", 7), wraplength=S(315),
                 justify="center").pack(side="bottom", fill="x", pady=(2, 3))
        cmd = tk.Frame(droite, bg="#f7f8fc")
        cmd.pack(side="bottom", fill="x", padx=8, pady=4)
        # date et heure Internet (tout en haut du panneau de droite)
        entete = tk.Frame(droite, bg=BLEU)          # en-tête bleu : heure, abonnement… et XyGrib à droite
        entete.pack(side="top", fill="x")
        if "xygrib" in self.hote:                   # secours : la météo dans XyGrib (petit bouton en haut à droite)
            tk.Button(entete, text="XyGrib", command=lambda: self.hote["xygrib"](), font=("Segoe UI", 9, "bold"),
                      bg="#2c3a85", fg="white", activebackground="#3b4aa0", activeforeground="white", relief="flat",
                      padx=S(8), pady=S(2), cursor="hand2").pack(side="right", anchor="n", padx=(0, S(6)),
                                                                   pady=(S(6), 0))
        textes = tk.Frame(entete, bg=BLEU)
        textes.pack(side="left", fill="x", expand=True)
        self.lbl_heure = tk.Label(textes, text="…", bg=BLEU, fg="white",
                                  font=("Segoe UI", 12, "bold"), justify="center", pady=6 if not hote else 3)
        self.lbl_heure.pack(side="top", fill="x")
        if self.hote:                              # programme : nom du client et validité de l'abonnement
            self.lbl_abo = tk.Label(textes, text="", bg=BLEU, fg="#7CFC9A", font=("Segoe UI", 9, "bold"),
                                    justify="center", cursor="hand2", pady=3)
            self.lbl_abo.pack(side="top", fill="x")
            self.lbl_abo.bind("<Button-1>", lambda e: self.hote.get("activer", lambda: None)())
        gros = dict(font=("Segoe UI", 13, "bold"), bg=BLEU, fg="white", activebackground="#3b4aa0",
                    activeforeground="white", relief="flat", height=2, cursor="hand2")
        fleche = dict(gros, font=("Segoe UI Symbol", 20, "bold"), height=1)      # juste un grand triangle
        gros = dict(gros, height=1, pady=6)                                     # Zoom : un peu moins haut
        tk.Button(cmd, text="◀", command=lambda: self.avancer(-1), **fleche).grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=3)
        tk.Button(cmd, text="▶", command=lambda: self.avancer(1), **fleche).grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=3)
        pas = tk.Frame(cmd, bg="#f7f8fc")
        pas.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self.pas_h = tk.IntVar(value=reg.get("pas_h", 1) if reg.get("pas_h") in (1, 3) else 1)
        tk.Label(pas, text="Pas :", bg="#f7f8fc", font=("Segoe UI", 9, "bold"), fg=BLEU).pack(side="left")
        for h in (1, 3):
            tk.Radiobutton(pas, text=f"{h} h", value=h, variable=self.pas_h, indicatoron=False, width=4,
                           command=self._reglage_change,
                           font=("Segoe UI", 9, "bold"), bg="#dfe3f2", selectcolor="#f6b400",
                           relief="flat", offrelief="flat").pack(side="left", padx=2)
        self.btn_lect = tk.Button(pas, text="▶ Animation", command=self.basculer_lecture, font=("Segoe UI", 8),
                                  bg="#dfe3f2", relief="flat", cursor="hand2")
        self.btn_lect.pack(side="right")
        tk.Button(cmd, text="+  Zoom", command=lambda: self._zoomer(1.5, self.W / 2, self.H / 2), **gros).grid(row=2, column=0, sticky="ew", padx=(0, 3), pady=3)
        tk.Button(cmd, text="−  Zoom", command=lambda: self._zoomer(1 / 1.5, self.W / 2, self.H / 2), **gros).grid(row=2, column=1, sticky="ew", padx=(3, 0), pady=3)
        cmd.columnconfigure(0, weight=1, uniform="b")
        cmd.columnconfigure(1, weight=1, uniform="b")
        self.fiche = tk.Text(droite, font=("Segoe UI", 11), bg="#f7f8fc", relief="flat", wrap="word",
                             padx=8, pady=4, cursor="arrow", exportselection=False, takefocus=0,
                             selectbackground="#f7f8fc", inactiveselectbackground="#f7f8fc",
                             selectforeground="#000000", highlightthickness=0)
        self.fiche.pack(side="top", fill="both", expand=True)
        self.fiche.tag_configure("t", font=("Segoe UI", 14, "bold"), foreground=BLEU)
        self.fiche.tag_configure("b", font=("Segoe UI", 11, "bold"))
        self.fiche.tag_configure("m", font=("Segoe UI", 11, "bold"), foreground="#c0392b")
        self.fiche.tag_configure("g", foreground="#6b7290", font=("Segoe UI", 9))
        self.fiche.tag_configure("d", foreground=BLEU, font=("Segoe UI", 9))
        self.fiche.tag_configure("s", spacing1=6)                  # petit espace avant une rubrique
        self.cv = tk.Canvas(centre, bg="#1a2046", highlightthickness=0, cursor="crosshair")
        self.cv.pack(side="left", fill="both", expand=True)
        self.cv.bind("<Configure>", self._taille)
        self.cv.bind("<ButtonPress-1>", self._presse)
        self.cv.bind("<B1-Motion>", self._glisse)
        self.cv.bind("<ButtonRelease-1>", self._lache)
        self.cv.bind("<Motion>", self._survol)
        self.cv.bind("<Leave>", self._quitte)
        self.cv.bind("<MouseWheel>", self._molette)                  # Windows
        self.cv.bind("<Button-4>", lambda e: self._zoomer(1.25, e.x, e.y))   # Linux
        self.cv.bind("<Button-5>", lambda e: self._zoomer(0.8, e.x, e.y))
        # clavier : les flèches DÉPLACENT la carte ; + / - zooment ; l'heure : Page préc. / Page suiv.
        # (ou Ctrl + ← / →) ; Espace : animation. « break » : la liste des zones ne prend pas les flèches.
        for touche, (dx, dy) in (("<Left>", (-1, 0)), ("<Right>", (1, 0)), ("<Up>", (0, -1)), ("<Down>", (0, 1))):
            w.bind(touche, lambda e, dx=dx, dy=dy: self._clavier(lambda: self.deplacer(dx, dy), e))
        for touche, f in (("<plus>", 1.5), ("<KP_Add>", 1.5), ("<equal>", 1.5), ("<minus>", 1 / 1.5),
                          ("<KP_Subtract>", 1 / 1.5)):
            w.bind(touche, lambda e, f=f: self._clavier(lambda: self._zoomer(f, self.W / 2, self.H / 2), e))
        for touche, sens in (("<Prior>", -1), ("<Next>", 1), ("<Control-Left>", -1), ("<Control-Right>", 1)):
            w.bind(touche, lambda e, sens=sens: self._clavier(lambda: self.avancer(sens), e))
        w.bind("<space>", lambda e: self._clavier(self.basculer_lecture, e))
        self.cv.bind("<Enter>", lambda e: self.cv.focus_set(), add="+")
        self.cv.bind("<ButtonPress-1>", lambda e: self.cv.focus_set(), add="+")
        self.zone.bind("<<ComboboxSelected>>", lambda e: self.win.after(50, self.cv.focus_set), add="+")
        self.win.after(800, self.cv.focus_set)

        self.k = self._echeance_maintenant()
        self.curseur.set(self.k)
        self._ecrire_fiche()
        self._recu = []                            # rempli par le fil de téléchargement, lu par la fenêtre
        threading.Thread(target=lambda: self._recu.append(charger_precision()), daemon=True).start()
        self.win.after(300, self._attendre_precision)
        if self.heure_net is None:                 # carte lancée seule : on demande l'heure à Internet
            self._recu_heure = []

            def chercher():
                try:
                    import heure
                    self._recu_heure.append(heure.heure_internet())
                except Exception as e:             # noqa: BLE001
                    self._recu_heure.append(e)
            threading.Thread(target=chercher, daemon=True).start()
            self.win.after(300, self._attendre_heure)
        self._heure_tick()
        if self.hote:
            self.win.protocol("WM_DELETE_WINDOW", lambda: self.hote.get("quitter", self.win.destroy)())
            self.maj_abonnement()

    # ---------------- options de la couche : zone des couleurs, flèches du courant en plus
    def _options_couche(self):
        """Montre seulement les options utiles à la couche choisie."""
        couche = self.couche.get()
        if couche in self.ZONES_COUCHE:
            self.zone_couleur.set(self.zone_de(couche))
            if not self.cadre_zone.winfo_ismapped():
                self.cadre_zone.pack_forget()
                self.cb_courant.pack_forget()                # « Couleurs » toujours avant « Flèches du courant »
                self.cadre_zone.pack(side="left", padx=(0, 10))
        elif self.cadre_zone.winfo_ismapped():
            self.cadre_zone.pack_forget()
        if self.d.courants and couche != "courant":
            if not self.cb_courant.winfo_ismapped():
                self.cb_courant.pack(side="left")
        elif self.cb_courant.winfo_ismapped():
            self.cb_courant.pack_forget()

    def _sauver_options(self):
        r = lire_reglages()
        r.update(zones=dict(self._zones), avec_courant=bool(self.avec_courant.get()))
        ecrire_reglages(r)

    def _zone_change(self):
        couche = self.couche.get()
        if couche in self.ZONES_COUCHE:
            self._zones[couche] = self.zone_couleur.get()
            self._sauver_options()
        self.dessiner()

    def _options_change(self):
        self._sauver_options()
        self.dessiner()

    def _couche_change(self):
        self._options_couche()
        self.dessiner()

    # ---------------- boutons des couches (refaits quand un nouveau fichier est chargé)
    def _boutons_couches(self):
        tk = self.tk
        for b in self.cadre_couches.winfo_children():
            b.destroy()
        # « Carte » : la carte seule, sans aucune donnée météo
        tk.Radiobutton(self.cadre_couches, text="Carte", value="carte", variable=self.couche, indicatoron=False,
                       command=self._couche_change, bg="#2c3a85", fg="white", selectcolor="#f6b400",
                       activebackground="#3b4aa0", font=("Segoe UI", 11, "bold"), padx=10, pady=3, relief="flat",
                       offrelief="flat").pack(side="left", padx=(1, 6), pady=4)
        for nom, (lib, *_r) in COUCHES.items():
            if nom == "rafales" and not any("GUST" in self.d.par[e] for e in self.d.echeances[:3]):
                continue
            if nom in ("pluie", "nuages") and not any(("PRATE" if nom == "pluie" else "TCDC") in self.d.par[e]
                                                       for e in self.d.echeances[:6]):
                continue
            if nom == "courant" and not self.d.courants:
                continue
            if nom == "maree" and not self.d.maree:
                continue
            tk.Radiobutton(self.cadre_couches, text=lib, value=nom, variable=self.couche, indicatoron=False,
                           command=self._couche_change, bg="#2c3a85", fg="white", selectcolor="#f6b400",
                           activebackground="#3b4aa0", font=("Segoe UI", 11, "bold"), padx=10, pady=3, relief="flat",
                           offrelief="flat").pack(side="left", padx=1, pady=4)
        if hasattr(self, "cadre_zone"):
            self._options_couche()

    # ---------------- liens avec le programme principal (hote)
    def statut(self, texte: str, couleur: str = "#ffd166"):
        """Message du programme (téléchargement…) dans la barre du bas. Appelé depuis le fil principal."""
        self.lbl_statut.config(text=texte, fg=couleur)

    def progression(self, i: int, n: int):
        if n and i < n:
            if not self.barre.winfo_ismapped():
                self.barre.pack(side="left", padx=4, before=self.lbl_statut)
            self.barre.config(maximum=n, value=i)
        elif self.barre.winfo_ismapped():
            self.barre.pack_forget()

    def en_cours(self, oui: bool):
        if self.btn_dl is not None:
            # pendant le téléchargement : texte gris (un 2e clic est ignoré par le programme)
            self.btn_dl.config(fg="#8a90a8" if oui else ROUGE_BOUTON, cursor="watch" if oui else "hand2",
                               text="En cours…" if oui else "Télécharger")
        if not oui:
            self.progression(0, 0)

    def maj_abonnement(self):
        """Nom du client et validité de l'abonnement, en haut (fournis par le programme)."""
        if self.lbl_abo is None or "abonnement" not in self.hote:
            return
        try:
            nom, detail, couleur = self.hote["abonnement"]()
        except Exception:                            # noqa: BLE001
            return
        self.lbl_abo.config(text=f"{nom}\n{detail}", fg=couleur)
        self.win.title(f"ACIS MétéoNav — {nom} — Météo Marine Tunisie")

    def charger(self, chemin: str):
        """Nouveau fichier météo (après un téléchargement) : la carte se met à jour sans se fermer."""
        d = Donnees(chemin)
        self.d = d
        self._img = self._img_cle = None
        self._lune_cache = {}
        self.curseur.config(to=max(0, len(d.echeances) - 1))
        self._boutons_couches()
        if self.couche.get() != "carte" and self.d.grille(self.couche.get(), 0) is None:
            self.couche.set("vent")
        self._options_couche()
        self.k = self._echeance_maintenant()
        self.curseur.set(self.k)
        self.dessiner()
        self._ecrire_fiche()

    # ---------------- heure Internet (affichée au-dessus des flèches)
    def regler_heure(self, h: dt.datetime):
        """Heure Internet obtenue par le programme principal."""
        h = h if h.tzinfo else h.replace(tzinfo=dt.timezone.utc)
        self.heure_net, self.heure_net_mono = h, time.monotonic()
        self.t0_heure, self.t0_pc = h, time.time()
        self._heure_tick(boucle=False)

    def _attendre_heure(self):
        if not self._recu_heure:
            try:
                self.win.after(300, self._attendre_heure)
            except Exception:                       # noqa: BLE001
                pass
            return
        r = self._recu_heure.pop()
        if isinstance(r, dt.datetime):
            self.heure_net, self.heure_net_mono = r, time.monotonic()
            self.t0_heure, self.t0_pc = r, time.time()    # l'échéance « maintenant » suit aussi l'heure Internet
            self._heure_tick(boucle=False)
        else:
            self.lbl_heure.config(text="Heure indisponible (connexion ?)", fg="#ff8a80")

    def _heure_tick(self, boucle: bool = True):
        if self.heure_net is not None:
            t = self.heure_net + dt.timedelta(seconds=time.monotonic() - self.heure_net_mono) + TUNISIE
            self.lbl_heure.config(text=f"{JOURS[t.weekday()]} {t:%d/%m/%Y}   {t:%H:%M:%S}", fg="white")
        if boucle:
            try:
                self.win.after(1000, self._heure_tick)
            except Exception:                       # noqa: BLE001
                pass

    # ---------------- paramètres (clic sur le drapeau)
    def ouvrir_parametres(self):
        tk = self.tk
        if self._fen_param is not None and self._fen_param.winfo_exists():
            self._fen_param.lift()
            self._fen_param.focus_force()
            return
        f = tk.Toplevel(self.win)
        self._fen_param = f
        f.title("Paramètres — ACIS MétéoNav")
        f.configure(bg="#f7f8fc")
        f.resizable(False, False)
        f.transient(self.win)
        tk.Label(f, text="⚙  Paramètres", bg=BLEU, fg="white", font=("Segoe UI", 12, "bold"),
                 pady=6).pack(fill="x")
        colonnes = tk.Frame(f, bg="#f7f8fc")
        colonnes.pack(fill="both")
        corps = tk.Frame(colonnes, bg="#f7f8fc", padx=14, pady=10)
        corps.pack(side="left", fill="both", anchor="n")
        tk.Label(corps, text="Carte", bg="#f7f8fc", fg=BLEU, font=("Segoe UI", 11, "bold"),
                 anchor="w").pack(fill="x", pady=(0, 4))
        if "parametres" in self.hote:                # programme : téléchargement, abonnement…
            tk.Frame(colonnes, bg="#c9cfe3", width=1).pack(side="left", fill="y", pady=10)
            prog = tk.Frame(colonnes, bg="#f7f8fc", padx=14, pady=10)
            prog.pack(side="left", fill="both", expand=True, anchor="n")
            try:
                self.hote["parametres"](prog, f)
            except Exception as e:                   # noqa: BLE001
                tk.Label(prog, text=f"Paramètres du programme indisponibles : {e}", bg="#f7f8fc",
                         fg="#c0392b").pack()
        opt = dict(bg="#f7f8fc", font=("Segoe UI", 10), anchor="w", activebackground="#f7f8fc")
        tk.Checkbutton(corps, text="Afficher les valeurs (nœuds, mètres)", variable=self.chiffres,
                       command=self._reglage_change, **opt).pack(fill="x")
        self.cb_marge = tk.Checkbutton(corps, text=self.texte_marge, variable=self.marge_on,
                                       command=self._reglage_change, **opt)
        self.cb_marge.pack(fill="x")
        tk.Checkbutton(corps, text="Afficher les noms des villes", variable=self.villes_on,
                       command=self._reglage_change, **opt).pack(fill="x")
        lp = tk.Frame(corps, bg="#f7f8fc")
        lp.pack(fill="x", pady=(10, 0))
        tk.Label(lp, text="Pas des flèches ◀ ▶ :", **opt).pack(side="left")
        for h in (1, 3):
            tk.Radiobutton(lp, text=f"{h} h", value=h, variable=self.pas_h, command=self._reglage_change,
                           **opt).pack(side="left", padx=4)
        tk.Label(corps, text="Vitesse de l'animation (une image toutes les … ms) :", **opt).pack(fill="x", pady=(10, 0))
        tk.Scale(corps, from_=200, to=2000, resolution=100, orient="horizontal", variable=self.vitesse,
                 command=lambda _v: self._reglage_change(redessiner=False), bg="#f7f8fc", highlightthickness=0,
                 length=S(300)).pack(fill="x")
        tk.Label(corps, text=f"Fichier : {os.path.basename(self.d.chemin)}", bg="#f7f8fc", fg="#6b7290",
                 font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(10, 0))
        tk.Button(f, text="✔ Fermer", command=f.destroy, font=("Segoe UI", 11, "bold"), bg=BLEU, fg="white",
                  activebackground="#3b4aa0", activeforeground="white", relief="flat", cursor="hand2",
                  pady=4).pack(fill="x", padx=14, pady=(0, 12))
        f.update_idletasks()                         # centrée sur la carte
        x = self.win.winfo_rootx() + (self.win.winfo_width() - f.winfo_width()) // 2
        y = self.win.winfo_rooty() + (self.win.winfo_height() - f.winfo_height()) // 3
        f.geometry(f"+{max(0, x)}+{max(0, y)}")
        f.bind("<Escape>", lambda e: f.destroy())

    def _reglage_change(self, redessiner: bool = True):
        ecrire_reglages({"valeurs": self.chiffres.get(), "marge": self.marge_on.get(),
                         "villes": self.villes_on.get(), "vitesse_ms": int(self.vitesse.get()),
                         "pas_h": int(self.pas_h.get())})
        if redessiner:
            self.dessiner()
            self._ecrire_fiche()

    # ---------------- précision (en arrière-plan : la carte s'ouvre tout de suite ;
    # tkinter ne doit être touché que par le fil principal, d'où cette petite attente)
    def _attendre_precision(self):
        if self._recu:
            self._precision_prete(self._recu.pop())
        else:
            try:
                self.win.after(300, self._attendre_precision)
            except Exception:                       # noqa: BLE001  (fenêtre fermée)
                pass

    def _precision_prete(self, p):
        self.precision, self.marges = p, marges(p)
        self.dessiner()
        self._ecrire_fiche()

    def marge(self, k: int, kt: float) -> int:
        if not self.marge_on.get() or kt < MARGE_SEUIL_KT:
            return 0
        jour = int(self.d.echeances[k] // 24)
        return self.marges.get(jour, 0)

    # ---------------- temps
    def maintenant(self):
        """Heure réelle (UTC) ou None si inconnue."""
        if self.t0_heure is None:
            return None
        h = self.t0_heure if self.t0_heure.tzinfo else self.t0_heure.replace(tzinfo=dt.timezone.utc)
        return h + dt.timedelta(seconds=time.time() - self.t0_pc)

    def _echeance_maintenant(self) -> int:
        maintenant = self.maintenant()
        if maintenant is None:
            return 0
        meilleur = 0
        for i in range(len(self.d.echeances)):
            if self.d.date(i) <= maintenant:
                meilleur = i
        return meilleur

    def aller(self, k: int, depuis_curseur: bool = False):
        k = max(0, min(len(self.d.echeances) - 1, k))
        if k == self.k and depuis_curseur:
            return
        self.k = k
        if not depuis_curseur:
            self.curseur.set(k)
        self.dessiner()
        self._ecrire_fiche()

    def avancer(self, sens: int):
        """Heure suivante / précédente selon le pas choisi (1 h ou 3 h)."""
        cible = self.d.echeances[self.k] + sens * self.pas_h.get()
        k = self.k
        if sens > 0:
            while k < len(self.d.echeances) - 1 and self.d.echeances[k] < cible:
                k += 1
        else:
            while k > 0 and self.d.echeances[k] > cible:
                k -= 1
        self.aller(k)

    def basculer_lecture(self):
        if self.lecture:
            self.win.after_cancel(self.lecture)
            self.lecture = None
            self.btn_lect.config(text="▶ Animation")
            return
        self.btn_lect.config(text="■ Arrêter")

        def pas():
            if self.k >= len(self.d.echeances) - 1:
                self.aller(0)
            else:
                self.avancer(1)
            self.lecture = self.win.after(max(200, int(self.vitesse.get())), pas)
        pas()

    # ---------------- vue
    def _taille(self, e):
        self.W, self.H = max(50, e.width), max(50, e.height)
        self._borner()
        self.dessiner()

    def _ppd(self) -> float:
        la0, la1, lo0, lo1 = 32.6, 38.6, 7.6, 13.9            # zoom 1 : la Tunisie entière
        cos = math.cos(math.radians(35.6))
        return min(self.W / (lo1 - lo0), self.H * cos / (la1 - la0)) * 1.02 * self.vue["z"]

    def xy(self, lat, lon):
        p, cos = self._ppd(), math.cos(math.radians(35.6))
        return self.W / 2 + (lon - self.vue["lon"]) * p, self.H / 2 - (lat - self.vue["lat"]) * p / cos

    def geo(self, x, y):
        p, cos = self._ppd(), math.cos(math.radians(35.6))
        return self.vue["lat"] - (y - self.H / 2) * cos / p, self.vue["lon"] + (x - self.W / 2) / p

    def _borner(self):
        la0, la1, lo0, lo1 = self.d.zone
        self.vue["z"] = max(0.35, min(8.0, self.vue["z"]))
        p, cos = self._ppd(), math.cos(math.radians(35.6))
        dlon, dlat = self.W / 2 / p, self.H / 2 * cos / p

        def lim(v, a, b, d):
            return max(a + d, min(b - d, v)) if b - a > 2 * d else (a + b) / 2
        self.vue["lon"] = lim(self.vue["lon"], lo0, lo1, dlon)
        self.vue["lat"] = lim(self.vue["lat"], la0, la1, dlat)

    def _zone(self, _e=None):
        for nom, la, lo, z in ZONES:
            if nom == self.zone.get():
                if la is None:
                    la0, la1, lo0, lo1 = self.d.zone
                    la, lo, z = (la0 + la1) / 2, (lo0 + lo1) / 2, 0.35
                self.vue.update(lat=la, lon=lo, z=z)
        self._borner()
        self.dessiner()

    def deplacer(self, dx: int, dy: int):
        """Fait glisser la vue d'un quart d'écran (flèches du clavier)."""
        p, cos = self._ppd(), math.cos(math.radians(35.6))
        self.vue["lon"] += dx * (self.W / 4) / p
        self.vue["lat"] -= dy * (self.H / 4) * cos / p
        self._borner()
        self.dessiner()

    def _clavier(self, action, e):
        """Touche du clavier : ignorée si on écrit dans une case (liste des zones…), sinon action."""
        cls = e.widget.winfo_class() if hasattr(e.widget, "winfo_class") else ""
        if cls in ("Entry", "TEntry", "TCombobox", "Text", "Spinbox"):
            return None
        action()
        return "break"

    def _molette(self, e):
        self._zoomer(1.25 if e.delta > 0 else 0.8, e.x, e.y)

    def _zoomer(self, f, x, y):
        la, lo = self.geo(x, y)
        self.vue["z"] *= f
        self._borner()
        la2, lo2 = self.geo(x, y)                   # le point sous la souris ne bouge pas
        self.vue["lat"] += la - la2
        self.vue["lon"] += lo - lo2
        self._borner()
        self.dessiner()

    def _presse(self, e):
        self._drag = (e.x, e.y, self.vue["lat"], self.vue["lon"], False)

    def _glisse(self, e):
        if not self._drag:
            return
        x0, y0, la, lo, _ = self._drag
        if abs(e.x - x0) + abs(e.y - y0) < 4 and not self._drag[4]:
            return
        self._drag = (x0, y0, la, lo, True)
        p, cos = self._ppd(), math.cos(math.radians(35.6))
        self.vue["lon"] = lo - (e.x - x0) / p
        self.vue["lat"] = la + (e.y - y0) * cos / p
        self._borner()
        self.dessiner(rapide=True)

    def _survol(self, e):
        """Le curseur qui se déplace affiche la position et la météo du point survolé."""
        if self._drag:
            return
        self.survol = self.geo(e.x, e.y)
        if not self._survol_prevu:                  # au plus ~12 mises à jour par seconde
            self._survol_prevu = True
            self.win.after(80, self._survol_maj)

    def _survol_maj(self):
        self._survol_prevu = False
        self._ecrire_fiche()

    def _quitte(self, _e):
        self.survol = None
        self._ecrire_fiche()

    def _lache(self, e):
        bouge = self._drag and self._drag[4]
        self._drag = None
        if bouge:
            self.dessiner()
        else:
            self.repere = self.geo(e.x, e.y)
            self.dessiner()
            self._ecrire_fiche()

    # ---------------- dessin
    def _masque_terre(self, w: int, h: int, PAS: int) -> list:
        """Pour chaque case de l'image (w x h), True si elle est sur la terre (côtes de la carte).
        Remplissage par lignes : chaque côté de côte ne touche que les lignes qu'il traverse (rapide)."""
        cle = (w, h, PAS, self.W, self.H, round(self.vue["lat"], 5), round(self.vue["lon"], 5), round(self.vue["z"], 5))
        if getattr(self, "_masque_cle", None) == cle:
            return self._masque
        coupes = [[] for _ in range(h)]
        for poly in self.terres:
            pts = [self.xy(la, lo) for la, lo in poly]
            n = len(pts)
            if n < 3:
                continue
            for i in range(n):
                x1, y1 = pts[i]
                x2, y2 = pts[(i + 1) % n]
                if y1 == y2:
                    continue
                ya, yb = (y1, y2) if y1 < y2 else (y2, y1)
                r0 = max(0, int(math.ceil(ya / PAS - 0.5)))
                r1 = min(h - 1, int(math.ceil(yb / PAS - 0.5)) - 1)
                for r in range(r0, r1 + 1):
                    yc = (r + 0.5) * PAS
                    coupes[r].append(x1 + (x2 - x1) * (yc - y1) / (y2 - y1))
        masque = []
        for r in range(h):
            ligne = bytearray(w)
            xs = sorted(coupes[r])
            for i in range(0, len(xs) - 1, 2):
                c0 = max(0, int(math.ceil(xs[i] / PAS - 0.5)))
                c1 = min(w - 1, int(math.ceil(xs[i + 1] / PAS - 0.5)) - 1)
                for c in range(c0, c1 + 1):
                    ligne[c] = 1
            masque.append(ligne)
        self._masque_cle, self._masque = cle, masque
        return masque

    def _image(self, couche: str, zone: str = "mer"):
        """Image des couleurs (1 pixel calculé tous les PAS pixels, puis agrandie).
        zone « terre » : la mer reste sombre (seule la terre est colorée)."""
        tk = self.tk
        PAS = max(4, S(4))
        g = self.d.grille(couche, self.k)
        if g is None:
            return None
        cle = (couche, zone, self.k, self.W, self.H, round(self.vue["lat"], 5), round(self.vue["lon"], 5),
               round(self.vue["z"], 5))
        if cle == self._img_cle:
            return self._img
        lib, ech, _u, _g, reg = COUCHES[couche]
        table, indice = palette(ech, reg)
        fond = "#1a2046"
        w, h = (self.W + PAS - 1) // PAS, (self.H + PAS - 1) // PAS
        p, cos = self._ppd(), math.cos(math.radians(35.6))
        lons = [self.vue["lon"] + ((x + 0.5) * PAS - self.W / 2) / p for x in range(w)]
        # colonnes : indice et poids précalculés (accélère beaucoup en Python)
        cols = []
        for lo in lons:
            xg = (lo - g.lon1) / g.dlon
            i0 = int(math.floor(xg))
            cols.append((i0, xg - i0) if 0 <= i0 < g.ni else None)
        v, ni = g.v, g.ni
        masque = self._masque_terre(w, h, PAS) if zone == "terre" else None
        lignes = []
        for y in range(h):
            la = self.vue["lat"] - ((y + 0.5) * PAS - self.H / 2) * cos / p
            yg = (la - g.lat1) / g.dlat
            j0 = int(math.floor(yg))
            if not 0 <= j0 < g.nj:
                lignes.append("{" + " ".join([fond] * w) + "}")
                continue
            fy = yg - j0
            j1 = min(j0 + 1, g.nj - 1)
            b0, b1 = j0 * ni, j1 * ni
            ligne = []
            mligne = masque[y] if masque is not None else None
            for xi, c in enumerate(cols):
                if c is None:
                    ligne.append(fond)
                    continue
                if mligne is not None and not mligne[xi]:
                    ligne.append("#2b3358")          # mer (mode « Terre ») : un peu plus claire que la terre sèche
                    continue
                i0, fx = c
                i1 = i0 + 1 if i0 + 1 < ni else i0
                a, b, cc, d = v[b0 + i0], v[b0 + i1], v[b1 + i0], v[b1 + i1]
                if a is not None and b is not None and cc is not None and d is not None:
                    val = (a * (1 - fx) + b * fx) * (1 - fy) + (cc * (1 - fx) + d * fx) * fy
                else:
                    s = q = 0.0
                    for vv, ww in ((a, (1 - fx) * (1 - fy)), (b, fx * (1 - fy)), (cc, (1 - fx) * fy), (d, fx * fy)):
                        if vv is not None:
                            s += vv * ww
                            q += ww
                    if q < 0.25:
                        ligne.append(fond)
                        continue
                    val = s / q
                ligne.append(table[indice(val)])
            lignes.append("{" + " ".join(ligne) + "}")
        img = tk.PhotoImage(width=w, height=h)
        img.put(" ".join(lignes))
        img = img.zoom(PAS)
        self._img, self._img_cle = img, cle
        return img

    def dessiner(self, rapide: bool = False):
        cv = self.cv
        cv.delete("all")
        couche = self.couche.get()
        if couche != "carte" and self.d.grille(couche, self.k) is None and couche != "vent":
            couche = "vent"
        zone = self.zone_de(couche)
        img = None
        if couche != "carte" and (not rapide or self._img is None):
            img = self._image(couche, zone)
        if img is not None:
            cv.create_image(0, 0, image=img, anchor="nw")
        # terres : remplies quand la couleur est seulement en mer (et carte seule) ; sinon, côtes bien visibles
        # (trait sombre + trait clair : on les voit sur des nuages blancs comme sur des couleurs foncées)
        remplir = couche == "carte" or zone == "mer"
        self._fil_zone = None                        # (le logo n'est dessiné que sur la terre pleine)
        for poly in self.terres:
            pts = []
            for la, lo in poly:
                x, y = self.xy(la, lo)
                pts += (x, y)
            if len(pts) >= 6:
                if remplir:
                    cv.create_polygon(pts, fill="#3b3f5c", outline="#0a0c1e", width=1.2 * ECH)
                else:
                    cv.create_polygon(pts, fill="", outline="#0a0c1e", width=3.2 * ECH)
                    cv.create_polygon(pts, fill="", outline="#f2f4fb", width=1.3 * ECH)
        for ligne in self.frontieres:
            pts = []
            for la, lo in ligne:
                pts += self.xy(la, lo)
            if len(pts) >= 4:
                if not remplir:
                    cv.create_line(pts, fill="#0a0c1e", width=2.6 * ECH)
                cv.create_line(pts, fill="#c8cbe0" if remplir else "#ffffff", dash=(5, 4), width=1 * ECH)
        if remplir:
            self._filigrane()
        if not rapide:
            self._symboles(couche)
            if self.villes_on.get():
                self._villes()
        if self.repere:
            x, y = self.xy(*self.repere)
            for lw, c in ((5, "#000000"), (2, "#ffffff")):
                for a, b in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    cv.create_line(x + a * S(5), y + b * S(5), x + a * S(13), y + b * S(13), width=lw * ECH, fill=c)
        self._dessiner_lune()
        self._entete(couche)
        self._legende(couche)

    # ---------------- sigle A.C.I.S Marine en filigrane (sur la terre, entre Algérie, Tunisie et Libye)
    FILIGRANE = (34.8, 8.15)                     # lat, lon : terre, frontière tuniso-algérienne (Kasserine–Tébessa)

    @staticmethod
    def _chemin_logo():
        """Fichier du logo A.C.I.S (ressources/logo_256.png, sinon logo_144 / logo_96), ou None."""
        ici = os.path.dirname(os.path.abspath(__file__))
        base = getattr(sys, "_MEIPASS", ici)
        for d in (ici, os.path.join(ici, "ressources"), os.path.join(base, "ressources"),
                  os.path.join(os.path.dirname(ici), "ressources")):
            for n in ("logo_256.png", "logo_144.png", "logo_96.png"):
                p = os.path.join(d, n)
                if os.path.exists(p):
                    return p
        return None

    @staticmethod
    def _dans_ancre(x: float, y: float) -> bool:
        """Ancre marine complète, dessinée (repère : x de -1 à 1, y de -1.2 en haut à 1.2 en bas) :
        anneau, jas (barre du haut, bouts arrondis), verge, bras en arc, pattes et diamant."""
        # anneau
        d = math.hypot(x, y + 0.98)
        if 0.11 <= d <= 0.21:
            return True
        # jas
        if -0.50 <= x <= 0.50 and -0.70 <= y <= -0.58:
            return True
        if math.hypot(abs(x) - 0.50, y + 0.64) <= 0.085:
            return True
        # verge
        if -0.075 <= x <= 0.075 and -0.80 <= y <= 0.92:
            return True
        # bras : arc de cercle (centre sous la croisée), partie basse seulement
        r = math.hypot(x, y - 0.18)
        if 0.66 <= r <= 0.79 and y >= 0.30:
            return True
        # pattes : triangles au bout des bras, pointes vers le haut et l'extérieur
        ax = abs(x)
        for (x1, y1), (x2, y2), (x3, y3) in (((0.56, 0.40), (0.94, 0.50), (0.84, 0.10)),):
            den = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
            a = ((y2 - y3) * (ax - x3) + (x3 - x2) * (y - y3)) / den
            b = ((y3 - y1) * (ax - x3) + (x1 - x3) * (y - y3)) / den
            if a >= 0 and b >= 0 and a + b <= 1:
                return True
        # diamant (bas de la verge)
        if abs(x) + abs(y - 0.97) <= 0.15:
            return True
        return False

    @staticmethod
    def _chemin_ancre():
        """Image de l'ancre A.C.I.S (ancre.png, à côté du programme ou dans ressources/), ou None."""
        ici = os.path.dirname(os.path.abspath(__file__))
        base = getattr(sys, "_MEIPASS", ici)
        for d in (ici, os.path.join(ici, "ressources"), os.path.join(base, "ressources"),
                  os.path.join(os.path.dirname(ici), "ressources")):
            p = os.path.join(d, "ancre.png")
            if os.path.exists(p):
                return p
        return None

    def _image_ancre(self):
        """Image de l'ancre : ancre.txt (PNG encodé en base64, livré par la mise à jour en ligne comme carte.html),
        sinon ancre.txt / ancre.png à côté du programme ou dans ressources/. None si rien."""
        texte = None
        try:
            import maj_en_ligne
            b = maj_en_ligne.ressource("ancre.txt")
            texte = b.decode("ascii").strip() if b else None
        except Exception:                            # noqa: BLE001
            texte = None
        if not texte:
            ici = os.path.dirname(os.path.abspath(__file__))
            base = getattr(sys, "_MEIPASS", ici)
            for d in (ici, os.path.join(ici, "ressources"), os.path.join(base, "ressources")):
                p = os.path.join(d, "ancre.txt")
                if os.path.exists(p):
                    try:
                        texte = open(p, encoding="ascii").read().strip()
                        break
                    except OSError:
                        pass
        if texte:
            try:
                return self.tk.PhotoImage(data=texte)
            except Exception:                        # noqa: BLE001
                pass
        p = self._chemin_ancre()
        return self.tk.PhotoImage(file=p) if p else None

    def _couverture_ancre_image(self):
        """Ancre de ancre.png : grille 0..1 (1 = trait de l'ancre), recadrée au plus près ; None si absente."""
        if hasattr(self, "_cov_ancre"):
            return self._cov_ancre
        self._cov_ancre = None
        try:
            src = self._image_ancre()
            if src is None:
                return None
            while src.width() > 300:
                src = src.subsample(2)
            w, h = src.width(), src.height()
            g = []
            for y in range(h):
                lig = []
                for x in range(w):
                    if src.transparency_get(x, y):
                        lig.append(0.0)
                        continue
                    r, gg, b = src.get(x, y)[:3]
                    lig.append(max(0.0, min(1.0, (215 - (0.3 * r + 0.59 * gg + 0.11 * b)) / 150)))
                g.append(lig)
            ys = [y for y in range(h) if max(g[y]) > 0.3]
            xs = [x for x in range(w) if max(g[y][x] for y in range(h)) > 0.3]
            if not ys or not xs:
                return None
            y0, y1, x0, x1 = max(0, ys[0] - 1), min(h, ys[-1] + 2), max(0, xs[0] - 1), min(w, xs[-1] + 2)
            self._cov_ancre = [row[x0:x1] for row in g[y0:y1]]
        except Exception:                            # noqa: BLE001
            self._cov_ancre = None
        return self._cov_ancre

    def _icone_ancre(self, fond: str, couleur: str, taille: int = 26, angle: float = 0.0):
        """Ancre A.C.I.S complète (image ancre.png, sinon dessinée), bords adoucis, « couleur » sur « fond »,
        carré de « taille » px, inclinée de « angle » degrés (sens des aiguilles d'une montre)."""
        try:
            fr, fg, fb = _hex(fond)
            cr, cg, cb = _hex(couleur)
            a = math.radians(angle)
            ca, sa = math.cos(a), math.sin(a)
            cov = self._couverture_ancre_image()
            if cov:                                   # image fournie par A.C.I.S
                ch, cw = len(cov), len(cov[0])
                echelle = (taille * 0.96) / max(cw * abs(ca) + ch * abs(sa), cw * abs(sa) + ch * abs(ca))
                Q, c0 = 3, taille / 2
                out = self.tk.PhotoImage(width=taille, height=taille)
                lignes = []
                for ty in range(taille):
                    lig = []
                    for tx in range(taille):
                        s_ = 0.0
                        for j in range(Q):
                            for i in range(Q):
                                u = (tx + (i + 0.5) / Q - c0) / echelle
                                v = (ty + (j + 0.5) / Q - c0) / echelle
                                xs_ = u * ca + v * sa + cw / 2
                                ys_ = -u * sa + v * ca + ch / 2
                                xi, yi = int(math.floor(xs_)), int(math.floor(ys_))
                                if 0 <= xi < cw - 1 and 0 <= yi < ch - 1:
                                    dx, dy = xs_ - xi, ys_ - yi
                                    s_ += (cov[yi][xi] * (1 - dx) * (1 - dy) + cov[yi][xi + 1] * dx * (1 - dy)
                                           + cov[yi + 1][xi] * (1 - dx) * dy + cov[yi + 1][xi + 1] * dx * dy)
                        k = min(1.0, s_ / (Q * Q) * 1.15)
                        lig.append("#%02x%02x%02x" % (int(fr + (cr - fr) * k), int(fg + (cg - fg) * k),
                                                      int(fb + (cb - fb) * k)))
                    lignes.append("{" + " ".join(lig) + "}")
                out.put(" ".join(lignes))
                return out
            # l'ancre (2 x 2.4) tournée doit tenir dans le carré, avec une petite marge
            echelle = (taille * 0.94) / max(2 * abs(ca) + 2.4 * abs(sa), 2 * abs(sa) + 2.4 * abs(ca))
            Q = 4                                     # 4 x 4 points par pixel : bords adoucis
            c0 = taille / 2
            out = self.tk.PhotoImage(width=taille, height=taille)
            lignes = []
            for ty in range(taille):
                lig = []
                for tx in range(taille):
                    n = 0
                    for j in range(Q):
                        for i in range(Q):
                            u = (tx + (i + 0.5) / Q - c0) / echelle
                            v = (ty + (j + 0.5) / Q - c0) / echelle
                            if self._dans_ancre(u * ca + v * sa, -u * sa + v * ca):
                                n += 1
                    k = n / (Q * Q)
                    lig.append("#%02x%02x%02x" % (int(fr + (cr - fr) * k), int(fg + (cg - fg) * k),
                                                  int(fb + (cb - fb) * k)))
                lignes.append("{" + " ".join(lig) + "}")
            out.put(" ".join(lignes))
            return out
        except Exception:                            # noqa: BLE001
            return None

    def _sources_logo(self) -> list:
        """Où trouver le logo, dans l'ordre : logo.txt (PNG 256 px en base64, livré par la mise à jour en ligne
        comme carte.html, ou rangé à côté du programme), puis les logo_*.png de ressources/."""
        sources = []
        try:
            import maj_en_ligne
            b = maj_en_ligne.ressource("logo.txt")
            if b:
                sources.append(("data", b.decode("ascii").strip()))
        except Exception:                            # noqa: BLE001
            pass
        ici = os.path.dirname(os.path.abspath(__file__))
        base = getattr(sys, "_MEIPASS", ici)
        dossiers = (ici, os.path.join(ici, "ressources"), os.path.join(base, "ressources"),
                    os.path.join(os.path.dirname(ici), "ressources"))
        for d in dossiers:
            p = os.path.join(d, "logo.txt")
            if os.path.exists(p):
                try:
                    sources.append(("data", open(p, encoding="ascii").read().strip()))
                except OSError:
                    pass
        for n in ("logo_256.png", "logo_144.png", "logo_96.png"):
            for d in dossiers:
                p = os.path.join(d, n)
                if os.path.exists(p):
                    sources.append(("file", p))
        return sources

    def _logo_filigrane(self):
        """Logo A.C.I.S (logo.txt, sinon logo_256/144/96.png) éclairci vers la couleur de la terre ; None si absent."""
        if hasattr(self, "_logo_fil"):
            return self._logo_fil
        self._logo_fil = None
        for genre, valeur in self._sources_logo():
            for _une_fois in (1,):
                try:
                    src = self.tk.PhotoImage(data=valeur) if genre == "data" else self.tk.PhotoImage(file=valeur)
                    if src.width() >= 256 and ECH < 1.3:     # 256 -> 192 px (à 150 % : 256 px tel quel)
                        src = src.zoom(3).subsample(4)
                    w, h = src.width(), src.height()
                    out = self.tk.PhotoImage(width=w, height=h)
                    # « gravé » dans la terre : le fond blanc du logo disparaît, seuls les traits foncés
                    # (ancre, lettres) ressortent, un peu plus clairs que la terre
                    fr, fg, fb = _hex("#3b3f5c")             # couleur de la terre
                    tr, tg, tb = _hex("#6a71a0")             # couleur des traits
                    lignes, transp = [], []
                    for y in range(h):
                        lig = []
                        for x in range(w):
                            if src.transparency_get(x, y):
                                lig.append("#3b3f5c")
                                transp.append((x, y))
                                continue
                            r, g, b = src.get(x, y)[:3]
                            a = max(0.0, min(1.0, (200 - (0.3 * r + 0.59 * g + 0.11 * b)) / 140))   # foncé -> 1
                            if a <= 0.02:
                                transp.append((x, y))
                            lig.append("#%02x%02x%02x" % (int(fr + (tr - fr) * a), int(fg + (tg - fg) * a),
                                                          int(fb + (tb - fb) * a)))
                        lignes.append("{" + " ".join(lig) + "}")
                    out.put(" ".join(lignes))
                    for x, y in transp:                      # fond du logo : on voit la terre (et les frontières)
                        out.transparency_set(x, y, True)
                    self._logo_fil = out
                    return out
                except Exception:                            # noqa: BLE001
                    continue
        return None

    def _filigrane(self):
        cv = self.cv
        self._fil_zone = None
        x, y = self.xy(*self.FILIGRANE)
        logo = self._logo_filigrane()
        if logo is not None:
            cv.create_image(x, y, image=logo, anchor="center")     # le logo porte déjà « A.C.I.S MARINE »
            r = logo.width() / 2
            self._fil_zone = (x, y, r)               # pas de symboles météo sur le logo (c'est la terre)
            return
        # pas de fichier logo : sigle écrit
        R = S(82)
        cv.create_oval(x - R, y - R, x + R, y + R, outline="#4c5276", width=5)
        cv.create_oval(x - R + 9, y - R + 9, x + R - 9, y + R - 9, outline="#464b6e", width=2)
        cv.create_text(x, y - 10, text="A.C.I.S", fill="#555b80", font=("Segoe UI", 32, "bold"))
        cv.create_text(x, y + 32, text="MARINE", fill="#4f557a", font=("Segoe UI", 17, "bold"))

    def _dessiner_lune(self):
        """Lune dans la bande bleue (à gauche de « ACIS MétéoNav »), dessinée selon sa phase à l'heure affichée :
        croissant, quartier, gibbeuse ou ronde."""
        cv = self.cv_lune
        cv.delete("all")
        frac, ph = phase_lune(self.d.date(self.k).timestamp())
        R = S(14)
        cx, cy = S(17), S(17)
        cv.create_oval(cx - R, cy - R, cx + R, cy + R, fill="#2b3150", outline="#6b74a3")   # partie sombre
        s = 1 if ph < 0.5 else -1                   # croissante : éclairée à droite (vue de Tunisie)
        k = 1 - 2 * frac                            # bord de l'ombre : 1 = nouvelle, 0 = quartier, -1 = pleine
        pts = []
        for q in range(0, 37):                      # bord éclairé, du haut vers le bas
            a = math.radians(-90 + q * 5)
            pts += (cx + s * R * math.cos(a), cy + R * math.sin(a))
        for q in range(36, -1, -1):                 # ligne d'ombre (demi-ellipse), du bas vers le haut
            a = math.radians(-90 + q * 5)
            pts += (cx + s * k * R * math.cos(a), cy + R * math.sin(a))
        if frac > 0.01:
            cv.create_polygon(pts, fill="#f4efc8", outline="")

    def _survol_lune(self, dessus: bool):
        self.lune_survol = dessus
        self._ecrire_fiche()

    def _prochaines_phases(self, t: dt.datetime) -> tuple:
        """(prochaine pleine lune, prochaine nouvelle lune) après t, à l'heure près (gardé en mémoire)."""
        cle = t.strftime("%Y%m%d%H")
        if cle in self._lune_cache:
            return self._lune_cache[cle]
        pleine = nouvelle = None
        prec = phase_lune(t.timestamp())[1]
        for h in range(1, 31 * 24):
            u = t + dt.timedelta(hours=h)
            p = phase_lune(u.timestamp())[1]
            if pleine is None and prec < 0.5 <= p:
                pleine = u
            if nouvelle is None and prec > 0.8 and p < 0.2:
                nouvelle = u
            if pleine and nouvelle:
                break
            prec = p
        self._lune_cache[cle] = (pleine, nouvelle)
        return pleine, nouvelle

    def _fiche_lune(self, f) -> None:
        """Fiche de la lune (survol de la lune dans la bande bleue), comme la fiche d'un point de la carte."""
        t = self.d.date(self.k)
        loc = t + TUNISIE
        frac, ph = phase_lune(t.timestamp())
        tm, tm2 = type_maree(ph)
        la, lo = self.vue["lat"], self.vue["lon"]            # centre de la carte affichée
        debut = dt.datetime(loc.year, loc.month, loc.day, tzinfo=dt.timezone.utc) - TUNISIE
        lev, cou = lever_coucher(lune_pos, None, debut.timestamp(), la, lo)
        pleine, nouvelle = self._prochaines_phases(t)

        def jour(u):
            if u is None:
                return "—"
            u = u + TUNISIE
            return f"{JOURS[u.weekday()]} {u:%d/%m} vers {u:%H}h"
        f.insert("end", "Lune\n", "t")
        f.insert("end", f"{JOURS[loc.weekday()]} {loc:%d/%m} à {loc:%H}h\n", "d")
        f.insert("end", "Phase : ", ("b", "s"))
        f.insert("end", f"{nom_lune(frac, ph)}\n", "s")
        f.insert("end", "Éclairée : ", "b")
        f.insert("end", f"{round(frac * 100)} %\n")
        f.insert("end", "Âge : ", "b")
        f.insert("end", f"{ph * 29.53:.1f} jours\n".replace(".", ","))
        f.insert("end", "Lever : ", ("b", "s"))
        f.insert("end", f"{hm(lev)}   ", "s")
        f.insert("end", "Coucher : ", ("b", "s"))
        f.insert("end", f"{hm(cou)}\n", "s")
        f.insert("end", "(au centre de la carte)\n", "g")
        f.insert("end", "Marée : ", ("b", "s"))
        f.insert("end", f"{tm}, {tm2}\n", "s")
        f.insert("end", "Pleine lune : ", ("b", "s"))
        f.insert("end", jour(pleine) + "\n", "s")
        f.insert("end", "Nouvelle lune : ", "b")
        f.insert("end", jour(nouvelle) + "\n")

    def _etiquette(self, x, y, txt, couleur="#ffffff"):
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            self.cv.create_text(x + dx, y + dy, text=txt, fill="#0a0e28", font=("Segoe UI", 8, "bold"))
        self.cv.create_text(x, y, text=txt, fill=couleur, font=("Segoe UI", 8, "bold"))

    def _barbule(self, x, y, de, kt):
        cv = self.cv
        a = math.radians(de)
        ux, uy = math.sin(a), -math.cos(a)
        L, B, E = 24 * ECH, 10 * ECH, 4.2 * ECH
        px, py = -uy, ux
        fx, fy = math.cos(math.pi / 6), math.sin(math.pi / 6)
        bx, by = px * fx + ux * fy, py * fx + uy * fy
        if kt < 2.5:
            cv.create_oval(x - S(4), y - S(4), x + S(4), y + S(4), outline="#121a45", width=1.6 * ECH)
            return
        traits, tri = [(x, y, x + ux * L, y + uy * L)], []
        reste, d = int(round(kt / 5) * 5), 0.0

        def pied(t):
            return x + ux * (L - t), y + uy * (L - t)
        while reste >= 50:
            ax, ay = pied(d)
            cx, cy = pied(d + 6)
            tri.append((ax, ay, ax + bx * B, ay + by * B, cx, cy))
            d += 8
            reste -= 50
        while reste >= 10:
            ax, ay = pied(d)
            traits.append((ax, ay, ax + bx * B, ay + by * B))
            d += E
            reste -= 10
        if reste >= 5:
            if d == 0:
                d = E
            ax, ay = pied(d)
            traits.append((ax, ay, ax + bx * B / 2, ay + by * B / 2))
        for lw, c in ((3.6, "#ffffff"), (1.6, "#121a45")):
            for t in traits:
                cv.create_line(*t, width=lw * ECH, fill=c, capstyle="round")
        for t in tri:
            cv.create_polygon(*t, fill="#121a45", outline="#ffffff")
        cv.create_oval(x - 1.6 * ECH, y - 1.6 * ECH, x + 1.6 * ECH, y + 1.6 * ECH, fill="#121a45", outline="")

    def _fleche(self, x, y, vers, taille, epais: float = 1.0):
        a = math.radians(vers)
        dx, dy = math.sin(a), -math.cos(a)
        x1, y1, x2, y2 = x - dx * taille / 2, y - dy * taille / 2, x + dx * taille / 2, y + dy * taille / 2
        pointe = S(6) * epais
        for lw, c in ((3.4 * ECH * epais, "#ffffff"), (1.6 * ECH * epais, "#121a45")):
            self.cv.create_line(x1, y1, x2, y2, width=lw, fill=c, capstyle="round")
            self.cv.create_line(x2 - pointe * math.sin(a - .5), y2 + pointe * math.cos(a - .5), x2, y2,
                                x2 - pointe * math.sin(a + .5), y2 + pointe * math.cos(a + .5), width=lw, fill=c,
                                capstyle="round", joinstyle="round")

    # couches dont la couleur peut être montrée sur la mer, sur la terre ou partout
    ZONES_COUCHE = ("vent", "rafales", "pluie", "nuages")
    ZONE_DEFAUT = {"vent": "mer", "rafales": "mer", "pluie": "partout", "nuages": "partout"}

    def zone_de(self, couche: str) -> str:
        """Où la couche est montrée : « mer », « terre » ou « partout » (vagues, courant, marée : la mer)."""
        if couche not in self.ZONES_COUCHE:
            return "mer"
        return self._zones.get(couche, self.ZONE_DEFAUT[couche])

    def _dans_zone(self, zone: str, la: float, lo: float) -> bool:
        if zone == "partout":
            return True
        mer = self.d.est_mer(la, lo)
        return mer if zone == "mer" else not mer

    def _fleche_courant(self, x, y, la, lo, chiffres):
        """Flèche du courant (plus grande qu'avant), avec sa vitesse en nœuds."""
        if not self.d.est_mer(la, lo):
            return
        c = self.d.courant(self.k, la, lo)
        if not c or c[0] < 0.05:
            return
        self._fleche(x, y, c[1], S(18 + min(c[0], 2.5) * 12), epais=1.35)
        if chiffres:
            a = math.radians(c[1])
            self._etiquette(x + math.cos(a) * S(18), y + math.sin(a) * S(18), f"{c[0]:.1f}", "#bff3ff")

    def _symboles(self, couche):
        """UNE seule chose à la fois : la couche choisie (barbules, flèches ou valeurs), dans sa zone.
        En plus, si demandé : les flèches du courant (décalées d'une demi-maille pour ne rien cacher)."""
        p = self._ppd()
        pas_deg = next((v for v in (1 / 16, 1 / 8, 1 / 4, 1 / 2, 1, 2) if v * p >= S(58)), 2)
        cos = math.cos(math.radians(35.6))
        pas_la = pas_deg * cos
        la_h, lo_g = self.geo(-20, -20)
        la_b, lo_d = self.geo(self.W + 20, self.H + 20)
        chiffres = self.chiffres.get()
        zone = self.zone_de(couche)
        fz = getattr(self, "_fil_zone", None)
        avec_courant = couche != "courant" and self.avec_courant.get() and bool(self.d.courants)
        for decale in ((0.0, 0.0),) + (((0.5, 0.5),) if avec_courant else ()):
            la = math.ceil(la_b / pas_la) * pas_la + decale[0] * pas_la
            while la <= la_h:
                lo = math.ceil(lo_g / pas_deg) * pas_deg + decale[1] * pas_deg
                while lo <= lo_d:
                    x, y = self.xy(la, lo)
                    lo_pt = lo
                    lo += pas_deg
                    if x < S(16) or x > self.W - S(12) or y < S(14) or y > self.H - S(8):
                        continue
                    if fz and (x - fz[0]) ** 2 + (y - fz[1]) ** 2 < (fz[2] + 16) ** 2:
                        continue                     # laisse le logo A.C.I.S propre
                    if decale != (0.0, 0.0):         # 2e passage : seulement les flèches du courant
                        self._fleche_courant(x, y, la, lo_pt, chiffres)
                        continue
                    if couche == "carte":
                        continue
                    if couche in ("vent", "rafales"):
                        if not self._dans_zone(zone, la, lo_pt):
                            continue
                        w = self.d.vent(self.k, la, lo_pt)
                        if not w:
                            continue
                        kt, de = w
                        if couche == "rafales":
                            g = self.d.grille("rafales", self.k)
                            r = g.en(la, lo_pt) if g else None
                            if r is not None:
                                kt = r
                        self._barbule(x, y, de, kt)
                        if chiffres:
                            a = math.radians(de)
                            m = self.marge(self.k, kt)
                            txt = f"{kt:.0f}" + (f" (+{m})" if m else "")
                            self._etiquette(x - math.sin(a) * S(12), y + math.cos(a) * S(12), txt,
                                            "#ffd166" if m else "#ffffff")
                    elif couche in ("pluie", "nuages"):      # seulement la valeur (pas de vent par-dessus)
                        if not chiffres or not self._dans_zone(zone, la, lo_pt):
                            continue
                        g = self.d.grille(couche, self.k)
                        v = g.en(la, lo_pt) if g else None
                        if v is None:
                            continue
                        if couche == "pluie":
                            if v < 0.1:
                                continue
                            txt = f"{v:.1f}" if v < 10 else f"{v:.0f}"
                        else:
                            txt = f"{v:.0f}"
                        self._etiquette(x, y, txt)
                    elif couche == "courant":
                        self._fleche_courant(x, y, la, lo_pt, chiffres)
                    elif couche == "maree":                  # seulement la hauteur d'eau (m)
                        if not chiffres or not self.d.est_mer(la, lo_pt):
                            continue
                        g = self.d.grille("maree", self.k)
                        v = g.en(la, lo_pt) if g else None
                        if v is not None:
                            self._etiquette(x, y, f"{v:+.2f}")
                    else:                                    # vagues
                        if not self.d.est_mer(la, lo_pt):
                            continue
                        g = self.d.grille("vagues", self.k)
                        h = g.en(la, lo_pt) if g else None
                        d = self.d.direction_vagues(self.k, la, lo_pt)
                        if h is None or d is None:
                            continue
                        self._fleche(x, y, (d + 180) % 360, S(14 + min(h, 4) * 4))
                        if chiffres:
                            a = math.radians(d)
                            self._etiquette(x + math.cos(a) * S(16), y + math.sin(a) * S(16), f"{h:.1f}")
                la += pas_la

    def _villes(self):
        zones = []
        for nom, la, lo in VILLES:
            x, y = self.xy(la, lo)
            if x < 0 or y < 0 or x > self.W or y > self.H:
                continue
            b = (x - S(4 * len(nom) + 4), y - S(22), x + S(4 * len(nom) + 4), y + S(4))
            if any(b[0] < d and b[2] > a and b[1] < f and b[3] > c for a, c, d, f in zones):
                continue
            zones.append(b)
            self.cv.create_oval(x - S(3.5), y - S(3.5), x + S(3.5), y + S(3.5), fill="#ffffff", outline="#000000")
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                self.cv.create_text(x + dx, y - S(12) + dy, text=nom, fill="#000000", font=("Segoe UI", 10, "bold"))
            self.cv.create_text(x, y - S(12), text=nom, fill="#ffffff", font=("Segoe UI", 10, "bold"))

    def _entete(self, couche):
        if self.d.vide:
            self.lbl_date.config(text="Pas encore de météo")
            self.lbl_info.config(text="Pas encore de météo — cliquez sur « Télécharger »" if self.hote
                                 else "Aucun fichier météo", fg="#ffd166")
            return
        t = self.d.date(self.k) + TUNISIE
        self.lbl_date.config(text=f"{JOURS[t.weekday()]} {t:%d/%m %H}h (Tunisie)")
        h = self.maintenant()
        age = (h - self.d.run).total_seconds() / 3600 if h else -1
        run = (self.d.run + TUNISIE).strftime("%d/%m %Hh")
        lead = self.d.echeances[self.k]
        txt = f"Prévision NOAA du {run} (Tunisie) · échéance +{lead:.0f} h"
        if 0 <= age < 100:
            txt += f" · reçue il y a {age:.0f} h"
        self.lbl_info.config(text=txt + (" · ANCIENNE : retéléchargez" if age >= 18 else ""),
                             fg="#ff8a80" if age >= 18 else "#7CFC9A")
        m = self.marges.get(int(lead // 24), 0)
        if self.marges:
            self.texte_marge = f"Marge de sécurité sur les vents forts ({'+' + str(m) + ' nd' if m else 'aucune'} à ce jour)"
        else:
            self.texte_marge = "Marge de sécurité sur les vents forts (contrôle en cours)"
        try:
            if self.cb_marge is not None and self.cb_marge.winfo_exists():
                self.cb_marge.config(text=self.texte_marge)
        except Exception:                            # noqa: BLE001
            pass

    def _legende(self, couche):
        lg = self.legende
        lg.delete("all")
        if couche == "carte":
            lg.create_text(S(60), S(15), text="Carte seule : aucune donnée météo affichée"
                           + (" (flèches du courant en plus)" if self.avec_courant.get() and self.d.courants else ""),
                           anchor="w", font=("Segoe UI", 9, "bold"), fill=BLEU)
            return
        lib, ech, unite, grad, reg = COUCHES[couche]
        table, indice = palette(ech, reg)
        larg = max(S(200), lg.winfo_width() - S(120))
        x0 = S(60)
        lg.create_text(x0 - S(6), S(10), text=lib, anchor="e", font=("Segoe UI", 9, "bold"), fill=BLEU)
        n = 128
        for q in range(n):
            lg.create_rectangle(x0 + q * larg / n, S(2), x0 + (q + 1) * larg / n + 1, S(14), fill=table[q * 2],
                                outline="")
        vmax = grad[-1]
        for v in grad:
            if reg:
                pos = indice(v) / 255
            else:
                pos = v / vmax
            lg.create_text(x0 + pos * larg, S(22), text=f"{v:g}", font=("Segoe UI", 8), fill="#333")
        lg.create_text(x0 + larg + S(8), S(10), text=unite, anchor="w", font=("Segoe UI", 9), fill="#333")

    def _fiche_maree_astres(self, f, la: float, lo: float) -> None:
        """Marée (hauteur, PM/BM du jour, courbe) puis soleil et lune du jour — comme la carte téléphone."""
        t = self.d.date(self.k)
        loc = t + TUNISIE
        debut = dt.datetime(loc.year, loc.month, loc.day, tzinfo=dt.timezone.utc) - TUNISIE   # 0 h en Tunisie
        frac, ph = phase_lune(t.timestamp())
        tm, tm2 = type_maree(ph)
        serie = self.d.serie_maree(la, lo) if self.d.est_mer(la, lo) else None
        if serie:
            hn, h0 = self.d.heure_maree(t), self.d.heure_maree(debut)
            ici = None
            if 0 <= hn <= len(serie) - 1:
                i = math.floor(hn)
                j = min(i + 1, len(serie) - 1)
                ici = serie[i] + (serie[j] - serie[i]) * (hn - i)
            if ici is None or math.isnan(ici):
                f.insert("end", "Marée : ", ("b", "s"))
                f.insert("end", "hors de la période prévue\n", "g")
            else:
                monte = serie[j] >= serie[i] if j > i else serie[i] >= serie[max(0, i - 1)]
                ex = extremes(serie, h0, h0 + 24)
                amp = (max(e[1] for e in ex) - min(e[1] for e in ex)) if ex else 0
                f.insert("end", "Marée : ", ("b", "s"))
                f.insert("end", f"{signe(ici)} m {'↑ monte' if monte else '↓ baisse'}"
                         + (f" · marnage {amp:.2f} m".replace(".", ",") if amp >= 0.06 else "") + "\n", "s")
                f.insert("end", f"{tm}, {tm2}\n", "d")
                t0m = self.d.maree_t0.timestamp()
                if ex and amp >= 0.06:
                    morceaux = [f"{'PM' if e[2] else 'BM'} {hm(t0m + e[0] * 3600)} {signe(e[1])}" for e in ex]
                    for q in range(0, len(morceaux), 2):                 # deux par ligne
                        f.insert("end", "  ·  ".join(morceaux[q:q + 2]) + "\n", "g")
                else:
                    f.insert("end", "marée très faible ici (moins de 6 cm)\n", "g")
        else:
            f.insert("end", "Marée : ", ("b", "s"))
            f.insert("end", f"{tm}, {tm2}\n", ("d", "s"))
        so = lever_coucher(soleil_pos, -0.833, debut.timestamp(), la, lo)
        lu = lever_coucher(lune_pos, None, debut.timestamp(), la, lo)
        f.insert("end", "Soleil : ", ("b", "s"))
        f.insert("end", f"{hm(so[0])} – {hm(so[1])}\n", "s")
        f.insert("end", "Lune : ", "b")
        court = {"gibbeuse croissante": "gibbeuse cr.", "gibbeuse décroissante": "gibbeuse décr.",
                 "dernier croissant": "dern. croissant", "premier quartier": "1er quartier",
                 "dernier quartier": "dern. quartier"}
        nl = nom_lune(frac, ph)
        f.insert("end", f"{court.get(nl, nl)} {round(frac * 100)} % ")
        f.insert("end", f"↑{hm(lu[0])} ↓{hm(lu[1])}\n", "d")

    def _courbe_maree(self, f, serie: list, h0: float, hn: float) -> None:
        """Petite courbe de la marée du jour (0 h – 24 h), trait jaune = heure affichée."""
        tk = self.tk
        Wc, Hc = 236, 40
        cv = tk.Canvas(f, width=Wc, height=Hc, bg="#eef0f6", highlightthickness=0)
        vals = []
        for x in range(25):
            k = round(h0 + x)
            v = serie[k] if 0 <= k < len(serie) else float("nan")
            vals.append(v)
        vmax = max([0.1] + [abs(v) for v in vals if not math.isnan(v)])
        cv.create_line(0, Hc / 2, Wc, Hc / 2, fill="#b6bac8", dash=(3, 3))
        pts = []
        for x, v in enumerate(vals):
            if not math.isnan(v):
                pts += (x / 24 * Wc, Hc / 2 - v / vmax * (Hc / 2 - 3))
        if len(pts) >= 4:
            cv.create_line(*pts, fill="#2f6bd8", width=2, smooth=True)
        xn = (hn - h0) / 24 * Wc
        if 0 <= xn <= Wc:
            cv.create_line(xn, 0, xn, Hc, fill="#f6b400", width=2)
        for hh in (6, 12, 18):
            cv.create_text(hh / 24 * Wc, Hc - 2, text=f"{hh}h", anchor="s", fill="#6b7290", font=("Segoe UI", 6))
        self._courbe = cv
        f.window_create("end", window=cv)
        f.insert("end", "\n")

    def _ecrire_fiche(self):
        f = self.fiche
        if getattr(self, "_courbe", None) is not None:
            try:
                self._courbe.destroy()
            except Exception:                        # noqa: BLE001
                pass
            self._courbe = None
        f.config(state="normal")
        f.delete("1.0", "end")
        if getattr(self, "lune_survol", False):
            self._fiche_lune(f)
            f.config(state="disabled")
            return
        t = self.d.date(self.k) + TUNISIE
        point = self.survol or self.repere
        if point:
            la, lo = point
            f.insert("end", position(la, lo) + "\n", "b")
            run = self.d.run + TUNISIE
            if not self.d.vide:
                f.insert("end", f"Prévue pour {JOURS[t.weekday()]} {t:%d/%m} {t:%H}h · fichier NOAA du "
                                f"{run:%d/%m} {run:%H}h\n", "d")
            v = self.d.fiche(self.k, la, lo)
            if "vent" in v:
                kt, de = v["vent"]
                f.insert("end", "Vent : ", ("b", "s"))
                f.insert("end", f"{kt:.0f} nds du {cardinal(de)} ({de:.0f}°)"
                         + (f" · rafales {v['rafales']:.0f}" if "rafales" in v else "") + "\n", "s")
                m = self.marge(self.k, max(kt, v.get("rafales", 0)))
                if m:
                    f.insert("end", f"  Marge de sécurité : +{m} nds → jusqu'à {kt + m:.0f} nds\n", "m")
            if "vagues" in v:
                f.insert("end", "Vagues : ", "b")
                txt = f"{v['vagues']:.1f} m"
                if "periode" in v:
                    txt += f", {v['periode']:.0f} s"
                if "dir_vagues" in v:
                    txt += f", du {cardinal(v['dir_vagues'])}"
                f.insert("end", txt + "\n")
            if "courant" in v:
                f.insert("end", "Courant : ", "b")
                f.insert("end", f"{v['courant'][0]:.1f} nds vers le {cardinal(v['courant'][1])}\n")
            if "pluie" in v or "nuages" in v:
                f.insert("end", "Pluie : ", "b")
                txt = ("aucune" if v.get("pluie", 0) < 0.1 else f"{v['pluie']:.1f} mm/h") if "pluie" in v else "—"
                if "nuages" in v:
                    txt += f" · nuages {v['nuages']:.0f} %"
                f.insert("end", txt + "\n")
            if not v:
                f.insert("end", "Pas encore de météo : cliquez sur « Télécharger ».\n" if self.d.vide
                         else "Hors de la zone téléchargée.\n", "s")
            self._fiche_maree_astres(f, la, lo)
        pe = (self.precision or {}).get("par_echeance") or {}
        s = pe.get(f"J+{int(self.d.echeances[self.k] // 24)}")
        if s and s.get("n", 0) >= 100:
            f.insert("end", "Précision mesurée à cette échéance\n", ("b", "s"))
            f.insert("end", f"écart moyen {s['ecart_moyen_kt']} nds, {s['pct_a_5kt']} % à ±5 nds "
                            f"({self.precision.get('periode_jours', 30)} derniers jours, 7 aéroports côtiers)\n", "g")
        f.config(state="disabled")


def ouvrir(chemin: str, parent=None, version: str = "", maintenant=None, hote: dict = None):
    """Ouvre la carte (fenêtre secondaire si parent est donné). Renvoie la Visionneuse.
    maintenant : heure Internet (datetime UTC) connue du programme, sinon None.
    hote : liens avec le programme principal (voir Visionneuse) ; la carte est alors sa fenêtre principale."""
    return Visionneuse(chemin, parent=parent, version=version, maintenant=maintenant, hote=hote)


def principal() -> int:
    if len(sys.argv) < 2:
        print("Usage : python visionneuse.py fichier.grb2")
        return 1
    v = ouvrir(sys.argv[1])
    v.win.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(principal())
