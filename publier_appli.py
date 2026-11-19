"""
Robot : fabrique et SIGNE la mise à jour en ligne du programme client (PC + téléphone).

Source : le dossier appli/ (code Python + pages HTML de la dernière version).
Sortie : maj.zip, maj.sig (signature Ed25519), maj.json (numéro de version) -> release « appli ».
Les programmes clients les téléchargent seuls et refusent tout paquet mal signé.

Pour publier une modification : changer le fichier dans appli/ ET augmenter VERSION dans
appli/version_appli.py (format AAAAMMJJHHMM). La clé secrète est le secret GitHub MAJ_CLE.
"""
import ast
import hashlib
import io
import json
import os
import re
import sys
import zipfile

import ed25519

ICI = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ICI, "appli")
DOMAINE = b"MeteoMarineTN-maj\x00"
NOMS = {"serveur.py": "meteomarinetn.serveur"}          # modules qui sont dans un paquet
INTERDITS = {"maj_en_ligne", "cle_maj_publique", "cle_publique", "ed25519", "version_appli"}
RESSOURCES = (".html", ".js", ".css", ".json", ".txt")


def fabriquer(src: str = SRC) -> tuple:
    txt = open(os.path.join(src, "version_appli.py"), encoding="utf-8").read()
    version = int(re.search(r"(?m)^VERSION\s*=\s*(\d{12})\b", txt).group(1))
    m = re.search(r"(?m)^BASE_MIN\s*=\s*(\d{12})\b", txt)
    base_min = int(m.group(1)) if m else 0
    modules, ressources, fichiers = {}, [], []
    for nom in sorted(os.listdir(src)):
        chemin = os.path.join(src, nom)
        if not os.path.isfile(chemin) or nom.startswith(".") or nom == "LISEZMOI.md":
            continue
        if nom.endswith(".py"):
            mod = NOMS.get(nom, nom[:-3])
            if mod.split(".")[-1] in INTERDITS:
                continue
            # le programme PC tourne aussi sous Windows 7 (Python 3.8) : refus si la syntaxe est trop récente
            ast.parse(open(chemin, "rb").read(), filename=nom, feature_version=(3, 8))
            modules[mod] = nom
        elif nom.endswith(RESSOURCES):
            ressources.append(nom)
        else:
            continue
        fichiers.append(nom)
    man = {"version": version, "base_min": base_min, "modules": modules, "ressources": ressources}
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        for nom, contenu in [("manifeste.json", json.dumps(man, indent=1, ensure_ascii=False).encode("utf-8"))] + \
                [(f, open(os.path.join(src, f), "rb").read()) for f in fichiers]:
            info = zipfile.ZipInfo(nom, date_time=(2026, 1, 1, 0, 0, 0))   # même contenu = même fichier
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, contenu)
    return man, tampon.getvalue()


def main(sortie: str = ".") -> None:
    cle = os.environ.get("MAJ_CLE", "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", cle):
        sys.exit("Secret GitHub MAJ_CLE absent ou invalide (64 caractères hexadécimaux) : "
                 "lancez CLE_MISES_A_JOUR.bat sur le PC A.C.I.S")
    graine = bytes.fromhex(cle)
    man, zb = fabriquer()
    publique = os.path.join(ICI, "cle_maj_publique.txt")     # contrôle : même clé que dans les programmes
    if os.path.exists(publique):
        import base64
        attendue = open(publique, encoding="ascii").read().strip()
        if base64.urlsafe_b64encode(ed25519.cle_publique(graine)).decode().rstrip("=") != attendue.rstrip("="):
            sys.exit("MAJ_CLE ne correspond pas à cle_maj_publique.txt : les clients refuseraient le paquet")
    sig = ed25519.signer(graine, DOMAINE + zb).hex()
    assert ed25519.verifier(ed25519.cle_publique(graine), DOMAINE + zb, bytes.fromhex(sig))
    open(os.path.join(sortie, "maj.zip"), "wb").write(zb)
    open(os.path.join(sortie, "maj.sig"), "w", encoding="ascii").write(sig)
    json.dump({"version": man["version"], "base_min": man["base_min"], "taille": len(zb),
               "sha256": hashlib.sha256(zb).hexdigest()}, open(os.path.join(sortie, "maj.json"), "w"))
    print(f"Version {man['version']} : {len(man['modules'])} modules, {len(man['ressources'])} pages, "
          f"{len(zb) / 1e3:.0f} ko, signée")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
