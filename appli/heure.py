"""
Heure fiable obtenue sur Internet, SANS utiliser l'horloge du PC.

L'horloge du PC peut être volontairement réglée avant 2025 (autre logiciel du
client). On ne peut donc pas s'y fier, ni pour la licence, ni pour trouver le
dernier run météo, ni pour la vérification HTTPS classique.

Méthode : connexion TLS à un serveur connu, lecture de l'en-tête HTTP « Date »,
puis vérification de la chaîne de certificats À CETTE DATE (x509pur.py, Python
pur) avec les autorités de confiance (certifi + magasin Windows). Pour tricher,
il faudrait un faux certificat signé par une vraie autorité : pratiquement impossible.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import queue
import socket
import ssl
import sys
import threading
import time
from typing import Iterable, Optional

import x509pur

HOTES = ("www.google.com", "www.cloudflare.com", "github.com", "www.microsoft.com", "nomads.ncep.noaa.gov")
# Aucune date antérieure à la sortie du programme n'est acceptée.
DATE_MINIMUM = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)

_magasin: Optional[x509pur.Magasin] = None


def magasin() -> x509pur.Magasin:
    global _magasin
    if _magasin is None:
        extra = []
        if sys.platform == "win32":
            for nom in ("ROOT", "CA"):
                try:
                    extra += [c for c, enc, _ in ssl.enum_certificates(nom) if enc == "x509_asn"]
                except Exception:
                    pass
        texte = ""
        try:
            import certifi
            with open(certifi.where(), encoding="ascii") as f:
                texte = f.read()
        except Exception:
            pass
        _magasin = x509pur.Magasin.depuis_pem(texte, extra)
    return _magasin


def heure_hote(hote: str, port: int = 443, timeout: float = 10,
               store: Optional[x509pur.Magasin] = None) -> dt.datetime:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # vérifié plus bas, à la date du serveur
    with socket.create_connection((hote, port), timeout=timeout) as brut:
        with ctx.wrap_socket(brut, server_hostname=hote) as s:
            if hasattr(s, "get_unverified_chain"):        # Python 3.13+
                chaine = list(s.get_unverified_chain() or [])
            else:                                          # Python 3.8 (Windows 7)
                chaine = [s.getpeercert(binary_form=True)]
            s.sendall(f"HEAD / HTTP/1.1\r\nHost: {hote}\r\nUser-Agent: MeteoMarineTN\r\n"
                      f"Connection: close\r\n\r\n".encode())
            rep = b""
            while b"\r\n\r\n" not in rep and len(rep) < 65536:
                bloc = s.recv(4096)
                if not bloc:
                    break
                rep += bloc
    chaine = [c if isinstance(c, bytes) else c.public_bytes(getattr(ssl, "ENCODING_DER", 2)) for c in chaine]
    date = None
    for ligne in rep.decode("latin-1").split("\r\n"):
        if ligne.lower().startswith("date:"):
            date = email.utils.parsedate_to_datetime(ligne[5:].strip())
    if date is None:
        raise ValueError(f"{hote} : pas d'en-tête Date")
    x509pur.verifier_chaine(chaine, hote, date, store or magasin())
    return date


def heure_internet(hotes: Iterable[str] = HOTES, delai_total: float = 20, **kw) -> dt.datetime:
    """Tous les serveurs sont interrogés EN MÊME TEMPS : la 1re réponse valable suffit.
    (Avant : l'un après l'autre, un serveur lent faisait attendre 10 à 60 s.)"""
    hotes = list(hotes)
    if "store" not in kw:
        kw["store"] = magasin()                 # chargé une seule fois, avant les fils
    kw.setdefault("timeout", 8)
    q: "queue.Queue" = queue.Queue()

    def essai(h):
        try:
            d = heure_hote(h, **kw)
            if d < DATE_MINIMUM:
                raise ValueError("date incohérente")
            q.put((h, d, None))
        except Exception as e:                  # noqa: BLE001
            q.put((h, None, e))

    for h in hotes:
        threading.Thread(target=essai, args=(h,), daemon=True).start()
    erreurs, fin = [], time.monotonic() + delai_total
    for _ in hotes:
        try:
            h, d, e = q.get(timeout=max(0.1, fin - time.monotonic()))
        except queue.Empty:
            erreurs.append("pas de réponse à temps")
            break
        if d is not None:
            return d
        erreurs.append(f"{h} : {e}")
    raise RuntimeError("Impossible d'obtenir l'heure sur Internet (connexion ?)\n" + "\n".join(erreurs))
