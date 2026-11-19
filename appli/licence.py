"""
Licences d'abonnement (1 an) signées Ed25519.

Format de la clé :  MMTN1.<données base64url>.<signature base64url>
Les données (JSON) contiennent : nom, email, debut, fin, machine.
- Signée avec la clé PRIVÉE du vendeur (admin/generer_licence.py), jamais distribuée.
- Vérifiée avec la clé PUBLIQUE intégrée au programme : impossible de fabriquer
  ou de prolonger une licence sans la clé privée.
- Liée à un PC (identifiant machine) -> une clé ne marche pas sur un autre ordinateur.
- L'horloge du PC n'est JAMAIS utilisée (elle peut être réglée avant 2025 pour
  un autre logiciel) : la date vient d'Internet (heure.py, certificats vérifiés).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import platform
import sys
import uuid
from typing import Optional

import ed25519

try:
    from cle_publique import CLE_PUBLIQUE_B64
except ImportError:  # pas encore initialisé
    CLE_PUBLIQUE_B64 = ""
try:
    from cle_publique import CODE_SECRET_MASQUE
except ImportError:
    CODE_SECRET_MASQUE = ""

PREFIXE = "MMTN1"


# --------------------------------------------------------------------------- utilitaires
def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def dossier_app() -> str:
    if os.environ.get("MMTN_DOSSIER"):          # appli Android : dossier privé de l'appli
        d = os.environ["MMTN_DOSSIER"]
        os.makedirs(d, exist_ok=True)
        return d
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "MeteoMarineTN")
    os.makedirs(d, exist_ok=True)
    return d


def identifiant_machine() -> str:
    """Identifiant stable du PC (16 caractères), à envoyer au vendeur."""
    brut = ""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                brut = winreg.QueryValueEx(k, "MachineGuid")[0]
        except OSError:
            pass
    else:
        for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            if os.path.exists(p):
                brut = open(p).read().strip()
                break
    if not brut:
        brut = f"{uuid.getnode()}-{platform.node()}"
    h = hashlib.sha256(("MMTN:" + brut).encode()).hexdigest().upper()[:16]
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


# --------------------------------------------------------------------------- vérification
class LicenceInvalide(Exception):
    pass


# --------------------------------------------------------------------------- code court
# Code d'activation de 16 caractères (ex. K7QM-4XRT-9BWE-2HNA), facile à taper ou à
# dicter quand le PC n'a ni navigateur ni WhatsApp. Il contient la date de fin
# + une empreinte HMAC liée au PC et au nom du client.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # base32 Crockford (sans I, L, O, U)
EPOQUE = dt.date(2026, 1, 1)
_MASQUE = hashlib.sha256(b"MeteoMarineTN-masque-v1").digest()


def masquer(secret: bytes) -> str:
    return b64e(bytes(a ^ b for a, b in zip(secret, _MASQUE)))


def _secret(masque_b64: str = "") -> bytes:
    m = b64d(masque_b64 or CODE_SECRET_MASQUE)
    return bytes(a ^ b for a, b in zip(m, _MASQUE))


def normaliser_nom(nom: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", nom or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).upper()
    return "".join(ch for ch in t if ch.isalnum())


def _mac(secret: bytes, machine: str, nom: str, jours: int) -> bytes:
    import hmac
    msg = f"MMTC1|{machine.upper()}|{normaliser_nom(nom)}|{jours}".encode()
    return hmac.new(secret, msg, hashlib.sha256).digest()[:8]


def _b32(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = "".join(ALPHABET[(n >> (5 * i)) & 31] for i in range(16))[::-1]
    return "-".join(out[i:i + 4] for i in range(0, 16, 4))


def _deb32(code: str) -> bytes:
    t = code.upper().replace("O", "0").replace("I", "1").replace("L", "1")
    t = "".join(ch for ch in t if ch in ALPHABET)
    if len(t) != 16:
        raise ValueError("16 caractères attendus")
    n = 0
    for ch in t:
        n = n * 32 + ALPHABET.index(ch)
    return n.to_bytes(10, "big")


def fabriquer_code(secret: bytes, machine: str, nom: str, fin: dt.date) -> str:
    jours = (fin - EPOQUE).days
    return _b32(jours.to_bytes(2, "big") + _mac(secret, machine, nom, jours))


def lire_code(code: str, machine: str, nom: str, masque_b64: str = "") -> dict:
    try:
        b = _deb32(code)
    except ValueError:
        raise LicenceInvalide("Code d'activation incomplet (16 caractères)")
    jours = int.from_bytes(b[:2], "big")
    import hmac
    if not (masque_b64 or CODE_SECRET_MASQUE):
        raise LicenceInvalide("Programme non initialisé (secret absent)")
    if not hmac.compare_digest(b[2:], _mac(_secret(masque_b64), machine, nom, jours)):
        raise LicenceInvalide("Code incorrect (vérifiez le code et le nom, exactement comme envoyés)")
    fin = EPOQUE + dt.timedelta(days=jours)
    return {"nom": nom.strip(), "machine": machine.upper(), "fin": fin.isoformat() + "T23:59:59"}


# --------------------------------------------------------------------------- code préparé
# Code de 10 caractères (ex. K7QM4-XRT9B) préparé à l'avance par le vendeur :
#   n° client (12 bits, jusqu'à 4095) + date de fin (14 bits) + empreinte (24 bits).
# Il se lie au premier PC où il est saisi ; il n'est plus jamais affiché ensuite et
# le fichier de licence est chiffré avec l'identifiant du PC (inutilisable ailleurs).
def _mac_pre(secret: bytes, numero: int, jours: int) -> int:
    import hmac
    d = hmac.new(secret, f"MMTP1|{numero}|{jours}".encode(), hashlib.sha256).digest()
    return int.from_bytes(d[:3], "big")


def _feistel(secret: bytes, n: int, inverse: bool = False) -> int:
    """Brassage chiffré réversible sur 50 bits : les codes paraissent aléatoires."""
    import hmac
    g, d = n >> 25, n & 0x1FFFFFF
    tours = range(5, -1, -1) if inverse else range(6)
    for t in tours:
        if not inverse:
            f = int.from_bytes(hmac.new(secret, b"F%d|%d" % (t, d), hashlib.sha256).digest()[:4], "big") & 0x1FFFFFF
            g, d = d, g ^ f
        else:
            f = int.from_bytes(hmac.new(secret, b"F%d|%d" % (t, g), hashlib.sha256).digest()[:4], "big") & 0x1FFFFFF
            g, d = d ^ f, g
    return (g << 25) | d


def fabriquer_code_pre(secret: bytes, numero: int, fin: dt.date) -> str:
    if not 1 <= numero <= 4095:
        raise ValueError("numéro client 1 à 4095")
    jours = (fin - EPOQUE).days
    if not 0 <= jours < 16384:
        raise ValueError("date de fin hors limites")
    n = (numero << 38) | (jours << 24) | _mac_pre(secret, numero, jours)
    n = _feistel(secret, n)
    t = "".join(ALPHABET[(n >> (5 * i)) & 31] for i in range(10))[::-1]
    return t[:5] + "-" + t[5:]


def lire_code_pre(code: str, masque_b64: str = "") -> dict:
    t = (code or "").upper().replace("O", "0").replace("I", "1").replace("L", "1")
    t = "".join(ch for ch in t if ch in ALPHABET)
    if len(t) != 10:
        raise LicenceInvalide("Code d'activation incomplet (10 caractères)")
    n = 0
    for ch in t:
        n = n * 32 + ALPHABET.index(ch)
    import hmac
    if not (masque_b64 or CODE_SECRET_MASQUE):
        raise LicenceInvalide("Programme non initialisé (secret absent)")
    n = _feistel(_secret(masque_b64), n, inverse=True)
    numero, jours, mac = n >> 38, (n >> 24) & 0x3FFF, n & 0xFFFFFF
    if numero == 0 or not hmac.compare_digest(mac.to_bytes(3, "big"),
                                              _mac_pre(_secret(masque_b64), numero, jours).to_bytes(3, "big")):
        raise LicenceInvalide("Code d'activation incorrect")
    fin = EPOQUE + dt.timedelta(days=jours)
    return {"numero": numero, "fin": fin.isoformat() + "T23:59:59", "code": t}


def _flux(cle: bytes, n: int) -> bytes:
    out, i = b"", 0
    while len(out) < n:
        out += hashlib.sha256(cle + i.to_bytes(4, "big")).digest()
        i += 1
    return out[:n]


def sceller(nom: str, code: str, machine: str = "") -> str:
    """Enregistrement chiffré + lié au PC (le code n'y est pas lisible)."""
    import hmac
    import json as _j
    machine = machine or identifiant_machine()
    info = lire_code_pre(code)
    data = _j.dumps({"nom": nom.strip(), "code": info["code"]}, ensure_ascii=False).encode()
    k = hashlib.sha256(_secret() + b"|" + machine.encode()).digest()
    chiffre = bytes(a ^ b for a, b in zip(data, _flux(k, len(data))))
    tag = hmac.new(k, chiffre, hashlib.sha256).digest()[:16]
    return "MMTP1:" + b64e(tag + chiffre)


def desceller(texte: str, machine: str = "") -> dict:
    import hmac
    import json as _j
    machine = machine or identifiant_machine()
    try:
        brut = b64d(texte[len("MMTP1:"):])
    except Exception:
        raise LicenceInvalide("Licence illisible")
    k = hashlib.sha256(_secret() + b"|" + machine.encode()).digest()
    tag, chiffre = brut[:16], brut[16:]
    if not hmac.compare_digest(tag, hmac.new(k, chiffre, hashlib.sha256).digest()[:16]):
        raise LicenceInvalide("Licence d'un autre ordinateur : saisissez votre code d'activation")
    d = _j.loads(bytes(a ^ b for a, b in zip(chiffre, _flux(k, len(chiffre)))).decode())
    info = lire_code_pre(d["code"])
    return {"nom": d["nom"], "machine": machine, "fin": info["fin"], "numero": info["numero"]}


def decoder(cle: str, cle_publique_b64: str = "") -> dict:
    if cle.startswith("MMTP1:"):          # code préparé, scellé pour ce PC
        return desceller(cle)
    if cle.startswith("MMTC1|"):          # code court enregistré : MMTC1|nom|code
        _, nom, code = cle.split("|", 2)
        return lire_code(code, identifiant_machine(), nom)
    cle = "".join(cle.split())
    pub = cle_publique_b64 or CLE_PUBLIQUE_B64
    if not pub:
        raise LicenceInvalide("Programme non initialisé (clé publique absente)")
    try:
        pfx, donnees, sig = cle.split(".")
        assert pfx == PREFIXE
        signee = ed25519.verifier(b64d(pub), donnees.encode(), b64d(sig))
        info = json.loads(b64d(donnees)) if signee else None
    except Exception:
        raise LicenceInvalide("Format de clé de licence invalide")
    if not signee:
        raise LicenceInvalide("Clé de licence falsifiée ou erronée")
    return info


def _etat_path() -> str:
    return os.path.join(dossier_app(), "etat.json")


def _lire_etat() -> dict:
    try:
        with open(_etat_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _ecrire_etat(e: dict) -> None:
    with open(_etat_path(), "w", encoding="utf-8") as f:
        json.dump(e, f)


def derniere_heure_connue() -> Optional[dt.datetime]:
    d = _lire_etat().get("derniere_date")
    return dt.datetime.fromisoformat(d) if d else None


def verifier(cle: str, heure_internet: Optional[dt.datetime] = None,
             cle_publique_b64: str = "", machine: Optional[str] = None) -> dict:
    """Vérifie signature, PC et date d'expiration.

    heure_internet : date fiable obtenue en ligne. Si absente (hors ligne), on
    utilise la plus récente entre la dernière date Internet mémorisée et l'horloge
    de l'appareil : une horloge reculée ne prolonge jamais l'abonnement, une horloge
    juste le fait bien expirer même sans Internet.
    """
    info = decoder(cle, cle_publique_b64)
    machine = machine or identifiant_machine()
    if info.get("machine") not in ("*", machine):
        raise LicenceInvalide(f"Cette licence est liée à un autre ordinateur "
                              f"(ce PC : {machine})")

    etat = _lire_etat()
    connue = derniere_heure_connue()
    if heure_internet:
        reference = max(filter(None, [heure_internet, connue]))
        etat["derniere_date"] = reference.isoformat()
        _ecrire_etat(etat)
        info["verifie_en_ligne"] = True
    else:
        horloge = dt.datetime.now(dt.timezone.utc)
        reference = max(filter(None, [connue, horloge]))
        info["verifie_en_ligne"] = False

    fin = dt.datetime.fromisoformat(info["fin"]).replace(tzinfo=dt.timezone.utc)
    if reference is None:
        info["jours_restants"] = None          # inconnu tant qu'on n'est pas en ligne
        return info
    if reference > fin:
        raise LicenceInvalide(f"Abonnement expiré le {fin:%d/%m/%Y}. ")
    info["jours_restants"] = (fin - reference).days
    return info


# --------------------------------------------------------------------------- stockage
def _licence_path() -> str:
    return os.path.join(dossier_app(), "licence.key")


def cle_enregistree() -> str:
    try:
        return open(_licence_path(), encoding="utf-8").read().strip()
    except OSError:
        return ""


def enregistrer(cle: str) -> None:
    if not cle.startswith(("MMTC1|", "MMTP1:")):
        cle = "".join(cle.split())
    with open(_licence_path(), "w", encoding="utf-8") as f:
        f.write(cle)


def enregistrer_code(nom: str, code: str) -> None:
    enregistrer(f"MMTC1|{nom.strip()}|{code.strip().upper()}")
