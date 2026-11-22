"""
Carte A.C.I.S — visionneuse météo du programme PC (Météo Marine Tunisie).

Affiche le fichier GRIB2 téléchargé, comme XyGrib, mais à la marque A.C.I.S et sans rien installer :
Python + tkinter seulement (Windows 7 à 11, aucun navigateur nécessaire).
  - couches : vent, rafales, vagues, courant, pluie, nuages (mêmes couleurs que l'appli téléphone) ;
  - barbules de vent et flèches (vagues, courant) avec leur valeur écrite à côté ;
  - MARGE DE SÉCURITÉ : quand le robot « Précision météo » a prouvé (30 jours de relevés réels)
    que les vents forts sont sous-estimés à une échéance, la valeur est suivie de « (+N) » ;
  - curseur de temps (heure de Tunisie), animation, zoom molette, déplacement à la souris,
    fiche complète du point cliqué.
Le fichier GRIB n'est jamais modifié : XyGrib et la carte montrent la même prévision NOAA.

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

COUCHES = {   # nom : (libellé, échelle, unité, graduations de la légende, échelle « régulière » ?)
    "vent": ("Vent", ECH_VENT, "nds", [0, 5, 10, 15, 20, 25, 30, 35, 40, 50], False),
    "rafales": ("Rafales", ECH_VENT, "nds", [0, 5, 10, 15, 20, 25, 30, 35, 40, 50], False),
    "vagues": ("Vagues", ECH_VAGUES, "m", [0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 6], False),
    "courant": ("Courant", ECH_COURANT, "nds", [0, 0.25, 0.5, 0.75, 1, 1.5, 2, 2.5, 3], False),
    "pluie": ("Pluie", ECH_PLUIE, "mm/h", [0, 0.1, 0.5, 1, 2, 5, 10, 20, 50], True),
    "nuages": ("Nuages", ECH_NUAGES, "%", [0, 25, 50, 75, 100], False),
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
    """Degrés + minutes décimales (format GPS marin)."""
    a = abs(v)
    d = int(a)
    return f"{d}°{(a - d) * 60:04.1f}' {pos if v >= 0 else neg}"


def cardinal(deg: float) -> str:
    noms = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]
    return noms[int((deg % 360) / 22.5 + 0.5) % 16]


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


class Donnees:
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


# ------------------------------------------------------------------ fenêtre
class Visionneuse:
    def __init__(self, chemin: str, parent=None, titre_extra: str = "", version: str = "", maintenant=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.d = Donnees(chemin)
        self.terres, self.frontieres = lire_cotes()
        self.precision, self.marges = {}, {}
        self.version = version
        # heure Internet du programme (l'horloge du PC client est souvent reculée avant 2025) ;
        # à défaut, l'horloge du PC si elle est plausible (après le run du fichier)
        pc = dt.datetime.now(dt.timezone.utc)
        self.t0_heure, self.t0_pc = (maintenant, time.time()) if maintenant else \
            ((pc, time.time()) if pc >= self.d.run - dt.timedelta(hours=1) else (None, None))
        self.win = tk.Toplevel(parent) if parent is not None else tk.Tk()
        w = self.win
        w.title(f"Carte A.C.I.S — Météo Marine Tunisie{titre_extra}")
        sl, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        L, H = min(1100, sl - 40), min(720, sh - 90)
        w.geometry(f"{L}x{H}+{max(0, (sl - L) // 2)}+{max(0, (sh - 60 - H) // 2)}")
        w.minsize(640, 460)
        self.couche = tk.StringVar(value="vent")
        self.chiffres = tk.BooleanVar(value=True)
        self.marge_on = tk.BooleanVar(value=True)
        self.k = 0
        self.lecture = None
        self.repere = None
        self.vue = {"lat": 35.6, "lon": 10.8, "z": 1.0}
        self.W, self.H = 800, 500
        self._img = None
        self._img_cle = None
        self._drag = None

        haut = tk.Frame(w, bg=BLEU)
        haut.pack(side="top", fill="x")
        tk.Label(haut, text="A.C.I.S MARINE", bg=BLEU, fg="white", font=("Segoe UI", 12, "bold")).pack(side="left", padx=(8, 2))
        tk.Label(haut, text="Carte météo", bg=BLEU, fg="#c9d2ff", font=("Segoe UI", 9)).pack(side="left", padx=(0, 10))
        for nom, (lib, *_r) in COUCHES.items():
            if nom == "rafales" and not any("GUST" in self.d.par[e] for e in self.d.echeances[:3]):
                continue
            if nom in ("pluie", "nuages") and not any(("PRATE" if nom == "pluie" else "TCDC") in self.d.par[e]
                                                       for e in self.d.echeances[:6]):
                continue
            if nom == "courant" and not self.d.courants:
                continue
            tk.Radiobutton(haut, text=lib, value=nom, variable=self.couche, indicatoron=False, command=self.dessiner,
                           bg="#2c3a85", fg="white", selectcolor="#f6b400", activebackground="#3b4aa0",
                           font=("Segoe UI", 9, "bold"), padx=8, pady=2, relief="flat",
                           offrelief="flat").pack(side="left", padx=1, pady=4)
        tk.Checkbutton(haut, text="Valeurs", variable=self.chiffres, command=self.dessiner, bg=BLEU, fg="white",
                       selectcolor=BLEU, activebackground=BLEU, font=("Segoe UI", 9)).pack(side="left", padx=(10, 0))
        self.cb_marge = tk.Checkbutton(haut, text="Marge de sécurité", variable=self.marge_on, command=self.dessiner,
                                       bg=BLEU, fg="white", selectcolor=BLEU, activebackground=BLEU,
                                       font=("Segoe UI", 9))
        self.cb_marge.pack(side="left")
        self.zone = ttk.Combobox(haut, values=[z[0] for z in ZONES], state="readonly", width=26)
        self.zone.set(ZONES[0][0])
        self.zone.pack(side="right", padx=6)
        self.zone.bind("<<ComboboxSelected>>", self._zone)

        bas = tk.Frame(w, bg="#eef0f6")
        bas.pack(side="bottom", fill="x")
        ligne = tk.Frame(bas, bg="#eef0f6")
        ligne.pack(fill="x", padx=6, pady=(4, 0))
        self.btn_lect = tk.Button(ligne, text="▶", width=3, command=self.basculer_lecture, bg=BLEU, fg="white",
                                  relief="flat", font=("Segoe UI", 10, "bold"))
        self.btn_lect.pack(side="left")
        tk.Button(ligne, text="◀", width=2, command=lambda: self.aller(self.k - 1), relief="flat").pack(side="left", padx=(4, 0))
        tk.Button(ligne, text="▶|", width=2, command=lambda: self.aller(self.k + 1), relief="flat").pack(side="left")
        self.lbl_date = tk.Label(ligne, text="", font=("Segoe UI", 11, "bold"), fg=BLEU, bg="#eef0f6", width=24, anchor="w")
        self.lbl_date.pack(side="left", padx=8)
        self.curseur = tk.Scale(ligne, from_=0, to=max(0, len(self.d.echeances) - 1), orient="horizontal",
                                showvalue=False, command=lambda v: self.aller(int(float(v)), depuis_curseur=True),
                                bg="#eef0f6", highlightthickness=0, troughcolor="#c9cfe3", sliderlength=18)
        self.curseur.pack(side="left", fill="x", expand=True)
        self.legende = tk.Canvas(bas, height=30, bg="#eef0f6", highlightthickness=0)
        self.legende.pack(fill="x", padx=6)
        pied = tk.Frame(bas, bg=BLEU)
        pied.pack(fill="x")
        self.lbl_info = tk.Label(pied, text="", bg=BLEU, fg="#7CFC9A", font=("Segoe UI", 9, "bold"))
        self.lbl_info.pack(side="left", padx=8, pady=3)
        tk.Label(pied, text=f"Météo Marine Tunisie · {version or 'v1.2026'} · créé par Claude @ A.C.I.S",
                 bg=BLEU, fg="#c9d2ff", font=("Segoe UI", 8, "italic")).pack(side="right", padx=8)

        centre = tk.Frame(w)
        centre.pack(fill="both", expand=True)
        self.fiche = tk.Text(centre, width=34, font=("Segoe UI", 9), bg="#f7f8fc", relief="flat", wrap="word",
                             padx=8, pady=6, cursor="arrow")
        self.fiche.pack(side="right", fill="y")
        self.fiche.tag_configure("t", font=("Segoe UI", 11, "bold"), foreground=BLEU)
        self.fiche.tag_configure("b", font=("Segoe UI", 9, "bold"))
        self.fiche.tag_configure("m", font=("Segoe UI", 9, "bold"), foreground="#c0392b")
        self.fiche.tag_configure("g", foreground="#6b7290", font=("Segoe UI", 8))
        self.cv = tk.Canvas(centre, bg="#1a2046", highlightthickness=0, cursor="crosshair")
        self.cv.pack(side="left", fill="both", expand=True)
        self.cv.bind("<Configure>", self._taille)
        self.cv.bind("<ButtonPress-1>", self._presse)
        self.cv.bind("<B1-Motion>", self._glisse)
        self.cv.bind("<ButtonRelease-1>", self._lache)
        self.cv.bind("<MouseWheel>", self._molette)                  # Windows
        self.cv.bind("<Button-4>", lambda e: self._zoomer(1.25, e.x, e.y))   # Linux
        self.cv.bind("<Button-5>", lambda e: self._zoomer(0.8, e.x, e.y))
        w.bind("<Left>", lambda e: self.aller(self.k - 1))
        w.bind("<Right>", lambda e: self.aller(self.k + 1))
        w.bind("<space>", lambda e: self.basculer_lecture())

        self.k = self._echeance_maintenant()
        self.curseur.set(self.k)
        self._ecrire_fiche()
        self._recu = []                            # rempli par le fil de téléchargement, lu par la fenêtre
        threading.Thread(target=lambda: self._recu.append(charger_precision()), daemon=True).start()
        self.win.after(300, self._attendre_precision)

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

    def basculer_lecture(self):
        if self.lecture:
            self.win.after_cancel(self.lecture)
            self.lecture = None
            self.btn_lect.config(text="▶")
            return
        self.btn_lect.config(text="■")

        def pas():
            self.aller(self.k + 1 if self.k < len(self.d.echeances) - 1 else 0)
            self.lecture = self.win.after(700, pas)
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
    def _image(self, couche: str):
        """Image des couleurs (1 pixel calculé tous les PAS pixels, puis agrandie)."""
        tk = self.tk
        PAS = 4
        g = self.d.grille(couche, self.k)
        if g is None:
            return None
        cle = (couche, self.k, self.W, self.H, round(self.vue["lat"], 5), round(self.vue["lon"], 5), round(self.vue["z"], 5))
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
            for c in cols:
                if c is None:
                    ligne.append(fond)
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
        if self.d.grille(couche, self.k) is None and couche != "vent":
            couche = "vent"
        img = self._image(couche) if not rapide or self._img is not None else None
        if rapide and self._img is not None:
            img = None                               # pendant un déplacement : pas de recalcul, carte seule
        if img is not None:
            cv.create_image(0, 0, image=img, anchor="nw")
        # terres : remplies (vent, vagues, courant) ; contour seul pour la pluie et les nuages (visibles sur terre)
        remplir = couche not in ("pluie", "nuages")
        for poly in self.terres:
            pts = []
            for la, lo in poly:
                x, y = self.xy(la, lo)
                pts += (x, y)
            if len(pts) >= 6:
                cv.create_polygon(pts, fill="#3b3f5c" if remplir else "", outline="#0a0c1e" if remplir else "#aab0d0",
                                  width=1.2)
        for ligne in self.frontieres:
            pts = []
            for la, lo in ligne:
                pts += self.xy(la, lo)
            if len(pts) >= 4:
                cv.create_line(pts, fill="#c8cbe0", dash=(5, 4))
        if not rapide:
            self._symboles(couche)
            self._villes()
        if self.repere:
            x, y = self.xy(*self.repere)
            for lw, c in ((5, "#000000"), (2, "#ffffff")):
                for a, b in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    cv.create_line(x + a * 5, y + b * 5, x + a * 13, y + b * 13, width=lw, fill=c)
        self._entete(couche)
        self._legende(couche)

    def _etiquette(self, x, y, txt, couleur="#ffffff"):
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            self.cv.create_text(x + dx, y + dy, text=txt, fill="#0a0e28", font=("Segoe UI", 8, "bold"))
        self.cv.create_text(x, y, text=txt, fill=couleur, font=("Segoe UI", 8, "bold"))

    def _barbule(self, x, y, de, kt):
        cv = self.cv
        a = math.radians(de)
        ux, uy = math.sin(a), -math.cos(a)
        L, B, E = 24, 10, 4.2
        px, py = -uy, ux
        fx, fy = math.cos(math.pi / 6), math.sin(math.pi / 6)
        bx, by = px * fx + ux * fy, py * fx + uy * fy
        if kt < 2.5:
            cv.create_oval(x - 4, y - 4, x + 4, y + 4, outline="#121a45", width=1.6)
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
                cv.create_line(*t, width=lw, fill=c, capstyle="round")
        for t in tri:
            cv.create_polygon(*t, fill="#121a45", outline="#ffffff")
        cv.create_oval(x - 1.6, y - 1.6, x + 1.6, y + 1.6, fill="#121a45", outline="")

    def _fleche(self, x, y, vers, taille):
        a = math.radians(vers)
        dx, dy = math.sin(a), -math.cos(a)
        x1, y1, x2, y2 = x - dx * taille / 2, y - dy * taille / 2, x + dx * taille / 2, y + dy * taille / 2
        for lw, c in ((3.4, "#ffffff"), (1.6, "#121a45")):
            self.cv.create_line(x1, y1, x2, y2, width=lw, fill=c, capstyle="round")
            self.cv.create_line(x2 - 6 * math.sin(a - .5), y2 + 6 * math.cos(a - .5), x2, y2,
                                x2 - 6 * math.sin(a + .5), y2 + 6 * math.cos(a + .5), width=lw, fill=c,
                                capstyle="round", joinstyle="round")

    def _symboles(self, couche):
        p = self._ppd()
        pas_deg = next((v for v in (1 / 16, 1 / 8, 1 / 4, 1 / 2, 1, 2) if v * p >= 46), 2)
        cos = math.cos(math.radians(35.6))
        pas_la = pas_deg * cos
        la_h, lo_g = self.geo(-20, -20)
        la_b, lo_d = self.geo(self.W + 20, self.H + 20)
        chiffres = self.chiffres.get()
        la = math.ceil(la_b / pas_la) * pas_la
        while la <= la_h:
            lo = math.ceil(lo_g / pas_deg) * pas_deg
            while lo <= lo_d:
                x, y = self.xy(la, lo)
                lo += pas_deg
                if x < 16 or x > self.W - 12 or y < 14 or y > self.H - 8:
                    continue
                lo_pt = lo - pas_deg
                if couche in ("vent", "rafales", "pluie", "nuages"):
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
                        self._etiquette(x - math.sin(a) * 12, y + math.cos(a) * 12, txt,
                                        "#ffd166" if m else "#ffffff")
                elif couche == "courant":
                    if not self.d.est_mer(la, lo_pt):
                        continue
                    c = self.d.courant(self.k, la, lo_pt)
                    if not c or c[0] < 0.05:
                        continue
                    self._fleche(x, y, c[1], 12 + min(c[0], 2.5) * 9)
                    if chiffres:
                        a = math.radians(c[1])
                        self._etiquette(x + math.cos(a) * 16, y + math.sin(a) * 16, f"{c[0]:.1f}")
                else:
                    if not self.d.est_mer(la, lo_pt):
                        continue
                    g = self.d.grille("vagues", self.k)
                    h = g.en(la, lo_pt) if g else None
                    d = self.d.direction_vagues(self.k, la, lo_pt)
                    if h is None or d is None:
                        continue
                    self._fleche(x, y, (d + 180) % 360, 14 + min(h, 4) * 4)
                    if chiffres:
                        a = math.radians(d)
                        self._etiquette(x + math.cos(a) * 16, y + math.sin(a) * 16, f"{h:.1f}")
            la += pas_la

    def _villes(self):
        zones = []
        for nom, la, lo in VILLES:
            x, y = self.xy(la, lo)
            if x < 0 or y < 0 or x > self.W or y > self.H:
                continue
            b = (x - 4 * len(nom) - 4, y - 22, x + 4 * len(nom) + 4, y + 4)
            if any(b[0] < d and b[2] > a and b[1] < f and b[3] > c for a, c, d, f in zones):
                continue
            zones.append(b)
            self.cv.create_oval(x - 3.5, y - 3.5, x + 3.5, y + 3.5, fill="#ffffff", outline="#000000")
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                self.cv.create_text(x + dx, y - 12 + dy, text=nom, fill="#000000", font=("Segoe UI", 10, "bold"))
            self.cv.create_text(x, y - 12, text=nom, fill="#ffffff", font=("Segoe UI", 10, "bold"))

    def _entete(self, couche):
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
            self.cb_marge.config(text=f"Marge de sécurité ({'+' + str(m) + ' nd' if m else 'aucune'} à ce jour)")
        else:
            self.cb_marge.config(text="Marge de sécurité (contrôle en cours)")

    def _legende(self, couche):
        lg = self.legende
        lg.delete("all")
        lib, ech, unite, grad, reg = COUCHES[couche]
        table, indice = palette(ech, reg)
        larg = max(200, lg.winfo_width() - 120)
        x0 = 60
        lg.create_text(x0 - 6, 10, text=lib, anchor="e", font=("Segoe UI", 9, "bold"), fill=BLEU)
        n = 128
        for q in range(n):
            lg.create_rectangle(x0 + q * larg / n, 2, x0 + (q + 1) * larg / n + 1, 14, fill=table[q * 2], outline="")
        vmax = grad[-1]
        for v in grad:
            if reg:
                pos = indice(v) / 255
            else:
                pos = v / vmax
            lg.create_text(x0 + pos * larg, 22, text=f"{v:g}", font=("Segoe UI", 8), fill="#333")
        lg.create_text(x0 + larg + 8, 10, text=unite, anchor="w", font=("Segoe UI", 9), fill="#333")

    def _ecrire_fiche(self):
        f = self.fiche
        f.config(state="normal")
        f.delete("1.0", "end")
        t = self.d.date(self.k) + TUNISIE
        f.insert("end", f"{JOURS[t.weekday()]} {t:%d/%m} à {t:%H}h\n", "t")
        if not self.repere:
            f.insert("end", "\nCliquez sur la carte pour voir la prévision détaillée d'un point.\n\n"
                            "Molette : zoom · glisser : déplacer\n← → : heure précédente / suivante\n"
                            "Espace : animation\n", "")
        else:
            la, lo = self.repere
            f.insert("end", f"{dm(la, 'N', 'S')}   {dm(lo, 'E', 'O')}\n\n", "g")
            v = self.d.fiche(self.k, la, lo)
            if "vent" in v:
                kt, de = v["vent"]
                f.insert("end", "Vent : ", "b")
                f.insert("end", f"{kt:.0f} nds du {cardinal(de)} ({de:.0f}°)\n")
                m = self.marge(self.k, max(kt, v.get("rafales", 0)))
                if m:
                    f.insert("end", f"  Marge de sécurité : +{m} nds → jusqu'à {kt + m:.0f} nds\n", "m")
            if "rafales" in v:
                f.insert("end", "Rafales : ", "b")
                f.insert("end", f"{v['rafales']:.0f} nds\n")
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
            if "pluie" in v:
                f.insert("end", "Pluie : ", "b")
                f.insert("end", ("aucune" if v["pluie"] < 0.1 else f"{v['pluie']:.1f} mm/h") + "\n")
            if "nuages" in v:
                f.insert("end", "Nuages : ", "b")
                f.insert("end", f"{v['nuages']:.0f} %\n")
            if not v:
                f.insert("end", "Hors de la zone téléchargée.\n")
        f.insert("end", "\n")
        pe = (self.precision or {}).get("par_echeance") or {}
        s = pe.get(f"J+{int(self.d.echeances[self.k] // 24)}")
        if s and s.get("n", 0) >= 100:
            f.insert("end", "Précision mesurée à cette échéance\n", "b")
            f.insert("end", f"écart moyen {s['ecart_moyen_kt']} nds, {s['pct_a_5kt']} % à ±5 nds "
                            f"({self.precision.get('periode_jours', 30)} derniers jours, 7 aéroports côtiers)\n", "g")
        else:
            f.insert("end", "Contrôle de la précision en cours : la marge de sécurité apparaîtra quand "
                            "assez de relevés réels seront comparés.\n", "g")
        f.insert("end", "\nDonnées : NOAA GFS 0.25° et GFS-Wave · courants : Copernicus Marine\n"
                        "A.C.I.S Marine · Sfax 22 633 254 · Kélibia 94 596 447\n", "g")
        f.config(state="disabled")


def ouvrir(chemin: str, parent=None, version: str = "", maintenant=None):
    """Ouvre la carte (fenêtre secondaire si parent est donné). Renvoie la Visionneuse.
    maintenant : heure Internet (datetime UTC) connue du programme, sinon None."""
    return Visionneuse(chemin, parent=parent, version=version, maintenant=maintenant)


def principal() -> int:
    if len(sys.argv) < 2:
        print("Usage : python visionneuse.py fichier.grb2")
        return 1
    v = ouvrir(sys.argv[1])
    v.win.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(principal())
