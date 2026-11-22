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
def interface() -> None:
    import re
    import tkinter as tk
    import webbrowser
    from tkinter import filedialog, messagebox, ttk

    c = charger_config()
    root = tk.Tk()
    root.title(f"Météo Marine Tunisie — {MARQUE}")
    try:
        root.iconbitmap(default=ressource("icone.ico"))
    except Exception:
        pass
    # tient sur les vieux écrans 1366x768 (barre des tâches comprise)
    # taille ET position : centrée au-dessus de la barre des tâches (écrans 1366x768, zoom Windows 125 %)
    sl, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    haut = min(520, sh - 120)
    root.geometry(f"560x{haut}+{max(0, (sl - 560) // 2)}+{max(0, (sh - 70 - haut) // 2)}")
    root.minsize(500, 440)
    etat: dict = {"heure": None, "tel": None, "derniers": [], "info": None, "en_cours": False}

    # ---------------- bas : coordonnées toujours visibles
    pied = tk.Frame(root, bg=BLEU); pied.pack(side="bottom", fill="x")
    tk.Label(pied, text=f"{MARQUE}  •  {SLOGAN}", bg=BLEU, fg="white",
             font=("Segoe UI", 9, "bold")).pack(side="left", padx=8, pady=4)
    for ville, tel, num in reversed(AGENCES):
        lbl = tk.Label(pied, text=f"{ville} : {tel}", bg=BLEU, fg="#7CFC9A",
                       font=("Segoe UI", 9, "bold"), cursor="hand2")
        lbl.pack(side="right", padx=6)
        lbl.bind("<Button-1>", lambda e, n=num: webbrowser.open(lien_whatsapp(n)))

    # ---------------- en-tête
    entete = tk.Frame(root); entete.pack(fill="x", padx=8, pady=(6, 0))
    try:
        logo = tk.PhotoImage(file=ressource("logo_96.png"))
        root.iconphoto(True, logo); root._logo = logo
        try:
            grand = tk.PhotoImage(file=ressource("logo_144.png"))         # 144 px : numéro lisible
        except Exception:
            grand = logo
        root._logo_grand = grand
        tk.Label(entete, image=grand).pack(side="left")
    except Exception:
        pass
    titres = tk.Frame(entete); titres.pack(side="left", padx=8)
    tk.Label(titres, text=MARQUE.upper(), font=("Segoe UI", 16, "bold"), fg=BLEU).pack(anchor="w")
    tk.Label(titres, text="Météo Marine Tunisie", font=("Segoe UI", 9), fg=BLEU).pack(anchor="w")
    for ville, tel, num in AGENCES:
        wa = tk.Label(titres, text=f"✆ {ville} : {tel}  (WhatsApp)", font=("Segoe UI", 9, "bold"),
                      fg=VERT_WA, cursor="hand2")
        wa.pack(anchor="w")
        wa.bind("<Button-1>", lambda e, n=num: webbrowser.open(lien_whatsapp(n)))
    ttk.Separator(root).pack(fill="x", padx=8, pady=5)

    # ---------------- centre : logo en filigrane + nom + grand bouton
    FOND = "#f0f0f0"
    centre = tk.Canvas(root, bg=FOND, highlightthickness=0, height=280)
    centre.pack(fill="both", expand=True)
    try:
        img_fond = tk.PhotoImage(file=ressource("fond.png")).zoom(3).subsample(4); root._img_fond = img_fond
        id_fond = centre.create_image(0, 0, image=img_fond)
    except Exception:
        id_fond = None
    id_nom = centre.create_text(0, 0, text="", font=("Segoe UI", 19, "bold"), fill=BLEU, justify="center")
    id_val = centre.create_text(0, 0, text="", font=("Segoe UI", 9), fill="green", justify="center")
    try:
        img_btn = tk.PhotoImage(file=ressource("bouton_64.png")).zoom(2).subsample(3); root._img_btn = img_btn
    except Exception:
        img_btn = None
    btn_dl = tk.Button(centre, text="  TÉLÉCHARGER LA MÉTÉO", image=img_btn, compound="left",
                       font=("Segoe UI", 15, "bold"), bg=BLEU, fg="white", activebackground="#2c3a85",
                       activeforeground="white", disabledforeground="#c8c8c8", relief="flat",
                       padx=18, pady=7, cursor="hand2")
    id_btn = centre.create_window(0, 0, window=btn_dl)
    id_etat = centre.create_text(0, 0, text="", font=("Segoe UI", 10), fill="#333", justify="center")
    prog = ttk.Progressbar(centre, mode="determinate", length=380)
    id_prog = centre.create_window(0, 0, window=prog)
    actions = tk.Frame(centre, bg=FOND)
    id_act = centre.create_window(0, 0, window=actions)
    id_sign = centre.create_text(0, 0, text=f"Météo Marine Tunisie  ·  {version_texte()}  ·  {SIGNATURE}",
                                 font=("Segoe UI", 8, "italic"), fill="#8a90a8")
    btn_carte = ttk.Button(actions, text="ACIS MétéoNav")
    btn_carte.pack(side="left", padx=4)
    btn_ouvrir = ttk.Button(actions, text="Ouvrir dans XyGrib")
    btn_ouvrir.pack(side="left", padx=4)
    btn_act = ttk.Button(actions, text="Activer / renouveler")
    btn_act.pack(side="left", padx=4)
    btn_par = ttk.Button(actions, text="⚙ Paramètres")
    btn_par.pack(side="left", padx=4)

    def placer(_e=None):
        lx = centre.winfo_width() / 2
        hy = centre.winfo_height()
        if id_fond:
            centre.coords(id_fond, lx, hy / 2)
        y = max(8, (hy - 262) / 2)
        centre.coords(id_nom, lx, y + 16)
        centre.coords(id_val, lx, y + 48)
        centre.coords(id_btn, lx, y + 102)
        centre.coords(id_etat, lx, y + 156)
        centre.coords(id_prog, lx, y + 180)
        centre.coords(id_act, lx, y + 218)
        centre.coords(id_sign, centre.winfo_width() - 10, hy - 10)
        centre.itemconfig(id_sign, anchor="se")
    centre.bind("<Configure>", placer)

    class _Texte:  # petit adaptateur : même usage qu'un Label
        def __init__(self, ident):
            self.ident = ident

        def config(self, text=None, fg=None):
            if text is not None:
                centre.itemconfig(self.ident, text=text)
            if fg is not None:
                centre.itemconfig(self.ident, fill=fg)
    lbl_nom, lbl_validite, lbl_etat = _Texte(id_nom), _Texte(id_val), _Texte(id_etat)

    # ---------------- paramètres (repliés)
    cadre = ttk.LabelFrame(root, text="Paramètres — zone 30°N–39°N / 7°E–14°E (côtes + haute mer)")
    v_vent = tk.BooleanVar(value=c["vent"]); v_vag = tk.BooleanVar(value=c["vagues"])
    v_ouv = tk.BooleanVar(value=c["ouvrir"])
    v_jours = tk.StringVar(value=str(c["jours"])); v_pas = tk.StringVar(value=str(c["pas_h"]))
    v_dos = tk.StringVar(value=c["dossier"]); v_xy = tk.StringVar(value=trouver_xygrib(c))
    pad = {"padx": 6, "pady": 3}
    ttk.Checkbutton(cadre, text="Vent + rafales", variable=v_vent).grid(row=0, column=0, sticky="w", **pad)
    ttk.Checkbutton(cadre, text="Vagues", variable=v_vag).grid(row=0, column=1, sticky="w", **pad)
    ttk.Label(cadre, text="Durée (jours) :").grid(row=1, column=0, sticky="w", **pad)
    ttk.Combobox(cadre, textvariable=v_jours, values=["3", "5", "7", "10", "16"], width=5,
                 state="readonly").grid(row=1, column=1, sticky="w")
    ttk.Label(cadre, text="Pas 5 premiers jours (h) :").grid(row=2, column=0, sticky="w", **pad)
    ttk.Combobox(cadre, textvariable=v_pas, values=["1", "3", "6"], width=5,
                 state="readonly").grid(row=2, column=1, sticky="w")

    def choisir_dossier():
        d = filedialog.askdirectory(initialdir=v_dos.get())
        if d: v_dos.set(d)

    def choisir_xy():
        f = filedialog.askopenfilename(title="Emplacement de XyGrib.exe")
        if f: v_xy.set(f)
    ttk.Label(cadre, text="Dossier :").grid(row=3, column=0, sticky="w", **pad)
    ttk.Entry(cadre, textvariable=v_dos).grid(row=3, column=1, sticky="we")
    ttk.Button(cadre, text="…", width=3, command=choisir_dossier).grid(row=3, column=2, padx=4)
    ttk.Label(cadre, text="XyGrib :").grid(row=4, column=0, sticky="w", **pad)
    ttk.Entry(cadre, textvariable=v_xy).grid(row=4, column=1, sticky="we")
    ttk.Button(cadre, text="…", width=3, command=choisir_xy).grid(row=4, column=2, padx=4)
    ttk.Checkbutton(cadre, text="Ouvrir automatiquement dans XyGrib", variable=v_ouv
                    ).grid(row=5, column=0, sticky="w", **pad)
    v_fermer = tk.BooleanVar(value=c.get("fermer", True))
    ttk.Checkbutton(cadre, text="Puis fermer cette fenêtre", variable=v_fermer
                    ).grid(row=5, column=1, sticky="w", **pad)
    v_garder = tk.BooleanVar(value=int(c.get("garder", 2)) > 0)
    ttk.Checkbutton(cadre, text="Effacer l'historique (garder les 2 derniers téléchargements)",
                    variable=v_garder).grid(row=8, column=0, columnspan=3, sticky="w", **pad)
    ttk.Label(cadre, text=f"Identifiant de ce PC : {licence.identifiant_machine()}",
              font=("Consolas", 9)).grid(row=6, column=0, columnspan=3, sticky="w", **pad)
    journal = tk.Text(cadre, height=5, font=("Consolas", 8), state="disabled")
    journal.grid(row=7, column=0, columnspan=3, sticky="nsew", padx=6, pady=(2, 6))
    cadre.rowconfigure(7, weight=1)
    cadre.columnconfigure(1, weight=1)

    def basculer_parametres():
        """Les paramètres REMPLACENT la zone centrale : la fenêtre garde la même taille."""
        if cadre.winfo_ismapped():
            cadre.pack_forget()
            centre.pack(fill="both", expand=True)
        else:
            centre.pack_forget()
            cadre.pack(fill="both", expand=True, padx=8, pady=(0, 6))
    ttk.Button(cadre, text="✔ Fermer les paramètres", command=lambda: basculer_parametres()
               ).grid(row=9, column=0, columnspan=3, pady=(4, 8))
    btn_par.config(command=basculer_parametres)

    def log(msg):
        def f():
            journal.config(state="normal"); journal.insert("end", msg + "\n")
            journal.see("end"); journal.config(state="disabled")
        root.after(0, f)

    def statut(msg, couleur="#333"):
        root.after(0, lambda: lbl_etat.config(text=msg, fg=couleur))

    # ---------------- licence
    def maj_licence(h=None) -> bool:
        try:
            info = controle_licence(h)
            etat["info"] = info
            fin = f"{info['fin'][8:10]}/{info['fin'][5:7]}/{info['fin'][:4]}"
            reste = (f" — {info['jours_restants']} jours restants" if info["jours_restants"] is not None else "")
            lbl_nom.config(text=info["nom"])
            lbl_validite.config(text=f"✔ Abonnement valable jusqu'au {fin}{reste}", fg="green")
            root.title(f"{info['nom']} — Météo Marine Tunisie — {MARQUE}")
            if not etat["en_cours"]:
                btn_dl.config(state="normal", bg=BLEU)
            return True
        except licence.LicenceInvalide as e:
            etat["info"] = None
            lbl_nom.config(text="Abonnement non activé" if "Aucune" in str(e) else "Abonnement à renouveler")
            lbl_validite.config(text=f"✖ {e}\nContact : {TELEPHONES}", fg="red")
            btn_dl.config(state="disabled", bg="#8a8fa8")
            return False

    def _reussite():
        maj_licence(etat.get("heure"))
        if etat.get("fen_act") is not None:
            try:
                etat["fen_act"].destroy()
            except Exception:
                pass
            etat["fen_act"] = None
        statut("✔ Abonnement activé", "green")   # pas de fenêtre à fermer

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
        w.title("Activer l'abonnement"); w.transient(root); w.resizable(False, False)
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

    btn_act.config(command=fenetre_activation)

    def a_propos(_e=None):
        a = tk.Toplevel(root); a.title("À propos"); a.transient(root); a.resizable(False, False)
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
        for t in ("Prévisions vent et vagues sur 16 jours",
                  "Côtes et haute mer tunisiennes (30°N–39°N / 7°E–14°E)",
                  "Données : NOAA GFS 0.25° et GFS-Wave · Affichage : XyGrib"):
            tk.Label(cad, text=t, font=("Segoe UI", 9), fg="#333", bg="white").pack()
        tk.Label(cad, text=SIGNATURE[0].upper() + SIGNATURE[1:], font=("Segoe UI", 10, "italic"),
                 fg=BLEU, bg="white").pack(pady=(12, 0))
        tk.Label(cad, text=f"{MARQUE} — {SLOGAN}", font=("Segoe UI", 10, "bold"), fg=BLEU, bg="white").pack()
        tk.Label(cad, text=TELEPHONES, font=("Segoe UI", 10, "bold"), fg=VERT_WA, bg="white").pack(pady=(2, 0))
        tk.Label(cad, text=f"© 2026 {MARQUE} — Tous droits réservés", font=("Segoe UI", 8),
                 fg="#888", bg="white").pack(pady=(10, 4))
        tk.Button(cad, text="Fermer", command=a.destroy, bg=BLEU, fg="white", relief="flat",
                  font=("Segoe UI", 10, "bold"), padx=24, pady=3).pack(pady=(4, 16))
    centre.tag_bind(id_sign, "<Button-1>", a_propos)
    centre.tag_bind(id_sign, "<Enter>", lambda e: (centre.itemconfig(id_sign, fill=BLEU), centre.config(cursor="hand2")))
    centre.tag_bind(id_sign, "<Leave>", lambda e: (centre.itemconfig(id_sign, fill="#8a90a8"), centre.config(cursor="")))

    # ---------------- téléchargement
    def lire_ui() -> dict:
        try:
            c.update(fermer=v_fermer.get(), garder=2 if v_garder.get() else 0)
            c.update(vent=v_vent.get(), vagues=v_vag.get(), ouvrir=v_ouv.get(), fusion=True,
                     jours=int(v_jours.get()), pas_h=int(v_pas.get()), dossier=v_dos.get(), xygrib=v_xy.get())
            sauver_config(c)
        except Exception:
            pass
        return c

    def progression(i, n):
        root.after(0, lambda: prog.config(maximum=n, value=i))

    def lancer():
        cfg = lire_ui()
        if not (cfg["vent"] or cfg["vagues"]):
            messagebox.showwarning("Météo Marine", "Cochez au moins vent ou vagues (⚙ Paramètres)."); return
        if not maj_licence(etat.get("heure")):
            fenetre_activation(); return
        etat["en_cours"] = True
        btn_dl.config(state="disabled", text="  TÉLÉCHARGEMENT…", bg="#3a4a8f")
        import time as _t
        debut = _t.time()

        def progression_eta(i, n):
            progression(i, n)
            ecoule = _t.time() - etape["t0"]
            reste = ecoule / max(i, 1) * (n - i)
            statut(f"{etape['nom']} : {i}/{n}  —  encore environ {int(reste) + 1} s")
        etape = {"nom": "", "t0": debut}

        def travail():
            try:
                statut("Vérification de la date et de l'abonnement…")
                h = heure.heure_internet()
                etat["heure"] = h
                controle_licence(h)
                statut("Recherche des dernières prévisions…")
                tel = gd.Telechargeur(h, log=log, progression=progression_eta); etat["tel"] = tel
                ancien_log = tel.log

                def log2(m):
                    ancien_log(m)
                    if m.startswith("Fichier météo préparé"):
                        statut("Téléchargement des prévisions préparées…")
                    elif m.startswith(("Courants", "Marée")):
                        statut("Téléchargement des courants…")
                    if m.startswith(("Vent", "Vagues")):
                        etape["nom"] = m.split(" :")[0].split(" (")[0]
                        etape["t0"] = _t.time()
                tel.log = log2
                fichiers = executer(cfg, tel)
                etat["derniers"] = fichiers
                rp = getattr(tel, "run_publie", None)
                info_run = (f"prévision NOAA du {rp:%d/%m} {rp.hour:02d} h UTC ({(rp.hour + 1) % 24:02d} h Tunis)"
                            if rp else os.path.basename(fichiers[-1]))
                statut(f"✔ Terminé en {int(_t.time() - debut)} s  —  {info_run}", "green")
                if cfg["ouvrir"]:
                    ouvert = False
                    for f in fichiers:
                        m = ouvrir_dans_xygrib(f, cfg)
                        log(m)
                        ouvert = ouvert or m.startswith("Ouverture dans XyGrib")
                    if not ouvert and not trouver_xygrib(cfg) and fichiers:   # pas de XyGrib : ACIS MétéoNav
                        root.after(0, lambda: ouvrir_carte(fichiers[-1]))
                    if ouvert and cfg.get("fermer", True):
                        statut("✔ Ouvert dans XyGrib — fermeture…", "green")
                        root.after(2000, root.destroy)   # la fenêtre disparaît, XyGrib reste ouvert
            except licence.LicenceInvalide as e:
                statut(f"Licence : {e}", "red")
            except Exception as e:
                log(f"ERREUR : {e}")
                statut("✖ Échec : vérifiez la connexion Internet (détails dans ⚙ Paramètres)", "red")
            finally:
                etat["en_cours"] = False

                def fin():
                    btn_dl.config(text="  TÉLÉCHARGER LA MÉTÉO")
                    maj_licence(etat["heure"])
                root.after(0, fin)
        threading.Thread(target=travail, daemon=True).start()
    btn_dl.config(command=lancer)

    def ouvrir_dernier():
        cfg = lire_ui()
        if etat["derniers"]:
            for f in etat["derniers"]:
                log(ouvrir_dans_xygrib(f, cfg))
        else:
            f = filedialog.askopenfilename(initialdir=cfg["dossier"], filetypes=[("GRIB", "*.grb2 *.grb *.grib2")])
            if f: log(ouvrir_dans_xygrib(f, cfg))
    btn_ouvrir.config(command=ouvrir_dernier)

    def ouvrir_carte(f: str = None):
        """ACIS MétéoNav (visionneuse intégrée) : même fichier que XyGrib, réservée aux abonnés."""
        cfg = lire_ui()
        if not maj_licence(etat.get("heure")):
            fenetre_activation(); return
        if not f:
            f = etat["derniers"][-1] if etat["derniers"] else None
        if not f or not os.path.exists(f):
            import glob
            tous = sorted(glob.glob(os.path.join(cfg["dossier"], "Tunisie_complet_*.grb2")), key=os.path.getmtime)
            f = tous[-1] if tous else filedialog.askopenfilename(initialdir=cfg["dossier"],
                                                                  filetypes=[("GRIB", "*.grb2 *.grb *.grib2")])
        if not f:
            messagebox.showinfo("ACIS MétéoNav", "Téléchargez d'abord la météo."); return
        statut("Ouverture d’ACIS MétéoNav…")
        root.update_idletasks()
        try:
            import visionneuse                     # importée ici : un souci de la carte ne gêne jamais le reste
            visionneuse.ouvrir(f, parent=root, version=version_texte(), maintenant=etat.get("heure"))
            statut(f"✔ Carte ouverte : {os.path.basename(f)}", "green")
        except Exception as e:                     # noqa: BLE001
            log(f"ACIS MétéoNav : {e}")
            statut("✖ Carte indisponible : utilisez XyGrib", "red")
    btn_carte.config(command=ouvrir_carte)

    # ---------------- démarrage
    if not maj_licence():
        root.after(300, fenetre_activation)
    if not trouver_xygrib(c):
        def proposer_xygrib():
            url = "https://opengribs.org/fr/xygrib"
            if messagebox.askyesno("XyGrib", "XyGrib n'est pas installé sur ce PC.\n"
                                             "Ouvrir la page de téléchargement (gratuit) ?"):
                ok = False
                try:
                    ok = webbrowser.open(url)
                except Exception:
                    pass
                if not ok:
                    messagebox.showinfo("XyGrib", "Pas de navigateur sur ce PC.\nInstallez XyGrib depuis une "
                                                  f"clé USB (installateur téléchargé sur un autre PC) :\n{url}")
        root.after(1500, proposer_xygrib)
    if "--installe" in sys.argv:
        statut("✔ Installé : raccourci « Météo Marine Tunisie » créé sur le Bureau", "green")

    def heure_au_demarrage():
        try:
            h = heure.heure_internet()
            etat["heure"] = h
            root.after(0, lambda: maj_licence(h))
        except Exception:
            log("Pas de connexion Internet : la licence sera vérifiée au téléchargement.")
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
