"""
Météo Marine Tunisie — télécharge la météo marine (vent, vagues, courant, marée, 16 jours) sur toute
la zone tunisienne et l'affiche dans la carte ACIS MétéoNav (aucun autre logiciel nécessaire).
Accès soumis à un abonnement annuel.

Utilisation :
    MeteoMarineTN.exe            -> fenêtre
    MeteoMarineTN.exe --auto     -> téléchargement silencieux (Planificateur de tâches)
"""
from __future__ import annotations

if __name__ == "__main__":              # mises à jour EN LIGNE : à faire AVANT tous les autres imports
    import maj_en_ligne
    maj_en_ligne.lancer_principal("meteo_marine")   # la version téléchargée prend le relais s'il y en a une

import datetime as dt
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

DEFAUT = {
    "jours": 16, "pas_h": 3, "pas_long": 6, "vent": True, "vagues": True, "fusion": True,
    "garder": 2, "maree": True,
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


def nettoyer_morceaux(dossier: str) -> int:
    """Morceaux laissés par un téléchargement interrompu (fichier unique) : vent, vagues ou courants SEULS.
    Ils ne doivent jamais être pris pour les prévisions."""
    import re
    n = 0
    if not os.path.isdir(dossier):
        return 0
    for nom in os.listdir(dossier):
        if nom.startswith("en_cours_") or re.match(r"Tunisie_(vent|vagues|courants)_\d{8}_\d{4}\.grb2(\.part)?$", nom):
            try:
                os.remove(os.path.join(dossier, nom))
                n += 1
            except OSError:
                pass
    return n


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
            except OSError:      # ex. fichier encore ouvert : on réessaiera la prochaine fois
                pass
    return supprimes


def executer(c: dict, tel: gd.Telechargeur) -> list[str]:
    """Télécharge selon la configuration. Renvoie les fichiers à ouvrir."""
    max_h = int(c["jours"]) * 24
    pas = int(c["pas_h"])
    fusion = c["fusion"] and c["vent"] and c["vagues"]
    pas_long = int(c.get("pas_long", 6))
    vent = gd.modele_gfs(max_h, pas, pas_long) if c["vent"] else None
    vagues = gd.modele_vagues(max_h, pas, "0p25" if fusion else "0p16", pas_long) if c["vagues"] else None
    if fusion:
        nettoyer_morceaux(c["dossier"])
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
    # fichier unique : morceaux sous un nom provisoire (si l'appli est arrêtée en cours de route, un morceau
    # « Tunisie_vent_… » serait pris pour les prévisions : vent seul, sans vagues, pluie ni courants)
    nom = (lambda x: gd.nom_provisoire(x, c["dossier"])) if fusion else (lambda x: gd.nom_fichier(x, c["dossier"], tel.heure))
    if vent:
        produits.append(tel.telecharger(vent, nom("vent"), run=run))
    if vagues:
        produits.append(tel.telecharger(vagues, nom("vagues"), run=run))
    if fusion and c.get("courants", True):
        try:                                      # facultatif : sans courants, on continue normalement
            fc = tel.courants(nom("courants"))
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
def fichiers_meteo(dossier: str) -> list:
    """Fichiers météo téléchargés, du plus récent au plus ancien (date écrite dans le nom = date Internet).
    Les fichiers complets passent avant tout : un morceau « vent seul » ne doit jamais être pris pour la météo.
    (os.listdir et non glob : règle 5, aucun module que les anciens exe n'ont pas)"""
    import re
    complets, autres = [], []
    try:
        noms = os.listdir(dossier)
    except OSError:
        noms = []
    for nom in noms:
        m = re.match(r"Tunisie_\w+?_(\d{8}_\d{4})\.grb2$", nom)
        if m:
            f = os.path.join(dossier, nom)
            (complets if "complet" in nom else autres).append((m.group(1), f))
    return [t[1] for t in sorted(complets, reverse=True)] + [t[1] for t in sorted(autres, reverse=True)]


def dernier_fichier(dossier: str) -> str:
    """Dernier fichier météo téléchargé, ou ""."""
    tous = fichiers_meteo(dossier)
    return tous[0] if tous else ""


def interface() -> None:
    """La carte ACIS MétéoNav est la fenêtre du programme : elle s'ouvre sur la dernière météo, la météo se
    télécharge seule à l'ouverture (bouton « ⬇ Télécharger » toujours là), le nom du client et la validité de
    l'abonnement sont en haut, et tout le reste (téléchargement, abonnement) est dans Paramètres (drapeau).
    Sans aucun fichier météo (1er lancement, fichier abîmé), la carte s'ouvre quand même, vide, et se remplit
    dès que la météo arrive."""
    import tkinter as tk
    import webbrowser
    from tkinter import filedialog, messagebox, ttk

    c = charger_config()
    try:                                # texte net malgré le zoom de Windows (125 %, 150 %…)
        import visionneuse
        visionneuse.activer_dpi()
    except Exception:
        pass
    root = tk.Tk()
    root.withdraw()                     # cachée : la carte est la fenêtre visible
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
                  "carte": None, "journal": [], "fen_act": None, "txt_journal": None,
                  "lbl_abo_param": None}

    # paramètres du programme (gardés même quand la fenêtre Paramètres est fermée)
    v_vent = tk.BooleanVar(value=c["vent"]); v_vag = tk.BooleanVar(value=c["vagues"])
    v_maree = tk.BooleanVar(value=c.get("maree", False))
    v_jours = tk.StringVar(value=str(c["jours"])); v_pas = tk.StringVar(value=str(c["pas_h"]))
    v_dos = tk.StringVar(value=c["dossier"])
    v_garder = tk.BooleanVar(value=int(c.get("garder", 2)) > 0)
    v_auto = tk.BooleanVar(value=c.get("auto_demarrage", True))

    def fenetre():
        """Fenêtre visible du moment (parent des petites fenêtres)."""
        return etat["carte"].win if etat["carte"] is not None else root

    # ---------------- journal, état, progression (affichés dans la carte)
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
        root.after(0, f)

    def progression(i, n):
        def f():
            if etat["carte"] is not None:
                etat["carte"].progression(i, n)
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
                  "Affichage : carte ACIS MétéoNav"):
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
            c.update(vent=v_vent.get(), vagues=v_vag.get(), maree=v_maree.get(), fusion=True,
                     jours=int(v_jours.get()), pas_h=int(v_pas.get()), dossier=v_dos.get(),
                     garder=2 if v_garder.get() else 0, auto_demarrage=v_auto.get())
            for ancien in ("ouvrir", "xygrib", "fermer", "reglages_xygrib"):   # XyGrib n'est plus utilisé
                c.pop(ancien, None)
            sauver_config(c)
        except Exception:
            pass
        return c

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

        titre("Abonnement")
        zoom = max(1.0, float(cadre.winfo_fpixels("1i")) / 96.0)      # zoom de Windows (texte net)
        lbl = tk.Label(cadre, text="", bg=B, font=("Segoe UI", 10, "bold"), anchor="w", justify="left",
                       wraplength=int(420 * zoom))
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
        debut = time.time()
        etape = {"nom": "", "t0": debut}

        def progression_eta(i, n):
            progression(i, n)
            if not etape["nom"]:                       # fichier du robot : avancement en %
                statut(f"Téléchargement des prévisions préparées : {int(100 * i / max(n, 1))} %")
                return
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
                    maj_licence(etat["heure"])
                root.after(0, fin)
        threading.Thread(target=travail, daemon=True).start()

    # ---------------- carte (fenêtre principale, toujours ouverte)
    hote = {"telecharger": lambda: lancer(True), "abonnement": abonnement, "activer": fenetre_activation,
            "parametres": parametres_programme, "quitter": root.destroy}

    def ecarter(f: str):
        """Fichier météo illisible : renommé « .abime » (gardé pour analyse, jamais rouvert)."""
        try:
            os.replace(f, f + ".abime")
        except OSError:
            pass
        log(f"Fichier météo illisible écarté : {os.path.basename(f)}")

    def erreur_carte(e: Exception):
        """La carte elle-même ne s'ouvre pas (cas extrême) : petit message Windows, pas d'autre fenêtre."""
        import traceback
        etat["erreur"] = "".join(traceback.format_exception_only(type(e), e)).strip()[-400:]
        log(f"ACIS MétéoNav : {etat['erreur']}")
        # la fenêtre principale est cachée : Windows cacherait aussi le message -> support transparent, centré
        sl, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"1x1+{sl // 2}+{sh // 3}")
        try:
            root.attributes("-alpha", 0.0)
        except Exception:
            pass
        root.deiconify()
        root.lift()
        try:
            encore = messagebox.askretrycancel("Météo Marine Tunisie",
                                               "La carte ne s'ouvre pas.\n\n« Recommencer » : nouvel essai\n"
                                               "« Annuler » : envoyer le problème à A.C.I.S", parent=root)
            envoyer = (not encore) and messagebox.askyesno(
                "Météo Marine Tunisie", "Envoyer le problème à A.C.I.S par WhatsApp ?\n(client, PC, version et erreur)",
                parent=root)
        finally:
            root.withdraw()
        if encore:
            root.after(100, demarrer_carte)
            return
        if envoyer:
            envoyer_probleme()
        root.after(300, root.destroy)

    def ouvrir_carte(f) -> bool:
        """Ouvre la carte (fichier f, ou vide si f est None). False si la carte elle-même ne s'ouvre pas."""
        try:
            import visionneuse                         # importée ici : un souci de la carte ne bloque pas le reste
            etat["carte"] = visionneuse.ouvrir(f, parent=root, version=version_texte(),
                                               maintenant=etat.get("heure"), hote=hote)
            etat["erreur"] = None
            return True
        except Exception as e:                         # noqa: BLE001
            erreur_carte(e)
            return False

    def afficher(f: str) -> bool:
        """Montre la météo dans la carte. Un fichier abîmé est écarté et le précédent est pris ;
        s'il n'en reste aucun, la carte reste vide et un nouveau téléchargement part tout seul."""
        try:
            import visionneuse
        except Exception as e:                         # noqa: BLE001
            erreur_carte(e)
            return False
        candidats = ([f] if f else []) + [g for g in fichiers_meteo(lire_ui()["dossier"]) if g != f]
        for g in candidats:
            try:
                visionneuse.Donnees(g)                 # le fichier se lit-il ?
            except Exception as e:                     # noqa: BLE001
                log(f"{os.path.basename(g)} : {e}")
                ecarter(g)
                continue
            if etat["carte"] is None:
                return ouvrir_carte(g)
            try:
                etat["carte"].charger(g)
            except Exception as e:                     # noqa: BLE001
                etat["erreur"] = str(e)
                log(f"ACIS MétéoNav : {e}")
                statut("✖ Nouvelle météo non affichée (détails : drapeau → Paramètres → Journal)", "red")
                return False
            etat["reparation"] = False
            return True
        # aucun fichier lisible : carte vide, et un nouveau téléchargement (une seule fois de suite)
        if etat["carte"] is None and not ouvrir_carte(None):
            return False
        if candidats and not etat.get("reparation"):
            etat["reparation"] = True
            statut("Fichier météo abîmé : nouveau téléchargement…", "red")
            root.after(500, lambda: lancer(manuel=False))
        elif candidats:
            statut(f"✖ Météo illisible même après un nouveau téléchargement — contactez {TELEPHONES}", "red")
        return False

    def demarrer_carte():
        afficher(dernier_fichier(lire_ui()["dossier"]))

    def envoyer_probleme():
        """WhatsApp vers A.C.I.S avec un message déjà écrit : client, PC, version, erreur exacte."""
        nom = etat["info"]["nom"] if etat.get("info") else "(abonnement non activé)"
        message = (f"Météo Marine Tunisie — la carte ne s'ouvre pas\nClient : {nom}\n"
                   f"PC : {licence.identifiant_machine()}\nVersion : {version_texte()}\n"
                   f"Erreur : {etat.get('erreur') or 'inconnue'}")
        try:
            webbrowser.open(lien_whatsapp(AGENCES[0][2], message))
            log("Problème envoyé à A.C.I.S par WhatsApp")
        except Exception:                              # noqa: BLE001
            messagebox.showinfo("A.C.I.S", f"Appelez A.C.I.S : {TELEPHONES}\n\n{message}", parent=fenetre())

    # ---------------- démarrage
    f0 = dernier_fichier(c["dossier"])
    demarrer_carte()                                # toujours la carte (vide s'il n'y a pas encore de météo)
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
