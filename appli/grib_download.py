"""
Téléchargement des fichiers GRIB marins pour la Tunisie (côtes + haute mer).

Sources (NOAA NOMADS, gratuites, découpage côté serveur -> fichiers légers) :
  - GFS 0.25°      : vent 10 m + rafales                              -> 16 jours (384 h)
  - GFS-Wave       : hauteur / direction / période des vagues           -> 16 jours (384 h)

Rapidité : plusieurs échéances sont téléchargées EN PARALLÈLE, en restant sous la
limite de la NOAA (~120 requêtes/minute, au-delà l'adresse IP est bloquée).

Les fichiers GRIB2 se concatènent simplement : le résultat s'ouvre tel quel dans XyGrib.
"""
from __future__ import annotations

import datetime as dt
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlencode

import http.client
import ssl
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Zone : toutes les côtes tunisiennes + haute mer (Pantelleria, Lampedusa,
# golfe de Gabès, frontière libyenne).
# ---------------------------------------------------------------------------
# Tunisie + voisins pour zoomer en arrière : Sardaigne, Sicile, Malte, sud de l'Italie, Libye jusqu'à Benghazi
ZONE_TUNISIE = {"nord": 42.0, "sud": 30.0, "ouest": 4.0, "est": 20.0}

NOMADS = "https://nomads.ncep.noaa.gov"
# Courants de surface Copernicus Marine (Méditerranée), préparés chaque jour par le robot GitHub (robot_courants/)
URL_COURANTS = "https://github.com/acis2025/meteo-courants/releases/download/courants/courants_tunisie.grb2"
# Fichier météo prêt (vent + vagues Tunisie) préparé toutes les 6 h par le robot GitHub ; {pas} = 1 ou 3
URL_METEO = "https://github.com/acis2025/meteo-courants/releases/download/meteo/Tunisie_{pas}h.grb2"
# Pluie + nuages (même run que le vent), et hauteur d'eau horaire (marée, Copernicus) : fichiers à part
URL_RUN = "https://github.com/acis2025/meteo-courants/releases/download/meteo/run.txt"
URL_CIEL = "https://github.com/acis2025/meteo-courants/releases/download/meteo/Ciel_{pas}h.grb2"
URL_MAREES = "https://github.com/acis2025/meteo-courants/releases/download/courants/marees_tunisie.grb2"
# Positions AIS (aisstream.io) autour de la Tunisie, photo toutes les 15 min par le robot GitHub
URL_AIS = "https://github.com/acis2025/meteo-courants/releases/download/ais/ais_tunisie.json"
# Navires de l'antenne AIS A.C.I.S (station_ais/), fusionnés avec ceux d'aisstream
URL_AIS_STATION = "https://github.com/acis2025/meteo-courants/releases/download/ais/ais_station.json"
PROD = NOMADS + "/pub/data/nccf/com/gfs/prod"

PAUSE_ENTRE_REQUETES = 0.6   # écart mini entre 2 requêtes (100/min, sous la limite NOAA de 120/min)
PARALLELE = 5                # téléchargements simultanés
ESSAIS = 3
TIMEOUT = 60
USER_AGENT = "MeteoMarineTN/1.0"


@dataclass
class Modele:
    nom: str
    script: str                       # CGI grib filter
    dossier: str                      # sous-dossier dans gfs.YYYYMMDD/HH/
    fichier: str                      # motif du nom de fichier ({hh}, {fff})
    variables: list[str]
    niveaux: list[str]
    echeances: list[int] = field(default_factory=list)

    def url_idx(self, date: str, hh: str, fff: int) -> str:
        f = self.fichier.format(hh=hh, fff=f"{fff:03d}")
        return f"{PROD}/gfs.{date}/{hh}/{self.dossier}/{f}.idx"

    def url_filtre(self, date: str, hh: str, fff: int, zone: dict) -> str:
        params = {
            "dir": f"/gfs.{date}/{hh}/{self.dossier}",
            "file": self.fichier.format(hh=hh, fff=f"{fff:03d}"),
        }
        for v in self.variables:
            params[f"var_{v}"] = "on"
        if self.niveaux:
            for n in self.niveaux:
                params[f"lev_{n}"] = "on"
        else:
            params["all_lev"] = "on"
        params.update({
            "subregion": "",
            "toplat": zone["nord"], "bottomlat": zone["sud"],
            "leftlon": zone["ouest"], "rightlon": zone["est"],
        })
        return f"{NOMADS}/cgi-bin/{self.script}?{urlencode(params)}"


def _echeances(max_h: int, pas_court: int, pas_long: int = 6) -> list[int]:
    """Pas fin jusqu'à 120 h (5 jours), puis pas_long (3 ou 6 h) jusqu'à max_h."""
    court = list(range(0, min(120, max_h) + 1, pas_court))
    long = list(range(120 + pas_long, max_h + 1, pas_long)) if max_h > 120 else []
    return sorted(set(court + long))


def modele_gfs(max_h: int = 384, pas: int = 3, pas_long: int = 6) -> Modele:
    return Modele(
        nom="Vent (GFS 0.25°)",
        script="filter_gfs_0p25.pl",
        dossier="atmos",
        fichier="gfs.t{hh}z.pgrb2.0p25.f{fff}",
        variables=["UGRD", "VGRD", "GUST"],          # vent 10 m + rafales
        niveaux=["10_m_above_ground", "surface"],
        echeances=_echeances(max_h, pas, pas_long),
    )


def modele_ciel(max_h: int = 384, pas: int = 3, pas_long: int = 6) -> Modele:
    """Pluie (intensité moyenne, kg/m²/s) + nébulosité totale (%) ; rien à l'heure 0."""
    return Modele(
        nom="Pluie et nuages (GFS 0.25°)",
        script="filter_gfs_0p25.pl",
        dossier="atmos",
        fichier="gfs.t{hh}z.pgrb2.0p25.f{fff}",
        variables=["PRATE", "TCDC"],
        niveaux=["surface", "entire_atmosphere"],
        echeances=[e for e in _echeances(max_h, pas, pas_long) if e > 0],
    )


def modele_vagues(max_h: int = 384, pas: int = 3, grille: str = "0p16", pas_long: int = 6) -> Modele:
    """grille "0p16" = plus précise ; "0p25" = même grille que GFS (fusion en 1 fichier)."""
    return Modele(
        nom=f"Vagues (GFS-Wave {grille.replace('p', '.')}°)",
        script="filter_gfswave.pl",
        dossier="wave/gridded",
        fichier="gfswave.t{hh}z.global." + grille + ".f{fff}.grib2",
        variables=["HTSGW", "DIRPW", "PERPW"],       # hauteur, direction, période
        niveaux=["surface"],
        echeances=_echeances(max_h, pas, pas_long),
    )


# ---------------------------------------------------------------------------
class ErreurReseau(Exception):
    pass


class Reponse:
    def __init__(self, status_code: int, content: bytes = b"", headers=None):
        self.status_code, self.content, self.headers = status_code, content, headers or {}


class SessionHttp:
    """HTTP(S) avec la seule bibliothèque standard (aucune DLL externe).

    Si l'horloge du PC est réglée dans le passé, le certificat HTTPS de la NOAA
    paraît « pas encore valide » : on passe alors sans contrôle HTTPS pour ces
    données météo publiques (l'heure et la licence restent vérifiées par heure.py)."""

    def __init__(self):
        self.verify = True
        self.headers = {"User-Agent": USER_AGENT}

    def _ctx(self):
        if self.verify:
            ctx = ssl.create_default_context()
            try:  # vieux Windows 7 : autorités racines périmées -> on ajoute celles de certifi
                import certifi
                ctx.load_verify_locations(certifi.where())
            except Exception:
                pass
            return ctx
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _envoyer(self, methode: str, url: str, timeout: float, rappel=None) -> Reponse:
        req = urllib.request.Request(url, method=methode.upper(), headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self._ctx()) as r:
                if rappel is None:
                    return Reponse(r.status, r.read(), dict(r.headers))
                total = int(r.headers.get("Content-Length") or 0)   # gros fichier : avancement affiché
                morceaux, lu = [], 0
                while True:
                    bloc = r.read(65536)
                    if not bloc:
                        break
                    morceaux.append(bloc)
                    lu += len(bloc)
                    try:
                        rappel(lu, total)
                    except Exception:                       # noqa: BLE001
                        pass
                if total and lu < total:
                    raise ErreurReseau(f"coupure ({lu // 1000} ko reçus sur {total // 1000})")
                return Reponse(r.status, b"".join(morceaux), dict(r.headers))
        except urllib.error.HTTPError as e:
            return Reponse(e.code, b"", dict(e.headers or {}))

    def requete(self, methode: str, url: str, timeout: float, rappel=None) -> Reponse:
        try:
            return self._envoyer(methode, url, timeout, rappel)
        except (urllib.error.URLError, ssl.SSLError) as e:
            raison = getattr(e, "reason", e)
            if self.verify and isinstance(raison, ssl.SSLError):
                self.verify = False
                try:
                    return self._envoyer(methode, url, timeout, rappel)
                except (urllib.error.URLError, OSError, http.client.HTTPException) as e2:
                    raise ErreurReseau(str(getattr(e2, "reason", e2)))
            raise ErreurReseau(str(raison))
        except (OSError, http.client.HTTPException) as e:   # délai dépassé, coupure en cours de route
            raise ErreurReseau(str(e) or e.__class__.__name__)

    def head(self, url, timeout):
        return self.requete("head", url, timeout)

    def get(self, url, timeout, rappel=None):
        return self.requete("get", url, timeout, rappel)


class Telechargeur:
    def __init__(self, heure: dt.datetime, log: Callable[[str], None] = print,
                 progression: Optional[Callable[[int, int], None]] = None,
                 session=None):
        self.log = log
        self.progression = progression or (lambda a, b: None)
        self.s = session or SessionHttp()
        self.heure = heure  # heure Internet fiable (l'horloge du PC peut être fausse)
        self.annule = False
        self._verrou = threading.Lock()
        self._prochain = 0.0

    def _attendre_tour(self) -> None:
        """Limiteur de débit commun à tous les fils : jamais plus de 100 requêtes/minute."""
        with self._verrou:
            maintenant = time.monotonic()
            depart = max(maintenant, self._prochain)
            self._prochain = depart + PAUSE_ENTRE_REQUETES
        if depart > maintenant:
            time.sleep(depart - maintenant)

    def _pause_globale(self, secondes: float) -> None:
        with self._verrou:
            self._prochain = max(self._prochain, time.monotonic() + secondes)

    def get_robot(self, url: str, timeout: float = 120, debut: int = 0, fin: int = 0, nom: str = "robot") -> Reponse:
        """Fichier préparé par le robot GitHub : 3 essais (un réseau mobile faible coupe parfois), avec
        l'avancement affiché de `debut` à `fin` %. ErreurReseau si les 3 essais échouent."""
        def rappel(lu, total):
            if total and fin > debut:
                self.progression(int(debut + (fin - debut) * min(lu, total) / total), 100)
        raison = "injoignable"
        for essai in range(3):
            if self.annule:
                raise RuntimeError("Téléchargement annulé")
            try:
                r = self.s.get(url, timeout, rappel) if isinstance(self.s, SessionHttp) else self.s.get(url, timeout)
                if r.status_code in (200, 403, 404):        # 404 : inutile de réessayer
                    return r
                raison = f"code {r.status_code}"
            except ErreurReseau as e:
                raison = f"réseau : {e}"
            if essai < 2:
                self.log(f"  {nom} : {raison}, nouvel essai dans {3 + 7 * essai} s…")
                time.sleep(3 + 7 * essai)
        raise ErreurReseau(raison)

    def courants(self, dest: str, url: str = URL_COURANTS, nom: str = "Courants Copernicus") -> Optional[str]:
        """Télécharge un fichier Copernicus préparé par le robot (courants, marée). None s'il n'est pas disponible."""
        for essai in range(3):
            try:
                r = self.s.get(url, 60)
            except ErreurReseau as e:
                self.log(f"{nom} : réseau ({e}), nouvel essai…")
                time.sleep(2 + 3 * essai)
                continue
            if r.status_code == 200 and r.content[:4] == b"GRIB":
                with open(dest + ".part", "wb") as f:
                    f.write(r.content)
                os.replace(dest + ".part", dest)
                self.log(f"{nom} : {len(r.content) / 1e6:.1f} Mo")
                return dest
            self.log(f"{nom} : indisponible (code {r.status_code})")
            return None
        return None

    def dernier_run(self, m: Modele) -> tuple[str, str]:
        """Le run le plus récent dont la DERNIÈRE échéance est déjà publiée."""
        now = self.heure
        derniere = m.echeances[-1]
        pannes = 0
        for k in range(0, 8):  # 8 runs = 2 jours en arrière
            t = now - dt.timedelta(hours=6 * k)
            hh = f"{(t.hour // 6) * 6:02d}"
            date = t.strftime("%Y%m%d")
            try:
                self._attendre_tour()
                r = self._requete("head", m.url_idx(date, hh, derniere), timeout=15)
                if r.status_code == 200:
                    return date, hh
            except ErreurReseau as e:
                self.log(f"  (vérification {date} {hh}z impossible : {e})")
                pannes += 1
                if pannes >= 2:                     # NOAA ne répond pas : inutile d'attendre 8 fois
                    break
        raise RuntimeError("Aucun run complet trouvé sur NOAA (connexion Internet ?)")

    def telecharger(self, m: Modele, dest: str, zone: dict = ZONE_TUNISIE,
                    run: Optional[tuple[str, str]] = None) -> str:
        date, hh = run or self.dernier_run(m)
        self.log(f"{m.nom} : run du {date[6:]}/{date[4:6]}/{date[:4]} {hh}h UTC, "
                 f"{len(m.echeances)} échéances jusqu'à +{m.echeances[-1]} h")
        n = len(m.echeances)
        resultats: dict = {}
        fait = [0]

        def une(fff: int):
            if self.annule:
                return
            resultats[fff] = self._get(m.url_filtre(date, hh, fff, zone))
            with self._verrou:
                fait[0] += 1
                self.progression(fait[0], n)

        with ThreadPoolExecutor(max_workers=PARALLELE) as pool:
            list(pool.map(une, m.echeances))
        if self.annule:
            raise RuntimeError("Téléchargement annulé")
        tmp = dest + ".part"
        ok = 0
        with open(tmp, "wb") as out:
            for fff in m.echeances:            # dans l'ordre chronologique
                data = resultats.get(fff)
                if data:
                    out.write(data)
                    ok += 1
                else:
                    self.log(f"  échéance +{fff} h manquante, ignorée")
        if ok == 0:
            os.remove(tmp)
            raise RuntimeError(f"{m.nom} : rien n'a pu être téléchargé")
        os.replace(tmp, dest)
        self.log(f"  -> {dest} ({os.path.getsize(dest) / 1e6:.1f} Mo, {ok}/{len(m.echeances)} échéances)")
        return dest

    def _requete(self, methode: str, url: str, timeout: float = TIMEOUT) -> Reponse:
        verif_avant = getattr(self.s, "verify", True)
        r = getattr(self.s, methode)(url, timeout=timeout)
        if verif_avant and getattr(self.s, "verify", True) is False:
            self.log("  (horloge du PC décalée : HTTPS sans contrôle de date pour les données NOAA)")
        return r

    def _get(self, url: str) -> Optional[bytes]:
        for essai in range(1, ESSAIS + 1):
            if self.annule:
                return None
            try:
                self._attendre_tour()
                r = self._requete("get", url)
                if r.status_code == 200 and r.content[:4] == b"GRIB":
                    return r.content
                if r.status_code in (403, 429):
                    self.log("  NOAA limite le débit, pause de 60 s…")
                    self._pause_globale(60)
            except ErreurReseau as e:
                self.log(f"  erreur réseau (essai {essai}/{ESSAIS}) : {e}")
            time.sleep(3 * essai)
        return None


_UNITES = {0: 60, 1: 3600, 2: 86400, 10: 3 * 3600, 11: 6 * 3600, 12: 12 * 3600, 13: 1}


def _echeance_message(m: bytes) -> float:
    """Échéance (h) lue dans la section 4 d'un message GRIB2, sans décoder les valeurs."""
    p = 16
    while p < len(m) - 4:
        lg = int.from_bytes(m[p:p + 4], "big")
        if lg < 5:
            break
        if m[p + 4] == 4:
            return int.from_bytes(m[p + 18:p + 22], "big") * _UNITES.get(m[p + 17], 3600) / 3600
        p += lg
    return 0.0


def _date_ref(m: bytes) -> Optional[dt.datetime]:
    lg = int.from_bytes(m[16:20], "big")          # section 1 juste après la section 0 (16 octets)
    if m[20] != 1 or lg < 21:
        return None
    s1 = m[16:16 + lg]
    an = int.from_bytes(s1[12:14], "big")
    return dt.datetime(an, s1[14], s1[15], s1[16], s1[17], tzinfo=dt.timezone.utc)


def _messages(b: bytes):
    """Découpe un fichier GRIB2 en messages (bytes), sans les décoder."""
    i = 0
    while True:
        j = b.find(b"GRIB", i)
        if j < 0 or j + 16 > len(b):
            return
        lg = int.from_bytes(b[j + 8:j + 16], "big")
        if lg < 16:
            return
        yield b[j:j + lg]
        i = j + lg


def _heure_valide(m: bytes) -> Optional[dt.datetime]:
    r = _date_ref(m)
    return None if r is None else r + dt.timedelta(minutes=round(_echeance_message(m) * 60))


def heures_fichier(b: bytes) -> set:
    """Heures (UTC) présentes dans un fichier GRIB2."""
    return {h for h in map(_heure_valide, _messages(b)) if h is not None}


def garder_heures(b: bytes, heures: set) -> bytes:
    """Ne garde que les messages valables à une heure de `heures`.
    XyGrib fait une image par heure présente dans le fichier : une heure où il n'y a que le courant
    donnerait une image sans vent (ex. 14:00 entre deux échéances GFS de 12:00 et 15:00)."""
    return b"".join(m for m in _messages(b) if _heure_valide(m) in heures)


def nom_maree(dossier: str, heure: dt.datetime) -> str:
    """Hauteur d'eau (marée) : fichier À PART pour la carte du téléphone. XyGrib ne l'affiche pas,
    et des heures « marée seule » dans le fichier XyGrib feraient des images vides."""
    os.makedirs(dossier, exist_ok=True)
    return os.path.join(dossier, f"Maree_{heure:%Y%m%d_%H%M}.grb2")


def fichier_robot(tel: "Telechargeur", dossier: str, jours: int, pas: int, courants: bool = True,
                  run_noaa: Optional[tuple] = None, maree: bool = False) -> Optional[str]:
    """Voie rapide : UN fichier préparé par le robot GitHub (vent + vagues, 16 j) au lieu de ~100 demandes NOAA.
    Il n'est pris QUE s'il contient le dernier run complet publié par NOAA (run_noaa = (« AAAAMMJJ », « HH »)) :
    le client a toujours la prévision la plus récente. Renvoie le fichier écrit, ou None (→ téléchargement direct)."""
    url = URL_METEO.format(pas=1 if int(pas) == 1 else 3) + "?t=" + str(int(time.time() // 60))
    tel.log("Fichier météo préparé (robot A.C.I.S)…")
    tel.progression(1, 100)
    try:
        r = tel.get_robot(url, 180, 2, 70, "fichier du robot")
    except ErreurReseau as e:
        tel.log(f"  robot indisponible ({e}) : téléchargement direct NOAA (plus long)")
        time.sleep(2)                                   # le temps que l'écran affiche la raison
        return None
    b = r.content
    if r.status_code != 200 or b[:4] != b"GRIB":
        tel.log(f"  robot indisponible (code {r.status_code}) : téléchargement direct NOAA")
        return None
    ref = _date_ref(b)
    if ref is None:
        tel.log("  fichier du robot illisible : téléchargement direct NOAA")
        return None
    if run_noaa:
        dernier = dt.datetime.strptime(run_noaa[0] + run_noaa[1], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=dt.timezone.utc)
        if ref < dernier - dt.timedelta(hours=6):     # robot en panne (2 runs de retard ou plus)
            tel.log(f"  le robot a le run du {ref:%d/%m %H}h UTC, NOAA a déjà celui du {dernier:%d/%m %H}h UTC : "
                    "téléchargement direct NOAA (plus long)")
            return None
        if ref < dernier:     # NOAA vient de publier : le robot le prépare (≈ 20 min). On ne fait pas attendre
            tel.log(f"  NOAA vient de publier le run du {dernier:%d/%m %H}h UTC, le robot le prépare : "
                    f"run du {ref:%d/%m %H}h UTC pris tout de suite (le nouveau à la prochaine mise à jour)")
    elif tel.heure - ref > dt.timedelta(hours=13):      # NOAA injoignable : on ne garde qu'un fichier récent
        tel.log("  fichier du robot trop ancien : téléchargement direct NOAA")
        return None
    tel.run_publie = ref
    max_h = int(jours) * 24
    garde = bytearray(b"".join(m for m in _messages(b) if _echeance_message(m) <= max_h))
    heures = heures_fichier(bytes(garde))             # les heures du vent et des vagues
    tel.log(f"  run du {ref:%d/%m %H}h UTC, {len(garde) / 1e6:.1f} Mo")
    garde = bytearray(ajouter_ciel(tel, bytes(garde), ref, pas, heures, (70, 88)))   # pluie + nuages (facultatif)
    tel.progression(90, 100)
    if courants:
        try:
            fc = tel.courants(nom_provisoire("courants", dossier))
            if fc:
                garde += garder_heures(open(fc, "rb").read(), heures)
                os.remove(fc)
        except Exception as e:
            tel.log(f"  courants non ajoutés : {e}")
    if maree:
        tel.progression(96, 100)
        telecharger_maree(tel, dossier)
    tel.progression(100, 100)
    dest = nom_fichier("complet", dossier, tel.heure)
    with open(dest + ".part", "wb") as f:
        f.write(bytes(garde))
    os.replace(dest + ".part", dest)
    return dest


def run_robot() -> Optional[dt.datetime]:
    """Run NOAA du fichier publié par le robot (run.txt, 10 octets). None si injoignable."""
    for essai in range(2):                          # 2 essais : un réseau mobile faible rate parfois le 1er
        try:
            r = SessionHttp().get(URL_RUN + "?t=" + str(int(time.time() // 60)), 20)
            if r.status_code == 200:
                return dt.datetime.strptime(r.content[:10].decode("ascii"), "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
        except Exception:                           # noqa: BLE001
            pass
        if essai == 0:
            time.sleep(2)
    return None


def _parametre(m: bytes) -> tuple:
    """(discipline, catégorie, numéro) d'un message GRIB2, lus dans les sections 0 et 4."""
    p = 16
    while p < len(m) - 4:
        lg = int.from_bytes(m[p:p + 4], "big")
        if lg < 5:
            break
        if m[p + 4] == 4:
            return m[6], m[p + 9], m[p + 10]
        p += lg
    return None


PLUIE_NUAGES = {(0, 1, 7), (0, 6, 1)}             # PRATE, TCDC


def a_pluie_nuages(b: bytes) -> bool:
    return any(_parametre(m) in PLUIE_NUAGES for m in _messages(b))


def ajouter_ciel(tel: "Telechargeur", garde: bytes, ref, pas: int, heures: set = None, avancement=(0, 0)) -> bytes:
    """Ajoute la pluie et les nuages préparés par le robot, seulement s'ils sont du MÊME run que `garde`
    (sinon les heures ne correspondraient pas). Facultatif : en cas d'échec, `garde` est rendu tel quel."""
    if ref is None:
        return garde
    try:
        rc = tel.get_robot(URL_CIEL.format(pas=1 if int(pas) == 1 else 3) + "?t=" + str(int(time.time() // 60)), 120,
                           avancement[0], avancement[1], "pluie et nuages")
        bc = rc.content
        if rc.status_code == 200 and bc[:4] == b"GRIB" and _date_ref(bc) == ref:
            ajout = garder_heures(bc, heures if heures is not None else heures_fichier(garde))
            tel.log(f"  pluie et nuages : {len(ajout) / 1e6:.1f} Mo")
            return garde + ajout
        tel.log("  pluie et nuages pas encore disponibles pour ce run")
    except Exception as e:                          # noqa: BLE001
        tel.log(f"  pluie et nuages non ajoutés : {e}")
    return garde


def run_du_fichier(chemin: str) -> Optional[dt.datetime]:
    """Run (date de référence) d'un fichier GRIB2, lu dans son 1er message.
    Fichier SANS pluie ni nuages (robot pas encore prêt, ou téléchargement direct NOAA) : le run est
    rendu un peu plus ancien UNE fois par run, pour que l'appli le retélécharge avec la pluie et les nuages."""
    try:
        with open(chemin, "rb") as f:
            b = f.read()
        ref = _date_ref(b[:64])
    except Exception:                               # noqa: BLE001
        return None
    try:
        if ref is not None and not a_pluie_nuages(b):
            dossier = os.path.dirname(chemin) or "."
            marque = os.path.join(dossier, f"essai_ciel_{ref:%Y%m%d%H}")
            if not os.path.exists(marque):
                for n in os.listdir(dossier):      # un seul repère (le run en cours)
                    if n.startswith("essai_ciel_"):
                        os.remove(os.path.join(dossier, n))
                open(marque, "w").close()
                return ref - dt.timedelta(minutes=1)
    except Exception:                               # noqa: BLE001
        pass
    return ref


def telecharger_maree(tel: "Telechargeur", dossier: str) -> Optional[str]:
    """Marée Copernicus (fichier à part, pour la carte du téléphone). Facultatif : None si indisponible."""
    try:
        return tel.courants(nom_maree(dossier, tel.heure), URL_MAREES, "Marée Copernicus")
    except Exception as e:                          # noqa: BLE001
        tel.log(f"  marée non téléchargée : {e}")
        return None


def nom_provisoire(prefixe: str, dossier: str) -> str:
    """Morceau d'un fichier unique en cours de fabrication (jamais pris pour des prévisions)."""
    os.makedirs(dossier, exist_ok=True)
    return os.path.join(dossier, f"en_cours_{prefixe}.grb2")


def nom_fichier(prefixe: str, dossier: str, heure: dt.datetime) -> str:
    os.makedirs(dossier, exist_ok=True)
    stamp = heure.strftime("%Y%m%d_%H%M")
    return os.path.join(dossier, f"Tunisie_{prefixe}_{stamp}.grb2")


def fusionner(fichiers: list[str], dest: str) -> str:
    """Concatène plusieurs GRIB2 (même grille) en un seul fichier pour XyGrib."""
    with open(dest + ".part", "wb") as out:
        for f in fichiers:
            with open(f, "rb") as src:
                out.write(src.read())
    os.replace(dest + ".part", dest)
    return dest
