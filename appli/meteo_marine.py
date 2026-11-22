"""
Météo Marine Tunisie — télécharge les GRIB (vent + vagues, 16 jours) sur toute
la zone tunisienne et les ouvre dans XyGrib. Accès soumis à un abonnement annuel.

Utilisation :
    MeteoMarineTN.exe            -> fenêtre
    MeteoMarineTN.exe --auto     -> téléchargement silencieux (Planificateur de tâches)
"""
from __future__ import annotations

if __name__ == "__main__":              # mises à jour EN LIGNE : à faire AVANT tous les autres imports
    import maj_en_ligne
    maj_en_ligne.lancer_principal("meteo_marine")   # la version téléchargée prend le relais s'il y en a une

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import grib_download as gd
import heure
import licence
import maj_en_ligne

VERSION = "1.2026"


def version_texte() -> str:
    """« v1.2026 · 19/11 18:00 » : date du code qui tourne (mise à jour en ligne comprise)."""
    v = str(maj_en_ligne.version_active())
    return f"v{VERSION} · {v[6:8]}/{v[4:6]} {v[8:10]}:{v[10:12]}" if len(v) == 12 else f"v{VERSION}"

SIGNATURE = "créé par Claude @ A.C.I.S"

# ---------------------------------------------------------------- VOS COORDONNÉES
# Modifiez ici : elles s'affichent en permanence dans le programme.
MARQUE = "A.C.I.S Marine"
SLOGAN = "Commerce Divers et Services"
# (ville, numéro affiché, numéro WhatsApp = 216 + numéro sans espace)
AGENCES = [
    ("Sfax",    "22 633 254", "21622633254"),
    ("Kélibia", "94 596 447", "21694596447"),
]
TELEPHONES = "  |  ".join(f"{v} : {t}" for v, t, _ in AGENCES)   # « Sfax : 22 633 254  |  Kélibia : 94 596 447 »
BLEU = "#1b265e"
VERT_WA = "#128c4a"
# ---------------------------------------------------------------------------------


def lien_whatsapp(numero: str, message: str = "") -> str:
    from urllib.parse import quote
    return f"https://wa.me/{numero}" + (f"?text={quote(message)}" if message else "")


def ressource(nom: str) -> str:
    """Chemin d'une ressource, y compris dans le .exe PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "ressources", nom)
CONFIG = os.path.join(licence.dossier_app(), "config.json")

CHEMINS_XYGRIB = [
    r"C:\Program Files\XyGrib\XyGrib.exe",
    r"C:\Program Files (x86)\XyGrib\XyGrib.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\XyGrib\XyGrib.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\XyGrib\XyGrib.exe"),
    "/Applications/XyGrib.app/Contents/MacOS/XyGrib",
    "/usr/bin/xygrib", "/usr/local/bin/xygrib",
]

DEFAUT = {
    "jours": 16, "pas_h": 3, "pas_long": 6, "vent": True, "vagues": True, "fusion": True,
    "ouvrir": True, "xygrib": "", "fermer": True, "garder": 2,
    "dossier": os.path.join(os.path.expanduser("~"), "Documents", "MeteoMarineTN"),
}


def charger_config() -> dict:
    c = dict(DEFAUT)
    try:
        c.update(json.load(open(CONFIG, encoding="utf-8")))
    except Exception:
        pass
    return c


def sauver_config(c: dict) -> None:
    json.dump(c, open(CONFIG, "w", encoding="utf-8"), indent=1)


def trouver_xygrib(c: dict) -> str:
    if c.get("xygrib") and os.path.exists(c["xygrib"]):
        return c["xygrib"]
    for p in CHEMINS_XYGRIB:
        if os.path.exists(p):
            return p
    return shutil.which("xygrib") or shutil.which("XyGrib") or ""


def nettoyer_historique(dossier: str, garder: int = 2) -> int:
    """Supprime les anciens fichiers GRIB et garde les `garder` derniers téléchargements.
    Tri d'après la date écrite dans le nom (date Internet), pas l'horloge du PC."""
    import re
    if garder <= 0 or not os.path.isdir(dossier):
        return 0
    lots: dict = {}
    for nom in os.listdir(dossier):
        chemin = os.path.join(dossier, nom)
        if nom.endswith(".part"):
            try:
                os.remove(chemin)
            except OSError:
                pass
            continue
        m = re.match(r"(?:Tunisie_\w+?|Maree)_(\d{8}_\d{4})\.grb2$", nom)
        if m:
            lots.setdefault(m.group(1), []).append(chemin)
    supprimes = 0
    for date in sorted(lots, reverse=True)[garder:]:
        for chemin in lots[date]:
            try:
                os.remove(chemin)
                supprimes += 1
            except OSError:      # ex. fichier encore ouvert dans XyGrib : on réessaiera la prochaine fois
                pass
    return supprimes


def appliquer_reglages_xygrib(c: dict) -> None:
    """Installe les réglages XyGrib d'A.C.I.S (tableau météo : vent, rafales, vagues… ; carte centrée Tunisie ;
    français) dans le xygrib.ini du PC, une fois par version des réglages. Les chemins du PC ne sont pas touchés."""
    try:
        ref = open(ressource("xygrib_reglages.ini"), encoding="utf-8").read()
    except OSError:
        return
    empreinte = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:12]
    if c.get("reglages_xygrib") == empreinte:
        return
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.config")
    ini = os.path.join(base, "openGribs", "XyGrib", "xygrib.ini") if sys.platform == "win32" \
        else os.path.expanduser("~/.config/xygrib.ini")
    nouveaux = dict(l.split("=", 1) for l in ref.splitlines() if "=" in l and not l.startswith("["))
    try:
        lignes = open(ini, encoding="utf-8").read().splitlines() if os.path.exists(ini) else ["[main]"]
        sortie, section, vus = [], None, set()
        for l in lignes:
            if l.startswith("["):
                if section == "[main]":                      # ajoute les clés absentes en fin de section
                    sortie += [f"{k}={v}" for k, v in nouveaux.items() if k not in vus]
                section = l.strip()
            elif section == "[main]" and "=" in l:
                k = l.split("=", 1)[0]
                if k in nouveaux:
                    l = f"{k}={nouveaux[k]}"; vus.add(k)
            sortie.append(l)
        if section == "[main]":
            sortie += [f"{k}={v}" for k, v in nouveaux.items() if k not in vus]
        elif "[main]" not in lignes:
            sortie += ["[main]"] + [f"{k}={v}" for k, v in nouveaux.items()]
        os.makedirs(os.path.dirname(ini), exist_ok=True)
        open(ini, "w", encoding="utf-8").write("\n".join(sortie) + "\n")
        c["reglages_xygrib"] = empreinte
        sauver_config(c)
    except OSError:
        pass


def ouvrir_dans_xygrib(fichier: str, c: dict) -> str:
    appliquer_reglages_xygrib(c)
    exe = trouver_xygrib(c)
    if exe:
        subprocess.Popen([exe, fichier])
        return f"Ouverture dans XyGrib : {os.path.basename(fichier)}"
    if sys.platform == "win32":
        os.startfile(fichier)  # programme associé aux .grb2
        return "XyGrib introuvable : ouverture avec le programme associé aux .grb2"
    return "XyGrib introuvable : indiquez son emplacement dans Paramètres"


def executer(c: dict, tel: gd.Telechargeur) -> list[str]:
    """Télécharge selon la configuration. Renvoie les fichiers à ouvrir."""
    max_h = int(c["jours"]) * 24
    pas = int(c["pas_h"])
    fusion = c["fusion"] and c["vent"] and c["vagues"]
    pas_long = int(c.get("pas_long", 6))
    vent = gd.modele_gfs(max_h, pas, pas_long) if c["vent"] else None
    vagues = gd.modele_vagues(max_h, pas, "0p25" if fusion else "0p16", pas_long) if c["vagues"] else None
    # Pour un fichier unique, vent et vagues doivent venir du même run
    # (les vagues sont publiées un peu après le vent).
    run = None
    robot_frais = False
    if fusion and c.get("robot", True):            # run du robot (10 octets) : s'il est récent, NOAA n'est pas
        rr = gd.run_robot()                        # interrogé du tout (NOAA est parfois très lent à répondre)
        robot_frais = rr is not None and dt.timedelta(0) <= tel.heure - rr <= dt.timedelta(hours=12)
    if fusion and not robot_frais:
        try:                                         # dernier run COMPLET chez NOAA (1 ou 2 petites vérifications)
            run = tel.dernier_run(vagues)
        except Exception as e:
            tel.log(f"Vérification NOAA impossible ({e})")
    if fusion and c.get("robot", True):            # voie rapide : 1 seul fichier préparé par le robot GitHub
        f = gd.fichier_robot(tel, c["dossier"], int(c["jours"]), pas, c.get("courants", True), run_noaa=run,
                             maree=c.get("maree", False))
        if f:
            tel.log(f"Fichier prêt : {f} ({os.path.getsize(f) / 1e6:.1f} Mo)")
            n = nettoyer_historique(c["dossier"], int(c.get("garder", 2)))
            return [f]
    if fusion and run is None:
        run = tel.dernier_run(vagues)
    if run:
        tel.run_publie = dt.datetime.strptime(run[0] + run[1], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    produits = []
    if vent:
        produits.append(tel.telecharger(vent, gd.nom_fichier("vent", c["dossier"], tel.heure), run=run))
    if vagues:
        produits.append(tel.telecharger(vagues, gd.nom_fichier("vagues", c["dossier"], tel.heure), run=run))
    if fusion and c.get("courants", True):
        try:                                      # facultatif : sans courants, on continue normalement
            fc = tel.courants(gd.nom_fichier("courants", c["dossier"], tel.heure))
            if fc:                                # seulement aux heures du vent / des vagues (pas d'image vide)
                heures = set()
                for p in produits:
                    heures |= gd.heures_fichier(open(p, "rb").read())
                with open(fc, "rb") as src:
                    garde = gd.garder_heures(src.read(), heures)
                with open(fc, "wb") as dst:
                    dst.write(garde)
                produits.append(fc)
        except Exception as e:
            tel.log(f"Courants non ajoutés : {e}")
    if c.get("maree", False):
        gd.telecharger_maree(tel, c["dossier"])
    if fusion:
        f = gd.fusionner(produits, gd.nom_fichier("complet", c["dossier"], tel.heure))
        for p in produits:
            os.remove(p)
        if c.get("robot", True):                    # pluie + nuages du robot (même run), comme la voie rapide
            try:
                b = open(f, "rb").read()
                b2 = gd.ajouter_ciel(tel, b, gd._date_ref(b), pas)
                if len(b2) > len(b):
                    with open(f + ".part", "wb") as dst:
                        dst.write(b2)
                    os.replace(f + ".part", f)
            except Exception as e:                # noqa: BLE001
                tel.log(f"Pluie et nuages non ajoutés : {e}")
        tel.log(f"Fichier unique vent + vagues{' + courants' if len(produits) > 2 else ''} : {f} ({os.path.getsize(f) / 1e6:.1f} Mo)")
        produits = [f]
    n = nettoyer_historique(c["dossier"], int(c.get("garder", 2)))
    if n:
        tel.log(f"Historique : {n} ancien(s) fichier(s) supprimé(s), les {c.get('garder', 2)} derniers sont gardés")
    return produits


def controle_licence(heure_internet=None) -> dict:
    cle = licence.cle_enregistree()
    if not cle:
        raise licence.LicenceInvalide("Aucune licence activée")
    return licence.verifier(cle, heure_internet)


# =========================================================================== mode --auto
def mode_auto() -> int:
    c = charger_config()
    journal = open(os.path.join(licence.dossier_app(), "auto.log"), "a", encoding="utf-8")

    def log(m):
        journal.write(m + "\n"); journal.flush()
    try:
        h = heure.heure_internet()
        controle_licence(h)
        executer(c, gd.Telechargeur(h, log=log))
        return 0
    except Exception as e:
        log(f"ERREUR : {e}")
        return 1
    finally:
        maj_en_ligne.verifier_en_ligne(log=log)      # nouvelle version du programme ? (prochain lancement)


# =========================================================================== installation
DOSSIER_INSTALL = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                               "Programs", "MeteoMarineTN")


def auto_installation() -> bool:
    """Au 1er lancement du .exe (n'importe où : clé USB, Téléchargements, WhatsApp…),
    il se copie dans le PC, crée les raccourcis Bureau + menu Démarrer, puis se relance.
    Renvoie True si le programme doit s'arrêter (la copie installée a pris le relais)."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False) or "--portable" in sys.argv:
        return False
    cible = os.path.join(DOSSIER_INSTALL, "MeteoMarineTN.exe")
    ici = os.path.abspath(sys.executable)
    if os.path.normcase(ici) == os.path.normcase(cible):
        return False
    def copier():
        os.makedirs(DOSSIER_INSTALL, exist_ok=True)
        shutil.copy2(ici, cible)
    try:
        copier()
    except OSError:
        # l'ancienne version est sans doute ouverte (fichier verrouillé) : on la ferme, sans nous fermer
        # nous-mêmes (un .exe PyInstaller = 2 processus : nous et notre lanceur), puis on réessaie
        try:
            subprocess.run(["taskkill", "/F", "/IM", "MeteoMarineTN.exe",
                            "/FI", f"PID ne {os.getpid()}", "/FI", f"PID ne {os.getppid()}"],
                           creationflags=0x08000000, timeout=20, capture_output=True)
        except Exception:
            pass
        for _ in range(12):
            time.sleep(0.5)
            try:
                copier()
                break
            except OSError:
                continue
        else:
            return False  # vraiment impossible : on continue depuis ici
    ps = (
        "$w=New-Object -ComObject WScript.Shell;"
        "foreach($d in @([Environment]::GetFolderPath('Desktop'),"
        "[Environment]::GetFolderPath('Programs'))){"
        "$s=$w.CreateShortcut((Join-Path $d 'Météo Marine Tunisie.lnk'));"
        f"$s.TargetPath='{cible}';$s.WorkingDirectory='{DOSSIER_INSTALL}';"
        f"$s.IconLocation='{cible},0';$s.Description='Météo Marine Tunisie — {MARQUE}';$s.Save()}}"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       creationflags=0x08000000, timeout=30)  # sans fenêtre
    except Exception:
        pass
    subprocess.Popen([cible, "--installe"], cwd=DOSSIER_INSTALL)
    return True


# =========================================================================== interface
def dernier_fichier(dossier: str) -> str:
    """Dernier fichier météo téléchargé (date écrite dans le nom = date Internet), ou ""."""
    import glob
    import re
    tous = []
    for f in glob.glob(os.path.join(dossier, "Tunisie_*.grb2")):
        m = re.search(r"_(\d{8}_\d{4})\.grb2$", f)
        if m:
            tous.append((m.group(1), "complet" in os.path.basename(f), f))
    return max(tous)[2] if tous else ""


def interface() -> None:
    """La carte ACIS MétéoNav est la fenêtre du programme : elle s'ouvre sur la dernière météo, la météo se
    télécharge seule à l'ouverture (bouton « ⬇ Télécharger » toujours là), le nom du client et la validité de
    l'abonnement sont en haut, et tout le reste (téléchargement, XyGrib, abonnement) est dans Paramètres (drapeau).
    Sans aucun fichier météo (1er lancement), un petit écran d'attente la remplace jusqu'au 1er téléchargement."""
    import tkinter as tk
    import webbrowser
    from tkinter import filedialog, messagebox, ttk

    c = charger_config()
    root = tk.Tk()
    root.withdraw()                     # cachée : la carte (ou l'écran d'attente) est la fenêtre visible
    root.title(f"Météo Marine Tunisie — {MARQUE}")
    try:
        root.iconbitmap(default=ressource("icone.ico"))
    except Exception:
        pass
    try:
        logo = tk.PhotoImage(file=ressource("logo_96.png"))
        root.iconphoto(True, logo); root._logo = logo
    except Exception:
        pass
    etat: dict = {"heure": None, "tel": None, "derniers": [], "info": None, "en_cours": False,
                  "carte": None, "attente": None, "journal": [], "fen_act": None, "txt_journal": None,
                  "lbl_abo_param": None}

    # paramètres du programme (gardés même quand la fenêtre Paramètres est fermée)
    v_vent = tk.BooleanVar(value=c["vent"]); v_vag = tk.BooleanVar(value=c["vagues"])
    v_maree = tk.BooleanVar(value=c.get("maree", False))
    v_ouv = tk.BooleanVar(value=c["ouvrir"])
    v_jours = tk.StringVar(value=str(c["jours"])); v_pas = tk.StringVar(value=str(c["pas_h"]))
    v_dos = tk.StringVar(value=c["dossier"]); v_xy = tk.StringVar(value=trouver_xygrib(c))
    v_garder = tk.BooleanVar(value=int(c.get("garder", 2)) > 0)
    v_auto = tk.BooleanVar(value=c.get("auto_demarrage", True))

    def fenetre():
        """Fenêtre visible du moment (parent des petites fenêtres)."""
        if etat["carte"] is not None:
            return etat["carte"].win
        if etat["attente"] is not None:
            return etat["attente"]["win"]
        return root

    # ---------------- journal, état, progression (affichés dans la carte ou l'écran d'attente)
    def log(msg):
        def f():
            etat["journal"].append(msg)
            del etat["journal"][:-300]
            t = etat.get("txt_journal")
            try:
                if t is not None and t.winfo_exists():
                    t.config(state="normal"); t.insert("end", msg + "\n"); t.see("end"); t.config(state="disabled")
            except Exception:
                pass
        root.after(0, f)

    COULEURS = {"green": ("#7CFC9A", "green"), "red": ("#ff8a80", "red")}   # (sur fond bleu, sur fond clair)

    def statut(msg, couleur=None):
        sombre, clair = COULEURS.get(couleur, ("#ffd166", "#333"))

        def f():
            if etat["carte"] is not None:
                etat["carte"].statut(msg, sombre)
            elif etat["attente"] is not None:
                etat["attente"]["statut"].config(text=msg, fg=clair)
        root.after(0, f)

    def progression(i, n):
        def f():
            if etat["carte"] is not None:
                etat["carte"].progression(i, n)
            elif etat["attente"] is not None:
                etat["attente"]["barre"].config(maximum=max(n, 1), value=i)
        root.after(0, f)

    # ---------------- licence
    def abonnement():
        """(nom, détail, couleur) affichés en haut de la carte."""
        info = etat["info"]
        if info:
            fin = f"{info['fin'][8:10]}/{info['fin'][5:7]}/{info['fin'][:4]}"
            j = info.get("jours_restants")
            detail = f"abonnement jusqu'au {fin}" + (f" ({j} j)" if j is not None else "")
            return info["nom"], detail, "#7CFC9A" if j is None or j > 30 else "#ffd166"
        return etat.get("abo_titre", "Abonnement non activé"), "cliquez ici pour l'activer", "#ff8a80"

    def maj_licence(h=None) -> bool:
        try:
            etat["info"] = controle_licence(h)
            ok = True
        except licence.LicenceInvalide as e:
            etat["info"] = None
            etat["abo_titre"] = "Abonnement non activé" if "Aucune" in str(e) else "Abonnement à renouveler"
            etat["abo_erreur"] = str(e)
            ok = False
        nom, detail, _coul = abonnement()
        if etat["carte"] is not None:
            etat["carte"].maj_abonnement()
        if etat["attente"] is not None:
            etat["attente"]["abo"].config(text=f"{nom}\n{detail}", fg="green" if ok else "red")
        lbl = etat.get("lbl_abo_param")
        try:
            if lbl is not None and lbl.winfo_exists():
                lbl.config(text=f"{nom} — {detail}" + ("" if ok else f"\n{etat.get('abo_erreur', '')}"),
                           fg="green" if ok else "red")
        except Exception:
            pass
        return ok

    def _reussite():
        maj_licence(etat.get("heure"))
        if etat.get("fen_act") is not None:
            try:
                etat["fen_act"].destroy()
            except Exception:
                pass
            etat["fen_act"] = None
        statut("✔ Abonnement activé", "green")
        if not etat["derniers"] and v_auto.get():      # 1re activation : la météo arrive tout de suite
            lancer(manuel=False)

    def activer_code(nom: str, code: str, parent=None) -> bool:
        """Code préparé (10 caractères) : vérifié, lié à ce PC, puis enregistré chiffré."""
        try:
            scelle = licence.sceller(nom, code)
            licence.verifier(scelle, etat.get("heure"))
        except licence.LicenceInvalide as e:
            messagebox.showerror("Activation", str(e), parent=parent)
            return False
        licence.enregistrer(scelle)
        _reussite()
        return True

    def fenetre_activation():
        if etat.get("fen_act") is not None:
            etat["fen_act"].lift(); return
        w = tk.Toplevel(root); etat["fen_act"] = w
        w.title("Activer l'abonnement"); w.transient(fenetre()); w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", lambda: (w.destroy(), etat.update(fen_act=None)))
        F = ("Segoe UI", 11)
        tk.Label(w, text=MARQUE, font=("Segoe UI", 15, "bold"), fg=BLEU).pack(pady=(12, 0))
        tk.Label(w, text="Activation par le technicien", font=("Segoe UI", 10), fg="#555").pack()

        tk.Label(w, text="Nom du client (ou du bateau) :", font=F).pack(anchor="w", padx=18, pady=(14, 2))
        v_nom = tk.StringVar()
        e_nom = ttk.Entry(w, textvariable=v_nom, font=("Segoe UI", 13), width=30); e_nom.pack(padx=18, fill="x")
        e_nom.focus_set()

        tk.Label(w, text="Code d'activation :", font=F).pack(anchor="w", padx=18, pady=(12, 2))
        v_code = tk.StringVar()
        ligne = tk.Frame(w); ligne.pack(padx=18, fill="x")
        e_code = ttk.Entry(ligne, textvariable=v_code, font=("Consolas", 18), show="•", justify="center")
        e_code.pack(side="left", fill="x", expand=True)
        voir = {"on": False}

        def basculer():   # œil : montre le code le temps de vérifier la saisie (technicien)
            voir["on"] = not voir["on"]
            e_code.config(show="" if voir["on"] else "•")
        ttk.Button(ligne, text="Voir", width=5, command=basculer).pack(side="left", padx=(6, 0))
        lbl_coller = tk.Label(w, text="", font=("Segoe UI", 9), fg="#c0392b")

        def coller_code(*_):   # code copié dans le générateur (même PC) -> case du code
            import presse_papiers
            brut = presse_papiers.coller(w).upper()
            t = "".join(ch for ch in brut if ch.isalnum() or ch == "-")[:14]
            if t:
                v_code.set(t); lbl_coller.config(text=""); e_code.icursor("end"); e_code.focus_set()
            else:
                lbl_coller.config(text="Presse-papiers vide : créez ou recopiez d'abord le code dans le générateur.")
            return "break"
        ttk.Button(ligne, text="📋 Coller", command=coller_code).pack(side="left", padx=(6, 0))
        lbl_coller.pack(padx=18, anchor="w")
        e_code.bind("<Button-3>", coller_code)           # clic droit dans la case = coller
        try:
            import presse_papiers
            presse_papiers.menu_clic_droit(e_nom)
        except Exception:
            pass

        def valider(*_):
            nom = v_nom.get().strip()
            if not nom:
                messagebox.showwarning("Activation", "Écrivez le nom du client.", parent=w)
                e_nom.focus_set(); return
            if activer_code(nom, v_code.get(), parent=w):
                v_code.set("")
        e_code.bind("<Return>", valider)
        e_nom.bind("<Return>", lambda e: e_code.focus_set())
        tk.Button(w, text="Activer", command=valider, bg=BLEU, fg="white", font=("Segoe UI", 12, "bold"),
                  relief="flat", padx=36, pady=6, cursor="hand2").pack(pady=(16, 6))
        tk.Label(w, text=f"Pas de code ? {TELEPHONES}", font=("Segoe UI", 9), fg="#666").pack(pady=(0, 12))

    def a_propos(_e=None):
        a = tk.Toplevel(root); a.title("À propos"); a.transient(fenetre()); a.resizable(False, False)
        cad = tk.Frame(a, bg="white"); cad.pack(fill="both", expand=True)
        try:
            try:
                img = tk.PhotoImage(file=ressource("logo_144.png"))
            except Exception:
                img = tk.PhotoImage(file=ressource("logo_96.png"))
            a._img = img
            tk.Label(cad, image=img, bg="white").pack(pady=(18, 4))
        except Exception:
            pass
        tk.Label(cad, text="Météo Marine Tunisie", font=("Segoe UI", 16, "bold"), fg=BLEU, bg="white").pack()
        tk.Label(cad, text=f"Version {version_texte()[1:]}", font=("Segoe UI", 10), fg="#555", bg="white").pack()
        tk.Frame(cad, height=2, width=260, bg=BLEU).pack(pady=10)
        for t in ("Prévisions vent, vagues, courant et marée sur 16 jours",
                  "Côtes et haute mer tunisiennes (30°N–39°N / 7°E–14°E)",
                  "Données : NOAA GFS 0.25° et GFS-Wave · Copernicus Marine",
                  "Affichage : ACIS MétéoNav (et XyGrib)"):
            tk.Label(cad, text=t, font=("Segoe UI", 9), fg="#333", bg="white").pack()
        tk.Label(cad, text=SIGNATURE[0].upper() + SIGNATURE[1:], font=("Segoe UI", 10, "italic"),
                 fg=BLEU, bg="white").pack(pady=(12, 0))
        tk.Label(cad, text=f"{MARQUE} — {SLOGAN}", font=("Segoe UI", 10, "bold"), fg=BLEU, bg="white").pack()
        tk.Label(cad, text=TELEPHONES, font=("Segoe UI", 10, "bold"), fg=VERT_WA, bg="white").pack(pady=(2, 0))
        tk.Label(cad, text=f"© 2026 {MARQUE} — Tous droits réservés", font=("Segoe UI", 8),
                 fg="#888", bg="white").pack(pady=(10, 4))
        tk.Button(cad, text="Fermer", command=a.destroy, bg=BLEU, fg="white", relief="flat",
                  font=("Segoe UI", 10, "bold"), padx=24, pady=3).pack(pady=(4, 16))

    # ---------------- réglages
    def lire_ui() -> dict:
        try:
            c.update(vent=v_vent.get(), vagues=v_vag.get(), maree=v_maree.get(), ouvrir=v_ouv.get(), fusion=True,
                     jours=int(v_jours.get()), pas_h=int(v_pas.get()), dossier=v_dos.get(), xygrib=v_xy.get(),
                     garder=2 if v_garder.get() else 0, auto_demarrage=v_auto.get(), fermer=False)
            sauver_config(c)
        except Exception:
            pass
        return c

    def ouvrir_dernier(parent=None):
        cfg = lire_ui()
        fichiers = etat["derniers"] or ([dernier_fichier(cfg["dossier"])] if dernier_fichier(cfg["dossier"]) else [])
        if not fichiers:
            f = filedialog.askopenfilename(initialdir=cfg["dossier"], parent=parent or fenetre(),
                                           filetypes=[("GRIB", "*.grb2 *.grb *.grib2")])
            fichiers = [f] if f else []
        for f in fichiers:
            log(ouvrir_dans_xygrib(f, cfg))

    def parametres_programme(cadre, fen):
        """Ancien menu de la première page, dans la fenêtre Paramètres de la carte (colonne de droite)."""
        B = cadre.cget("bg")
        opt = dict(bg=B, font=("Segoe UI", 10), anchor="w", activebackground=B)

        def titre(t, haut=10):
            tk.Label(cadre, text=t, bg=B, fg=BLEU, font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", pady=(haut, 2))

        def ligne():
            fr = tk.Frame(cadre, bg=B); fr.pack(fill="x", pady=1); return fr
        titre("Téléchargement", 0)
        l1 = ligne()
        for t, v in (("Vent + rafales", v_vent), ("Vagues", v_vag), ("Marée", v_maree)):
            tk.Checkbutton(l1, text=t, variable=v, command=lire_ui, **opt).pack(side="left", padx=(0, 8))
        l2 = ligne()
        tk.Label(l2, text="Durée (jours) :", **opt).pack(side="left")
        cb = ttk.Combobox(l2, textvariable=v_jours, values=["3", "5", "7", "10", "16"], width=4, state="readonly")
        cb.pack(side="left", padx=(4, 14)); cb.bind("<<ComboboxSelected>>", lambda e: lire_ui())
        tk.Label(l2, text="Pas 5 premiers jours (h) :", **opt).pack(side="left")
        cb = ttk.Combobox(l2, textvariable=v_pas, values=["1", "3", "6"], width=3, state="readonly")
        cb.pack(side="left", padx=4); cb.bind("<<ComboboxSelected>>", lambda e: lire_ui())
        tk.Checkbutton(cadre, text="Télécharger la météo à l'ouverture du programme", variable=v_auto,
                       command=lire_ui, **opt).pack(fill="x")
        tk.Checkbutton(cadre, text="Effacer l'historique (garder les 2 derniers téléchargements)",
                       variable=v_garder, command=lire_ui, **opt).pack(fill="x")
        l3 = ligne()
        tk.Label(l3, text="Dossier :", **opt).pack(side="left")
        ttk.Entry(l3, textvariable=v_dos, width=36).pack(side="left", fill="x", expand=True, padx=4)

        def choisir_dossier():
            d = filedialog.askdirectory(initialdir=v_dos.get(), parent=fen)
            if d: v_dos.set(d); lire_ui()
        ttk.Button(l3, text="…", width=3, command=choisir_dossier).pack(side="left")

        titre("XyGrib")
        l4 = ligne()
        tk.Label(l4, text="XyGrib :", **opt).pack(side="left")
        ttk.Entry(l4, textvariable=v_xy, width=36).pack(side="left", fill="x", expand=True, padx=4)

        def choisir_xy():
            f = filedialog.askopenfilename(title="Emplacement de XyGrib.exe", parent=fen)
            if f: v_xy.set(f); lire_ui()
        ttk.Button(l4, text="…", width=3, command=choisir_xy).pack(side="left")
        l5 = ligne()
        tk.Checkbutton(l5, text="Ouvrir aussi dans XyGrib après « Télécharger »", variable=v_ouv,
                       command=lire_ui, **opt).pack(side="left")
        ttk.Button(cadre, text="Ouvrir la météo dans XyGrib", command=lambda: ouvrir_dernier(fen)).pack(anchor="w", pady=(2, 0))

        titre("Abonnement")
        lbl = tk.Label(cadre, text="", bg=B, font=("Segoe UI", 10, "bold"), anchor="w", justify="left", wraplength=420)
        lbl.pack(fill="x")
        etat["lbl_abo_param"] = lbl
        l6 = ligne()
        ttk.Button(l6, text="Activer / renouveler", command=fenetre_activation).pack(side="left")
        ttk.Button(l6, text="À propos", command=a_propos).pack(side="left", padx=6)
        tk.Label(cadre, text=f"Identifiant de ce PC : {licence.identifiant_machine()}", bg=B,
                 font=("Consolas", 9), anchor="w").pack(fill="x", pady=(4, 0))
        for ville, tel, num in AGENCES:
            wa = tk.Label(cadre, text=f"✆ {ville} : {tel}  (WhatsApp)", bg=B, font=("Segoe UI", 9, "bold"),
                          fg=VERT_WA, cursor="hand2", anchor="w")
            wa.pack(fill="x")
            wa.bind("<Button-1>", lambda e, n=num: webbrowser.open(lien_whatsapp(n)))

        titre("Journal")
        journal = tk.Text(cadre, height=5, width=56, font=("Consolas", 8))
        journal.pack(fill="x")
        journal.insert("end", "\n".join(etat["journal"]) + ("\n" if etat["journal"] else ""))
        journal.see("end"); journal.config(state="disabled")
        etat["txt_journal"] = journal
        cadre.bind("<Destroy>", lambda e: lire_ui() if e.widget is cadre else None)
        maj_licence(etat.get("heure"))

    # ---------------- téléchargement
    def lancer(manuel: bool = True):
        if etat["en_cours"]:
            return
        cfg = lire_ui()
        if not (cfg["vent"] or cfg["vagues"]):
            messagebox.showwarning("Météo Marine", "Cochez au moins vent ou vagues (drapeau → Paramètres).",
                                   parent=fenetre()); return
        if not maj_licence(etat.get("heure")):
            fenetre_activation(); return
        etat["en_cours"] = True
        if etat["carte"] is not None:
            etat["carte"].en_cours(True)
        if etat["attente"] is not None:
            etat["attente"]["btn"].config(state="disabled", text="  TÉLÉCHARGEMENT…")
        debut = time.time()
        etape = {"nom": "", "t0": debut}

        def progression_eta(i, n):
            progression(i, n)
            ecoule = time.time() - etape["t0"]
            reste = ecoule / max(i, 1) * (n - i)
            statut(f"{etape['nom'] or 'Téléchargement'} : {i}/{n} — encore environ {int(reste) + 1} s")

        def travail():
            try:
                statut("Vérification de la date et de l'abonnement…")
                h = heure.heure_internet()
                etat["heure"] = h
                root.after(0, lambda: etat["carte"] is not None and etat["carte"].regler_heure(h))
                controle_licence(h)
                statut("Recherche des dernières prévisions…")
                tel = gd.Telechargeur(h, log=log, progression=progression_eta); etat["tel"] = tel
                ancien_log = tel.log

                def log2(m):
                    ancien_log(m)
                    if m.startswith("Fichier météo préparé"):
                        statut("Téléchargement des prévisions préparées…")
                    elif m.startswith(("Courants", "Marée")):
                        statut("Téléchargement des courants et de la marée…")
                    if m.startswith(("Vent", "Vagues")):
                        etape["nom"] = m.split(" :")[0].split(" (")[0]
                        etape["t0"] = time.time()
                tel.log = log2
                fichiers = executer(cfg, tel)
                etat["derniers"] = fichiers
                rp = getattr(tel, "run_publie", None)
                info_run = (f" · NOAA du {rp:%d/%m} {(rp.hour + 1) % 24:02d} h" if rp else "")
                statut(f"✔ Météo à jour{info_run}", "green")
                if fichiers:
                    root.after(0, lambda: afficher(fichiers[-1]))
                if manuel and cfg["ouvrir"] and trouver_xygrib(cfg):
                    for f in fichiers:
                        log(ouvrir_dans_xygrib(f, cfg))
            except licence.LicenceInvalide as e:
                statut(f"Licence : {e}", "red")
            except Exception as e:
                log(f"ERREUR : {e}")
                statut("✖ Échec du téléchargement : vérifiez la connexion Internet (détails : drapeau → Paramètres)",
                       "red")
            finally:
                etat["en_cours"] = False

                def fin():
                    if etat["carte"] is not None:
                        etat["carte"].en_cours(False)
                    if etat["attente"] is not None:
                        etat["attente"]["btn"].config(state="normal", text="  TÉLÉCHARGER LA MÉTÉO")
                    maj_licence(etat["heure"])
                root.after(0, fin)
        threading.Thread(target=travail, daemon=True).start()

    # ---------------- carte (fenêtre principale) et écran d'attente
    hote = {"telecharger": lambda: lancer(True), "abonnement": abonnement, "activer": fenetre_activation,
            "parametres": parametres_programme, "quitter": root.destroy}

    def afficher(f: str) -> bool:
        """Montre le fichier météo dans la carte (ouverte si besoin)."""
        if etat["carte"] is not None:
            try:
                etat["carte"].charger(f)
                return True
            except Exception as e:                     # noqa: BLE001
                log(f"ACIS MétéoNav : {e}")
                statut("✖ Fichier météo illisible", "red")
                return False
        try:
            import visionneuse                         # importée ici : un souci de la carte ne bloque pas le reste
            v = visionneuse.ouvrir(f, parent=root, version=version_texte(), maintenant=etat.get("heure"), hote=hote)
        except Exception as e:                         # noqa: BLE001
            log(f"ACIS MétéoNav : {e}")
            if etat["attente"] is None:
                ecran_attente()
            statut("✖ Carte indisponible : drapeau → Paramètres → « Ouvrir la météo dans XyGrib »", "red")
            return False
        etat["carte"] = v
        if etat["attente"] is not None:
            etat["attente"]["win"].destroy()
            etat["attente"] = None
        return True

    def ecran_attente():
        """1er lancement (aucun fichier météo) : en attendant le 1er téléchargement."""
        w = tk.Toplevel(root)
        w.title(f"Météo Marine Tunisie — {MARQUE}")
        w.protocol("WM_DELETE_WINDOW", root.destroy)
        sl, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        haut = min(470, sh - 120)
        w.geometry(f"560x{haut}+{max(0, (sl - 560) // 2)}+{max(0, (sh - 70 - haut) // 2)}")
        pied = tk.Frame(w, bg=BLEU); pied.pack(side="bottom", fill="x")
        tk.Label(pied, text=f"{MARQUE}  •  {TELEPHONES}", bg=BLEU, fg="white",
                 font=("Segoe UI", 9, "bold")).pack(pady=4)
        try:
            try:
                img = tk.PhotoImage(file=ressource("logo_144.png"))
            except Exception:
                img = tk.PhotoImage(file=ressource("logo_96.png"))
            w._img = img
            tk.Label(w, image=img).pack(pady=(14, 2))
        except Exception:
            tk.Label(w, text=MARQUE.upper(), font=("Segoe UI", 18, "bold"), fg=BLEU).pack(pady=(20, 2))
        tk.Label(w, text="ACIS MétéoNav — Météo Marine Tunisie", font=("Segoe UI", 11, "bold"), fg=BLEU).pack()
        abo = tk.Label(w, text="", font=("Segoe UI", 10, "bold"), justify="center")
        abo.pack(pady=(8, 6))
        btn = tk.Button(w, text="  TÉLÉCHARGER LA MÉTÉO", command=lambda: lancer(True), font=("Segoe UI", 15, "bold"),
                        bg=BLEU, fg="white", activebackground="#2c3a85", activeforeground="white",
                        disabledforeground="#c8c8c8", relief="flat", padx=18, pady=7, cursor="hand2")

        btn.pack(pady=4)
        st = tk.Label(w, text="", font=("Segoe UI", 10), fg="#333", wraplength=520, justify="center")
        st.pack(pady=(6, 2))
        barre = ttk.Progressbar(w, mode="determinate", length=380)
        barre.pack(pady=2)
        act = tk.Frame(w); act.pack(pady=8)
        ttk.Button(act, text="Activer / renouveler", command=fenetre_activation).pack(side="left", padx=4)

        def parametres():
            p = tk.Toplevel(w); p.title("Paramètres — Météo Marine Tunisie"); p.transient(w); p.resizable(False, False)
            cad = tk.Frame(p, bg="#f7f8fc", padx=14, pady=10); cad.pack(fill="both", expand=True)
            parametres_programme(cad, p)
            tk.Button(p, text="✔ Fermer", command=p.destroy, bg=BLEU, fg="white", relief="flat",
                      font=("Segoe UI", 11, "bold"), pady=4).pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(act, text="⚙ Paramètres", command=parametres).pack(side="left", padx=4)
        etat["attente"] = {"win": w, "abo": abo, "btn": btn, "statut": st, "barre": barre}

    # ---------------- démarrage
    f0 = dernier_fichier(c["dossier"])
    if not (f0 and afficher(f0)) and etat["attente"] is None:
        ecran_attente()
    if not maj_licence():
        root.after(300, fenetre_activation)
    if "--installe" in sys.argv:
        statut("✔ Installé : raccourci « Météo Marine Tunisie » créé sur le Bureau", "green")
    elif not f0:
        statut("Préparation du premier téléchargement…")

    def heure_au_demarrage():
        try:
            h = heure.heure_internet()
            etat["heure"] = h

            def suite():
                if etat["carte"] is not None:
                    etat["carte"].regler_heure(h)
                if maj_licence(h) and v_auto.get():        # la météo se met à jour seule à l'ouverture
                    lancer(manuel=False)
            root.after(0, suite)
        except Exception:
            log("Pas de connexion Internet : la licence sera vérifiée au téléchargement.")
            statut("Pas de connexion Internet : météo du dernier téléchargement", "red")
    threading.Thread(target=heure_au_demarrage, daemon=True).start()
    maj_en_ligne.lancer_en_fond(log=log)          # nouvelle version du programme ? (prochain lancement)
    root.mainloop()


def principal() -> int:
    if "--auto" in sys.argv:
        return mode_auto()
    if auto_installation():
        return 0
    interface()
    return 0


if __name__ == "__main__":
    sys.exit(principal())
