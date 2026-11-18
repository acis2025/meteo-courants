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

import ssl
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Zone : toutes les côtes tunisiennes + haute mer (Pantelleria, Lampedusa,
# golfe de Gabès, frontière libyenne).
# ---------------------------------------------------------------------------
ZONE_TUNISIE = {"nord": 42.0, "sud": 30.0, "ouest": 4.0, "est": 20.0}

NOMADS = "https://nomads.ncep.noaa.gov"
# Courants de surface Copernicus Marine (Méditerranée), préparés chaque jour par le robot GitHub (robot_courants/)
URL_COURANTS = "https://github.com/acis2025/meteo-courants/releases/download/courants/courants_tunisie.grb2"
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

    def _envoyer(self, methode: str, url: str, timeout: float) -> Reponse:
        req = urllib.request.Request(url, method=methode.upper(), headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self._ctx()) as r:
                return Reponse(r.status, r.read(), dict(r.headers))
        except urllib.error.HTTPError as e:
            return Reponse(e.code, b"", dict(e.headers or {}))

    def requete(self, methode: str, url: str, timeout: float) -> Reponse:
        try:
            return self._envoyer(methode, url, timeout)
        except (urllib.error.URLError, ssl.SSLError) as e:
            raison = getattr(e, "reason", e)
            if self.verify and isinstance(raison, ssl.SSLError):
                self.verify = False
                try:
                    return self._envoyer(methode, url, timeout)
                except (urllib.error.URLError, OSError) as e2:
                    raise ErreurReseau(str(getattr(e2, "reason", e2)))
            raise ErreurReseau(str(raison))
        except OSError as e:
            raise ErreurReseau(str(e))

    def head(self, url, timeout):
        return self.requete("head", url, timeout)

    def get(self, url, timeout):
        return self.requete("get", url, timeout)


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

    def courants(self, dest: str, url: str = URL_COURANTS) -> Optional[str]:
        """Télécharge le fichier des courants (Copernicus). Renvoie None s'il n'est pas disponible."""
        for essai in range(3):
            try:
                r = self.s.get(url, 60)
            except ErreurReseau as e:
                self.log(f"Courants : réseau ({e}), nouvel essai…")
                time.sleep(2 + 3 * essai)
                continue
            if r.status_code == 200 and r.content[:4] == b"GRIB":
                with open(dest + ".part", "wb") as f:
                    f.write(r.content)
                os.replace(dest + ".part", dest)
                self.log(f"Courants Copernicus : {len(r.content) / 1e6:.1f} Mo")
                return dest
            self.log(f"Courants indisponibles (code {r.status_code})")
            return None
        return None

    def dernier_run(self, m: Modele) -> tuple[str, str]:
        """Le run le plus récent dont la DERNIÈRE échéance est déjà publiée."""
        now = self.heure
        derniere = m.echeances[-1]
        for k in range(0, 8):  # 8 runs = 2 jours en arrière
            t = now - dt.timedelta(hours=6 * k)
            hh = f"{(t.hour // 6) * 6:02d}"
            date = t.strftime("%Y%m%d")
            try:
                self._attendre_tour()
                r = self._requete("head", m.url_idx(date, hh, derniere))
                if r.status_code == 200:
                    return date, hh
            except ErreurReseau as e:
                self.log(f"  (vérification {date} {hh}z impossible : {e})")
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

    def _requete(self, methode: str, url: str) -> Reponse:
        verif_avant = getattr(self.s, "verify", True)
        r = getattr(self.s, methode)(url, timeout=TIMEOUT)
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
