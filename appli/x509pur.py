"""
Vérification de chaîne de certificats X.509 à une DATE CHOISIE, en Python pur.

Sert à heure.py : prouver qu'une date venant d'Internet est authentique même si
l'horloge du PC est fausse. Gère RSA (PKCS#1 v1.5) et ECDSA P-256 / P-384 avec
SHA-256/384/512, soit la quasi-totalité des certificats web actuels.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Optional


class ErreurCertificat(Exception):
    pass


# --------------------------------------------------------------------------- DER
def _tlv(b: bytes, i: int):
    """Renvoie (tag, début_contenu, fin_contenu)."""
    tag = b[i]
    n = b[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(b[i:i + k], "big")
        i += k
    return tag, i, i + n


def _enfants(b: bytes, debut: int, fin: int):
    out = []
    i = debut
    while i < fin:
        t, s, e = _tlv(b, i)
        out.append((t, s, e, i))  # i = début de l'élément complet (en-tête inclus)
        i = e
    return out


def _oid(v: bytes) -> str:
    parts = [v[0] // 40, v[0] % 40]
    n = 0
    for c in v[1:]:
        n = (n << 7) | (c & 0x7F)
        if not c & 0x80:
            parts.append(n)
            n = 0
    return ".".join(map(str, parts))


def _temps(tag: int, v: bytes) -> dt.datetime:
    s = v.decode()
    if tag == 0x17:  # UTCTime
        an = int(s[:2])
        s = ("19" if an >= 50 else "20") + s
    return dt.datetime.strptime(s[:14], "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)


@dataclass
class Cert:
    der: bytes
    tbs: bytes
    alg_sig: str
    signature: bytes
    emetteur: bytes
    sujet: bytes
    debut: dt.datetime
    fin: dt.datetime
    alg_cle: str
    param_cle: str
    cle: bytes
    est_ca: bool = False
    noms_dns: list = field(default_factory=list)
    aia: list = field(default_factory=list)   # URL du certificat de l'émetteur (caIssuers)


def lire_cert(der: bytes) -> Cert:
    _, s, e = _tlv(der, 0)
    tbs_e, sigalg_e, sig_e = _enfants(der, s, e)[:3]
    tbs = der[tbs_e[3]:tbs_e[2]]
    alg_sig = _oid(der[_enfants(der, sigalg_e[1], sigalg_e[2])[0][1]:_enfants(der, sigalg_e[1], sigalg_e[2])[0][2]])
    signature = der[sig_e[1] + 1:sig_e[2]]  # BIT STRING : on saute l'octet "bits inutilisés"

    ch = _enfants(der, tbs_e[1], tbs_e[2])
    k = 1 if ch[0][0] == 0xA0 else 0     # version explicite [0]
    emetteur = der[ch[k + 2][3]:ch[k + 2][2]]
    validite = _enfants(der, ch[k + 3][1], ch[k + 3][2])
    debut = _temps(validite[0][0], der[validite[0][1]:validite[0][2]])
    fin = _temps(validite[1][0], der[validite[1][1]:validite[1][2]])
    sujet = der[ch[k + 4][3]:ch[k + 4][2]]
    spki = _enfants(der, ch[k + 5][1], ch[k + 5][2])
    alg = _enfants(der, spki[0][1], spki[0][2])
    alg_cle = _oid(der[alg[0][1]:alg[0][2]])
    param_cle = _oid(der[alg[1][1]:alg[1][2]]) if len(alg) > 1 and alg[1][0] == 0x06 else ""
    cle = der[spki[1][1] + 1:spki[1][2]]
    c = Cert(der, tbs, alg_sig, signature, emetteur, sujet, debut, fin, alg_cle, param_cle, cle)

    for t, s2, e2, _ in ch[k + 6:]:
        if t != 0xA3:
            continue
        _, s3, e3 = _tlv(der, s2)
        for _, s4, e4, _ in _enfants(der, s3, e3):
            ext = _enfants(der, s4, e4)
            oid = _oid(der[ext[0][1]:ext[0][2]])
            val = der[ext[-1][1]:ext[-1][2]]
            if oid == "2.5.29.19":       # basicConstraints
                _, vs, ve = _tlv(val, 0)
                items = _enfants(val, vs, ve)
                c.est_ca = bool(items) and items[0][0] == 0x01 and val[items[0][1]] != 0
            elif oid == "1.3.6.1.5.5.7.1.1":   # authorityInfoAccess
                _, vs, ve = _tlv(val, 0)
                for _, s5, e5, _ in _enfants(val, vs, ve):
                    acc = _enfants(val, s5, e5)
                    if len(acc) == 2 and _oid(val[acc[0][1]:acc[0][2]]) == "1.3.6.1.5.5.7.48.2" \
                            and acc[1][0] == 0x86:
                        c.aia.append(val[acc[1][1]:acc[1][2]].decode("ascii", "ignore"))
            elif oid == "2.5.29.17":     # subjectAltName
                _, vs, ve = _tlv(val, 0)
                for t5, s5, e5, _ in _enfants(val, vs, ve):
                    if t5 == 0x82:
                        c.noms_dns.append(val[s5:e5].decode("ascii", "ignore").lower())
    return c


# --------------------------------------------------------------------------- RSA
_HASH = {
    "1.2.840.113549.1.1.11": "sha256", "1.2.840.113549.1.1.12": "sha384",
    "1.2.840.113549.1.1.13": "sha512",
    "1.2.840.10045.4.3.2": "sha256", "1.2.840.10045.4.3.3": "sha384",
    "1.2.840.10045.4.3.4": "sha512",
}
_DIGESTINFO = {
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}


def _int(b: bytes, s: int, e: int) -> int:
    return int.from_bytes(b[s:e], "big")


def _verif_rsa(cle: bytes, h: str, msg: bytes, sig: bytes) -> bool:
    _, s, e = _tlv(cle, 0)
    (_, ns, ne, _), (_, es, ee, _) = _enfants(cle, s, e)[:2]
    n, ex = _int(cle, ns, ne), _int(cle, es, ee)
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        return False
    m = pow(int.from_bytes(sig, "big"), ex, n).to_bytes(k, "big")
    t = _DIGESTINFO[h] + hashlib.new(h, msg).digest()
    attendu = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return m == attendu


# --------------------------------------------------------------------------- ECDSA
_COURBES = {
    "1.2.840.10045.3.1.7": (  # P-256
        0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF,
        0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFC,
        0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B,
        0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
        0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
        0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551),
    "1.3.132.0.34": (  # P-384
        int("FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFF0000000000000000FFFFFFFF", 16),
        int("FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFFFF0000000000000000FFFFFFFC", 16),
        int("B3312FA7E23EE7E4988E056BE3F82D19181D9C6EFE8141120314088F5013875AC656398D8A2ED19D2A85C8EDD3EC2AEF", 16),
        int("AA87CA22BE8B05378EB1C71EF320AD746E1D3B628BA79B9859F741E082542A385502F25DBF55296C3A545E3872760AB7", 16),
        int("3617DE4A96262C6F5D9E98BF9292DC29F8F41DBD289A147CE9DA3113B5F0B8C00A60B1CE1D7E819D7A431D7C90EA0E5F", 16),
        int("FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFC7634D81F4372DDF581A0DB248B0A77AECEC196ACCC52973", 16)),
}


def _jdouble(P, p, a):
    X, Y, Z = P
    if Y == 0 or Z == 0:
        return (0, 1, 0)
    YY = Y * Y % p
    S = 4 * X * YY % p
    M = (3 * X * X + a * pow(Z, 4, p)) % p
    X3 = (M * M - 2 * S) % p
    return (X3, (M * (S - X3) - 8 * YY * YY) % p, 2 * Y * Z % p)


def _jadd(P, Q, p, a):
    if P[2] == 0:
        return Q
    if Q[2] == 0:
        return P
    Z1Z1, Z2Z2 = P[2] * P[2] % p, Q[2] * Q[2] % p
    U1, U2 = P[0] * Z2Z2 % p, Q[0] * Z1Z1 % p
    S1, S2 = P[1] * Q[2] * Z2Z2 % p, Q[1] * P[2] * Z1Z1 % p
    if U1 == U2:
        return _jdouble(P, p, a) if S1 == S2 else (0, 1, 0)
    H, R = (U2 - U1) % p, (S2 - S1) % p
    HH = H * H % p
    HHH = H * HH % p
    V = U1 * HH % p
    X3 = (R * R - HHH - 2 * V) % p
    return (X3, (R * (V - X3) - S1 * HHH) % p, H * P[2] * Q[2] % p)


def _verif_ecdsa(cle: bytes, courbe: str, h: str, msg: bytes, sig: bytes) -> bool:
    if courbe not in _COURBES or cle[0] != 4:
        return False
    p, a, b, gx, gy, n = _COURBES[courbe]
    t = (len(cle) - 1) // 2
    qx, qy = int.from_bytes(cle[1:1 + t], "big"), int.from_bytes(cle[1 + t:], "big")
    if (qy * qy - qx ** 3 - a * qx - b) % p:
        return False
    _, s, e = _tlv(sig, 0)
    (_, rs, re_, _), (_, ss, se, _) = _enfants(sig, s, e)[:2]
    r, s_ = _int(sig, rs, re_), _int(sig, ss, se)
    if not (0 < r < n and 0 < s_ < n):
        return False
    z = int.from_bytes(hashlib.new(h, msg).digest(), "big")
    exces = len(hashlib.new(h).digest()) * 8 - n.bit_length()
    if exces > 0:
        z >>= exces
    w = pow(s_, -1, n)
    u1, u2 = z * w % n, r * w % n
    # Shamir : u1*G + u2*Q
    G, Q = (gx, gy, 1), (qx, qy, 1)
    GQ = _jadd(G, Q, p, a)
    R = (0, 1, 0)
    for i in range(max(u1.bit_length(), u2.bit_length()) - 1, -1, -1):
        R = _jdouble(R, p, a)
        b1, b2 = (u1 >> i) & 1, (u2 >> i) & 1
        if b1 and b2:
            R = _jadd(R, GQ, p, a)
        elif b1:
            R = _jadd(R, G, p, a)
        elif b2:
            R = _jadd(R, Q, p, a)
    if R[2] == 0:
        return False
    x = R[0] * pow(R[2] * R[2], -1, p) % p
    return x % n == r


def signature_valide(enfant: Cert, parent: Cert) -> bool:
    h = _HASH.get(enfant.alg_sig)
    if not h:
        return False
    try:
        if parent.alg_cle == "1.2.840.113549.1.1.1":
            return enfant.alg_sig.startswith("1.2.840.113549") and _verif_rsa(parent.cle, h, enfant.tbs, enfant.signature)
        if parent.alg_cle == "1.2.840.10045.2.1":
            return enfant.alg_sig.startswith("1.2.840.10045") and _verif_ecdsa(parent.cle, parent.param_cle, h, enfant.tbs, enfant.signature)
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------- chaîne
def _nom_ok(hote: str, noms: list) -> bool:
    hote = hote.lower()
    for n in noms:
        if n == hote:
            return True
        if n.startswith("*.") and "." in hote and hote.split(".", 1)[1] == n[2:]:
            return True
    return False


class Magasin:
    """Autorités racines de confiance (certifi + magasin Windows)."""

    def __init__(self, ders):
        self.par_sujet: dict = {}
        for der in ders:
            try:
                c = lire_cert(der)
            except Exception:
                continue
            self.par_sujet.setdefault(c.sujet, []).append(c)

    @classmethod
    def depuis_pem(cls, texte: str, extra=()):
        import base64
        ders = list(extra)
        for bloc in texte.split("-----BEGIN CERTIFICATE-----")[1:]:
            b64 = bloc.split("-----END CERTIFICATE-----")[0]
            ders.append(base64.b64decode("".join(b64.split())))
        return cls(ders)


def _charger_der(donnees: bytes) -> list:
    """Certificat(s) téléchargé(s) : DER ou PEM."""
    if b"-----BEGIN CERTIFICATE-----" in donnees:
        import base64
        out = []
        for bloc in donnees.split(b"-----BEGIN CERTIFICATE-----")[1:]:
            out.append(base64.b64decode(b"".join(bloc.split(b"-----END CERTIFICATE-----")[0].split())))
        return out
    return [donnees] if donnees[:1] == b"\x30" else []


def telecharger_aia(url: str, timeout: float = 10) -> list:
    """Récupère le certificat intermédiaire (comme les navigateurs). HTTP simple :
    pas besoin de confiance ici, la signature est vérifiée ensuite."""
    import urllib.request
    if not url.startswith("http://") and not url.startswith("https://"):
        return []
    req = urllib.request.Request(url, headers={"User-Agent": "MeteoMarineTN"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _charger_der(r.read(200000))


def verifier_chaine(chaine_der: list, hote: str, date: dt.datetime, magasin: Magasin,
                    aia=telecharger_aia) -> None:
    """Lève ErreurCertificat si la chaîne n'est pas valide pour `hote` à `date`.
    Si des intermédiaires manquent (Python 3.8 / Windows 7 ne donne que le certificat
    du serveur), ils sont téléchargés via l'adresse indiquée dans le certificat."""
    if not chaine_der:
        raise ErreurCertificat("aucun certificat")
    certs = [lire_cert(d) for d in chaine_der]
    feuille, inter = certs[0], certs[1:]
    if not _nom_ok(hote, feuille.noms_dns):
        raise ErreurCertificat(f"certificat non prévu pour {hote}")
    courant = feuille
    for _ in range(8):
        if not (courant.debut <= date <= courant.fin):
            raise ErreurCertificat("certificat hors de sa période de validité à cette date")
        for racine in magasin.par_sujet.get(courant.emetteur, []):
            if signature_valide(courant, racine):
                return  # chaîne reliée à une autorité de confiance
        suivant = None
        for c in inter:
            if c.sujet == courant.emetteur and c.est_ca and c is not courant and signature_valide(courant, c):
                suivant = c
                break
        if suivant is None and aia is not None:
            for url in courant.aia:
                try:
                    for der in aia(url):
                        c = lire_cert(der)
                        inter.append(c)
                        if c.sujet == courant.emetteur and c.est_ca and signature_valide(courant, c):
                            suivant = c
                            break
                except Exception:
                    continue
                if suivant is not None:
                    break
        if suivant is None:
            raise ErreurCertificat("chaîne non reliée à une autorité de confiance")
        courant = suivant
    raise ErreurCertificat("chaîne trop longue")
