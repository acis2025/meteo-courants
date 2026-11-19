"""
Serveur interne de l'appli Android (127.0.0.1) — aucune dépendance externe.

L'écran de l'appli est une page web (WebView) servie ici :
  GET  /                -> la CARTE directement (dernières prévisions)
  GET  /parametres      -> ⚙ abonnement / activation / réglages du téléchargement
  GET  /api/etat        -> état (licence, dernier fichier, téléchargement)
  POST /api/maj         -> mise à jour automatique si de nouvelles prévisions NOAA existent
  POST /api/activer     -> {"nom", "code"} : code de 10 caractères (le même que sur PC)
  POST /api/telecharger -> {"jours", "pas"} : téléchargement immédiat
  POST /api/reglages    -> {"jours", "pas"} : réglages du téléchargement automatique
Le moteur (grib2, grib_download, heure, licence…) est exactement celui du programme PC.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import time
import traceback
import uuid

ICI = os.path.dirname(os.path.abspath(__file__))
MOTEUR = os.path.join(ICI, "moteur")
# Dans l'APK le moteur est copié dans ./moteur ; en test sur PC on prend le projet principal.
PRESSE_PAPIERS = None      # (copier, coller) fournis par l'appli Android
RACINE = MOTEUR if os.path.isdir(MOTEUR) else os.path.abspath(os.path.join(ICI, "..", "..", ".."))
sys.path.insert(0, RACINE)
sys.path.insert(0, os.path.join(RACINE, "carte"))



def lire_page(nom: str) -> str:
    """Page de la mise à jour en ligne si elle en contient une, sinon celle de l'APK."""
    try:
        import maj_en_ligne
        b = maj_en_ligne.ressource(nom)
        if b is not None:
            return b.decode("utf-8")
    except ImportError:
        pass
    return open(os.path.join(ICI, nom), encoding="utf-8").read()


REGLAGES_DEFAUT = {"jours": 7, "pas": 3}
DELAI_PUBLICATION_H = 5.5    # run NOAA 00/06/12/18 UTC : publié par le robot ~5 h 20 après (mesuré nov. 2026)
REESSAI_S = 600              # hors ligne : nouvel essai automatique au plus toutes les 10 min


class Appli:
    """État de l'appli + actions. `dossier` = dossier privé (prévisions, licence, réglages)."""

    def __init__(self, dossier: str):
        os.environ["MMTN_DOSSIER"] = dossier
        self.dossier = dossier
        self.dossier_grib = os.path.join(dossier, "previsions")
        os.makedirs(self.dossier_grib, exist_ok=True)
        import licence
        import meteo_marine as mm
        self.licence, self.mm = licence, mm
        licence.identifiant_machine = self.identifiant_appareil   # identifiant stable propre au téléphone
        self.heure = None            # heure Internet (jamais l'horloge du téléphone)
        self._heure_mono = 0.0
        self.prog = {"en_cours": False, "i": 0, "n": 0, "etape": "", "message": "", "erreur": "", "fini": False}
        self._cache = (None, None)
        self._verrou = threading.Lock()
        self._dernier_essai = 0.0
        self._ais = (0.0, None)

    # ------------------------------------------------------------ appareil / réglages / heure
    def identifiant_appareil(self) -> str:
        f = os.path.join(self.dossier, "appareil.id")
        if not os.path.exists(f):
            with open(f, "w") as h:
                h.write(uuid.uuid4().hex)
        brut = open(f).read().strip()
        x = hashlib.sha256(("MMTN-android:" + brut).encode()).hexdigest().upper()[:16]
        return "-".join(x[i:i + 4] for i in range(0, 16, 4))

    def reglages(self) -> dict:
        try:
            r = json.load(open(os.path.join(self.dossier, "reglages.json")))
            return {"jours": int(r.get("jours", 7)), "pas": int(r.get("pas", 3))}
        except (OSError, ValueError):
            return dict(REGLAGES_DEFAUT)

    def sauver_reglages(self, jours, pas) -> dict:
        r = {"jours": max(1, min(16, int(jours))), "pas": 1 if int(pas) == 1 else 3}
        json.dump(r, open(os.path.join(self.dossier, "reglages.json"), "w"))
        return {"ok": True, **r}

    def heure_actuelle(self, forcer=False):
        """Heure Internet ; rafraîchie au plus toutes les 5 min, avancée entre-temps par l'horloge monotone."""
        if forcer or self.heure is None or time.monotonic() - self._heure_mono > 300:
            import heure
            self.heure = heure.heure_internet()
            self._heure_mono = time.monotonic()
            return self.heure
        return self.heure + dt.timedelta(seconds=time.monotonic() - self._heure_mono)

    def heure_licence(self):
        """Heure du jour pour l'abonnement (jamais celle d'hier gardée en mémoire) ; None si hors ligne."""
        if getattr(self, "_echec_heure", 0) and time.monotonic() - self._echec_heure < 60:
            return None                           # hors ligne il y a moins d'1 min : pas de nouvel essai
        try:
            h = self.heure_actuelle()
            self._echec_heure = 0
            return h
        except Exception:
            self._echec_heure = time.monotonic()
            return None                           # licence.verifier prend alors la dernière date connue / l'horloge

    # ------------------------------------------------------------ licence
    def info_licence(self) -> dict:
        try:
            i = self.mm.controle_licence(self.heure_licence())
            return {"ok": True, "nom": i["nom"], "fin": i["fin"][:10], "jours": i.get("jours_restants")}
        except self.licence.LicenceInvalide as e:
            return {"ok": False, "erreur": str(e)}

    def activer(self, nom: str, code: str) -> dict:
        nom = (nom or "").strip()
        if not nom:
            return {"ok": False, "erreur": "Écrivez le nom du client."}
        try:
            self.heure_actuelle()
        except Exception:
            pass                                  # hors ligne : date vérifiée au 1er téléchargement
        try:
            scelle = self.licence.sceller(nom, code)
            self.licence.verifier(scelle, self.heure_licence())
        except self.licence.LicenceInvalide as e:
            return {"ok": False, "erreur": str(e)}
        self.licence.enregistrer(scelle)
        threading.Thread(target=self.maj_auto, daemon=True).start()   # 1res prévisions tout de suite
        return {"ok": True, **self.info_licence()}

    # ------------------------------------------------------------ prévisions
    def dernier_fichier(self):
        f = sorted(x for x in os.listdir(self.dossier_grib) if x.startswith("Tunisie_") and x.endswith(".grb2"))
        return os.path.join(self.dossier_grib, f[-1]) if f else None

    @staticmethod
    def date_fichier(chemin):
        """Date Internet (UTC) du téléchargement, lue dans le nom du fichier."""
        m = re.search(r"_(\d{8})_(\d{4})\.grb2$", chemin or "")
        return dt.datetime.strptime("".join(m.groups()), "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc) if m else None

    def maj_necessaire(self, maintenant) -> bool:
        """Vrai si un run NOAA plus récent que notre fichier est disponible."""
        f = self.dernier_fichier()
        d = self.date_fichier(f)
        if d is None:
            return True
        base = maintenant - dt.timedelta(hours=DELAI_PUBLICATION_H)
        run = base.replace(hour=base.hour - base.hour % 6, minute=0, second=0, microsecond=0)
        if d < run + dt.timedelta(hours=DELAI_PUBLICATION_H):
            return True
        # fichier pris pendant que le robot préparait le run suivant : on le prend dès qu'il est prêt
        import grib_download as gd
        a, r = gd.run_du_fichier(f), gd.run_robot()
        return bool(a and r and r > a)

    def maj_auto(self) -> dict:
        """Appelée à l'ouverture de l'appli et de la carte : télécharge seulement s'il y a du nouveau."""
        if self.prog["en_cours"]:
            return {"lance": False, "raison": "en cours"}
        if not self.info_licence()["ok"]:
            return {"lance": False, "raison": "abonnement"}
        if time.monotonic() - self._dernier_essai < REESSAI_S and self._dernier_essai:
            return {"lance": False, "raison": "attente"}
        self._dernier_essai = time.monotonic()
        try:
            maintenant = self.heure_actuelle()
        except Exception:
            self.prog.update(erreur="Pas de connexion Internet", fini=False)
            return {"lance": False, "raison": "hors ligne"}
        if not self.maj_necessaire(maintenant):
            return {"lance": False, "raison": "à jour"}
        r = self.reglages()
        return {"lance": self.telecharger(r["jours"], r["pas"])["ok"]}

    def telecharger(self, jours: int, pas: int) -> dict:
        with self._verrou:
            if self.prog["en_cours"]:
                return {"ok": False, "erreur": "Téléchargement déjà en cours"}
            if not self.info_licence()["ok"]:
                return {"ok": False, "erreur": "Abonnement non activé"}
            self.prog = {"en_cours": True, "i": 0, "n": 0, "etape": "Recherche des nouvelles prévisions",
                         "message": "", "erreur": "", "fini": False}
        threading.Thread(target=self._telecharger, args=(int(jours), int(pas)), daemon=True).start()
        return {"ok": True}

    def _telecharger(self, jours: int, pas: int):
        import grib_download as gd
        try:
            h = self.heure_actuelle(forcer=True)
            self.mm.controle_licence(h)

            def progression(i, n):
                self.prog.update(i=i, n=n)

            def log(m):
                self.prog["message"] = m
                if m.startswith(("Vent", "Vagues")):
                    self.prog["etape"] = m.split(" :")[0].split(" (")[0]
            self.prog.update(etape="Téléchargement du fichier météo")
            tel = gd.Telechargeur(h, log=log, progression=progression)
            cfg = {"jours": jours, "pas_h": pas, "pas_long": 6 if pas > 1 else 3, "vent": True, "vagues": True,
                   "fusion": True, "dossier": self.dossier_grib, "garder": 2, "maree": True}
            self.mm.executer(cfg, tel)
            self.prog.update(en_cours=False, fini=True, etape="Terminé")
        except Exception as e:                      # erreur montrée à l'écran
            self.prog.update(en_cours=False, erreur=str(e) or e.__class__.__name__)
            traceback.print_exc()

    def fichier_maree(self, f):
        """Marée du même téléchargement (fichier à part, Maree_AAAAMMJJ_HHMM.grb2), sinon la plus récente."""
        m = re.search(r"_(\d{8}_\d{4})\.grb2$", f or "")
        if m and os.path.exists(os.path.join(self.dossier_grib, f"Maree_{m.group(1)}.grb2")):
            return os.path.join(self.dossier_grib, f"Maree_{m.group(1)}.grb2")
        l = sorted(x for x in os.listdir(self.dossier_grib) if x.startswith("Maree_") and x.endswith(".grb2"))
        return os.path.join(self.dossier_grib, l[-1]) if l else None

    def page_carte(self) -> str:
        f = self.dernier_fichier()
        fm = self.fichier_maree(f)
        cle = (f, os.path.getmtime(f), fm, os.path.getmtime(fm) if fm else 0)
        if self._cache[0] != cle:
            import grib2
            import donnees_carte
            champs = grib2.lire_fichier(f)
            if fm:
                try:
                    champs += grib2.lire_fichier(fm)
                except Exception:                 # marée illisible : la carte reste utilisable
                    traceback.print_exc()
            self._cache = (cle, donnees_carte.construire(champs))
        lic = self.info_licence()
        maintenant = self.heure + dt.timedelta(seconds=time.monotonic() - self._heure_mono) if self.heure else None
        options = {
            "abonnement": (f"✔ Abonnement valable jusqu'au {lic['fin'][8:10]}/{lic['fin'][5:7]}/{lic['fin'][:4]}"
                           if lic["ok"] else "✖ " + lic["erreur"]),
            "actions": [["⟳  Mettre à jour maintenant", "location.href='/parametres?maj=1'"],
                        ["⚙  Paramètres / abonnement", "location.href='/parametres'", 1]],
            "parametres": "/parametres",
            "aisUrl": "/api/ais",
            "suivi": {"etat": "/api/etat", "maj": "/api/maj", "fichier": os.path.basename(f)},
        }
        if maintenant:
            options["maintenant"] = maintenant.isoformat()
        if not lic["ok"]:
            options["bandeau"] = "Abonnement : " + lic["erreur"] + " — ⚙ Paramètres"
        import donnees_carte
        return donnees_carte.page_html(self._cache[1], lic.get("nom", ""), options)

    def ais(self) -> dict:
        """Navires AIS (robot GitHub), gardés 90 s en mémoire ; âge calculé avec l'heure Internet."""
        import grib_download as gd
        t0, d = self._ais
        if d is None or time.monotonic() - t0 > 90:
            fusion, maj, erreur = {}, 0, None
            for url in (gd.URL_AIS, gd.URL_AIS_STATION):      # aisstream + antenne A.C.I.S
                try:
                    r = gd.SessionHttp().get(url + "?t=" + str(int(time.time() // 60)), 20)
                    if r.status_code != 200:
                        raise OSError(f"code {r.status_code}")
                    x = json.loads(r.content)
                except Exception as e:
                    erreur = erreur or e
                    continue
                maj = max(maj, x.get("maj", 0))
                for n in x.get("navires", []):
                    a = fusion.get(n["mmsi"])
                    if a is None:
                        fusion[n["mmsi"]] = dict(n)
                        continue
                    neuf, vieux = (n, a) if n.get("t", 0) > a.get("t", 0) else (a, n)
                    m = dict(vieux)
                    m.update({k: v for k, v in neuf.items() if v not in ("", None)})
                    for k, vide in (("nom", str(n["mmsi"])), ("type", 0)):   # garder le vrai nom / type connu
                        if m.get(k) in ("", None, vide) and vieux.get(k) not in ("", None, vide):
                            m[k] = vieux[k]
                    fusion[n["mmsi"]] = m
            if fusion or maj:
                d = {"maj": maj, "navires": list(fusion.values())}
                self._ais = (time.monotonic(), d)
            elif d is None:
                return {"ok": False, "erreur": "AIS indisponible (" + str(erreur)[:60] + ")"}
        try:
            maintenant = self.heure_actuelle().timestamp()
        except Exception:
            maintenant = time.time()
        navires = []
        for n in d.get("navires", []):
            n = dict(n)
            n["age"] = max(0, int((maintenant - n.get("t", maintenant)) / 60))
            navires.append(n)
        return {"ok": True, "navires": navires, "age_photo": max(0, int((maintenant - d.get("maj", maintenant)) / 60))}

    def etat(self) -> dict:
        f = self.dernier_fichier()
        return {"licence": self.info_licence(), "appareil": self.identifiant_appareil(),
                "fichier": os.path.basename(f) if f else None, "progression": self.prog,
                "reglages": self.reglages()}


# ---------------------------------------------------------------- HTTP
def _gestionnaire(appli: Appli):
    parametres = lire_page("parametres.html")

    class G(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _envoyer(self, code, corps, type_="text/html; charset=utf-8"):
            b = corps.encode("utf-8") if isinstance(corps, str) else corps
            self.send_response(code)
            self.send_header("Content-Type", type_)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _json(self, obj, code=200):
            self._envoyer(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

        def _redir(self, url):
            self.send_response(302)
            self.send_header("Location", url)
            self.end_headers()

        def do_GET(self):
            chemin = self.path.split("?")[0]
            try:
                if chemin == "/":
                    if not appli.info_licence()["ok"]:
                        self._redir("/parametres#activation")           # abonnement fini : plus de carte
                    elif appli.dernier_fichier():
                        self._envoyer(200, appli.page_carte())          # ouverture = la carte
                    elif appli.info_licence()["ok"]:
                        self._redir("/parametres?premiere=1")           # 1re fois : premières prévisions
                    else:
                        self._redir("/parametres#activation")
                elif chemin in ("/parametres", "/accueil"):
                    self._envoyer(200, parametres)
                elif chemin == "/api/etat":
                    self._json(appli.etat())
                elif chemin == "/api/ais":
                    self._json(appli.ais())
                elif chemin == "/logo.png":
                    lg = os.path.join(RACINE, "ressources", "logo_256.png")
                    if not os.path.exists(lg):
                        lg = os.path.join(RACINE, "ressources", "logo_96.png")
                    self._envoyer(200, open(lg, "rb").read(), "image/png")
                else:
                    self._envoyer(404, "introuvable", "text/plain")
            except Exception as e:
                traceback.print_exc()
                self._envoyer(500, f"<pre>Erreur : {e}</pre>")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                d = {}
            try:
                if self.path == "/api/activer":
                    self._json(appli.activer(d.get("nom", ""), d.get("code", "")))
                elif self.path == "/api/telecharger":
                    r = appli.reglages()
                    self._json(appli.telecharger(d.get("jours", r["jours"]), d.get("pas", r["pas"])))
                elif self.path == "/api/maj":
                    self._json(appli.maj_auto())
                elif self.path == "/api/reglages":
                    self._json(appli.sauver_reglages(d.get("jours", 7), d.get("pas", 3)))
                elif self.path == "/api/coller":          # presse-papiers natif (bouton « Coller » du code)
                    if not PRESSE_PAPIERS:
                        self._json({"ok": False, "erreur": "presse-papiers indisponible"})
                    else:
                        try:
                            self._json({"ok": True, "texte": PRESSE_PAPIERS[1]()})
                        except Exception as e:      # noqa: BLE001
                            traceback.print_exc()
                            self._json({"ok": False, "erreur": f"{type(e).__name__} : {e}"})
                else:
                    self._json({"ok": False, "erreur": "inconnu"}, 404)
            except Exception as e:
                traceback.print_exc()
                self._json({"ok": False, "erreur": str(e)}, 500)
    return G


class _Serveur(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def demarrer(dossier: str, port: int = 0):
    """Démarre le serveur en arrière-plan et lance la vérification des nouvelles prévisions ; renvoie (appli, port)."""
    appli = Appli(dossier)
    srv = _Serveur(("127.0.0.1", port), _gestionnaire(appli))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=appli.maj_auto, daemon=True).start()
    return appli, srv.server_address[1]
