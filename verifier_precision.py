"""
Contrôle quotidien de la précision — Météo Marine Tunisie (A.C.I.S Marine).

À chaque nouveau run publié par le robot météo, ce script :
  1. ARCHIVE la prévision de vent du fichier Tunisie_1h.grb2 (exactement celui que reçoivent les clients)
     aux 7 aéroports côtiers, sur 7 jours ;
  2. TÉLÉCHARGE les observations réelles de ces aéroports (METAR, archive IEM / Iowa State University) ;
  3. COMPARE prévision et observation, par échéance (J+0 … J+6), sur les 30 derniers jours,
     et publie precision.txt (lisible) + precision.json (pour l'appli plus tard).

Usage : python verifier_precision.py DOSSIER
  DOSSIER contient Tunisie_1h.grb2, run.txt et (s'il existe déjà) previsions_archive.json.
Seul le contrôle est fait ici : rien n'est changé dans les fichiers envoyés aux clients.
"""
import csv
import datetime as dt
import io
import json
import math
import os
import ssl
import sys
import urllib.request

import grib2

STATIONS = {                      # code OACI : (nom, latitude, longitude)
    "DTKA": ("Tabarka", 36.950, 8.750),
    "DTTA": ("Tunis-Carthage", 36.844, 10.228),
    "DTNH": ("Enfidha", 36.069, 10.432),
    "DTMB": ("Monastir", 35.758, 10.755),
    "DTTX": ("Sfax", 34.718, 10.691),
    "DTTG": ("Gabès", 33.877, 10.103),
    "DTTJ": ("Djerba", 33.875, 10.776),
}
JOURS_ARCHIVE = 30                # runs gardés (et période des statistiques)
ECHEANCE_MAX_H = 168              # 7 jours
FORT_KT = 15                      # « vent fort » pour le contrôle des coups de vent
URL_OBS = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?{stations}&data=drct&data=sknt"
           "&year1={d.year}&month1={d.month}&day1={d.day}&year2={f.year}&month2={f.month}&day2={f.day}"
           "&tz=Etc/UTC&format=onlycomma&missing=M&report_type=3&report_type=4")


def _heure(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H")


def archiver(dossier: str, archive: dict) -> bool:
    """Ajoute le run de Tunisie_1h.grb2 à l'archive. Faux s'il y est déjà."""
    run = open(os.path.join(dossier, "run.txt"), encoding="ascii").read().strip()[:10]
    if run in archive["runs"]:
        print(f"Run {run} déjà archivé")
        return False
    prev = grib2.Previsions(grib2.lire_fichier(os.path.join(dossier, "Tunisie_1h.grb2")))
    ref = f"{prev.date_ref:%Y%m%d%H}"
    if ref != run:
        print(f"Attention : run.txt = {run}, fichier = {ref} ; on garde le fichier")
        run = ref
        if run in archive["runs"]:
            return False
    pts = {s: {} for s in STATIONS}
    for k, h in enumerate(prev.echeances):
        if h > ECHEANCE_MAX_H:
            break
        t = _heure(prev.date(k))
        for s, (_, lat, lon) in STATIONS.items():
            p = prev.point(k, lat, lon)
            if "vent_kt" in p:
                pts[s][t] = [round(p["vent_kt"], 1), round(p["vent_dir"])]
    archive["runs"][run] = pts
    limite = f"{dt.datetime.utcnow() - dt.timedelta(days=JOURS_ARCHIVE + 7):%Y%m%d%H}"
    for r in [r for r in archive["runs"] if r < limite]:
        del archive["runs"][r]
    print(f"Run {run} archivé ({sum(len(v) for v in pts.values())} prévisions)")
    return True


def observations(debut: dt.datetime, fin: dt.datetime) -> dict:
    """{station: {"AAAA-MM-JJTHH": (vent kt, direction ou None)}} — relevés à l'heure ronde seulement."""
    url = URL_OBS.format(stations="&".join("station=" + s for s in STATIONS), d=debut, f=fin + dt.timedelta(days=1))
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except ImportError:
        pass
    req = urllib.request.Request(url, headers={"User-Agent": "MeteoMarineTN-verification"})
    texte = urllib.request.urlopen(req, timeout=120, context=ctx).read().decode("utf-8")
    obs = {s: {} for s in STATIONS}
    for l in csv.DictReader(io.StringIO(texte)):
        if l.get("station") not in obs or not l["valid"].endswith(":00") or l["sknt"] == "M":
            continue
        d = None if l["drct"] == "M" else float(l["drct"])
        obs[l["station"]][l["valid"][:13].replace(" ", "T")] = (float(l["sknt"]), d)
    return obs


def _stats(liste: list) -> dict:
    n = len(liste)
    if not n:
        return {"n": 0}
    e = [f - o for o, f, _, _ in liste]
    dirs = []
    for o, f, do, df in liste:
        if o >= 7 and do is not None and df is not None:
            d = abs(df - do) % 360
            dirs.append(360 - d if d > 180 else d)
    forts = [f - o for o, f, _, _ in liste if o >= FORT_KT]
    return {
        "n": n,
        "biais_kt": round(sum(e) / n, 1),
        "ecart_moyen_kt": round(sum(abs(x) for x in e) / n, 1),
        "rmse_kt": round(math.sqrt(sum(x * x for x in e) / n), 1),
        "pct_a_5kt": round(100 * sum(abs(x) <= 5 for x in e) / n),
        "ecart_direction_deg": round(sum(dirs) / len(dirs)) if dirs else None,
        "n_vent_fort": len(forts),
        "biais_vent_fort_kt": round(sum(forts) / len(forts), 1) if forts else None,
    }


def comparer(archive: dict, obs: dict, maintenant: dt.datetime) -> dict:
    debut = _heure(maintenant - dt.timedelta(days=JOURS_ARCHIVE))
    par_jour, par_station, par_date = {}, {}, {}
    for run, pts in archive["runs"].items():
        t0 = dt.datetime.strptime(run, "%Y%m%d%H")
        for s, serie in pts.items():
            for t, (f, df) in serie.items():
                o = obs.get(s, {}).get(t)
                if o is None or t < debut:
                    continue
                lead = (dt.datetime.strptime(t, "%Y-%m-%dT%H") - t0).total_seconds() / 3600
                j = int(lead // 24)
                x = (o[0], f, o[1], df)
                par_jour.setdefault(j, []).append(x)
                if j <= 2:
                    par_station.setdefault(s, []).append(x)
                if j in (0, 1, 3):
                    par_date.setdefault((t[:10], j), []).append(x)
    hist = {}
    for (jour, j), lst in sorted(par_date.items()):
        hist.setdefault(jour, {})[f"J+{j}"] = _stats(lst).get("ecart_moyen_kt")
    return {
        "mis_a_jour": f"{maintenant:%Y-%m-%d %H:%M} UTC",
        "periode_jours": JOURS_ARCHIVE,
        "runs_archives": len(archive["runs"]),
        "par_echeance": {f"J+{j}": _stats(par_jour[j]) for j in sorted(par_jour)},
        "par_station_J0_J2": {f"{STATIONS[s][0]} ({s})": _stats(v) for s, v in sorted(par_station.items())},
        "historique_ecart_moyen_kt": hist,
    }


def texte(r: dict) -> str:
    L = ["PRÉCISION DU VENT — MÉTÉO MARINE TUNISIE (A.C.I.S Marine)",
         f"Mis à jour : {r['mis_a_jour']} — {r['periode_jours']} derniers jours, {r['runs_archives']} runs archivés",
         "Prévision du fichier envoyé aux clients, comparée aux relevés réels de 7 aéroports côtiers",
         "(Tabarka, Tunis, Enfidha, Monastir, Sfax, Gabès, Djerba). Vent en nœuds.", "",
         "Échéance  Relevés  Écart moyen  Biais  À ±5 nd  Direction  Vent fort (≥15 nd) : biais"]
    for j, s in r["par_echeance"].items():
        if s["n"]:
            fort = f"{s['biais_vent_fort_kt']:+.1f} nd ({s['n_vent_fort']})" if s["biais_vent_fort_kt"] is not None else "—"
            dirn = f"±{s['ecart_direction_deg']}°" if s["ecart_direction_deg"] is not None else "—"
            L.append(f"{j:<9} {s['n']:>7}  {s['ecart_moyen_kt']:>8.1f} nd  {s['biais_kt']:+5.1f}  {s['pct_a_5kt']:>6} %"
                     f"  {dirn:>9}  {fort}")
    L += ["", "Par station (J+0 à J+2) :"]
    for nom, s in r["par_station_J0_J2"].items():
        if s["n"]:
            L.append(f"  {nom:<22} écart moyen {s['ecart_moyen_kt']:.1f} nd, biais {s['biais_kt']:+.1f}, "
                     f"{s['pct_a_5kt']} % à ±5 nd")
    L += ["", "Lecture : biais positif = prévision plus forte que le relevé. Les aéroports sont à terre :",
          "en mer, le vent réel est souvent un peu plus fort que le relevé de l'aéroport.",
          "Source des relevés : METAR, archive IEM (Iowa State University)."]
    return "\n".join(L) + "\n"


def main(dossier: str) -> None:
    chemin = os.path.join(dossier, "previsions_archive.json")
    archive = {"runs": {}}
    if os.path.exists(chemin):
        try:
            archive = json.load(open(chemin, encoding="utf-8"))
        except ValueError:
            print("Archive illisible : on repart de zéro")
    nouveau = archiver(dossier, archive)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(archive, f, separators=(",", ":"))
    if not nouveau and os.path.exists(os.path.join(dossier, "precision.json")):
        open(os.path.join(dossier, "rien_a_faire"), "w").close()
        return
    maintenant = dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    try:
        obs = observations(maintenant - dt.timedelta(days=JOURS_ARCHIVE), maintenant)
    except Exception as e:                               # noqa: BLE001
        print(f"Relevés indisponibles ({e}) : archive publiée, statistiques au prochain passage")
        return
    r = comparer(archive, obs, maintenant)
    json.dump(r, open(os.path.join(dossier, "precision.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    t = texte(r)
    open(os.path.join(dossier, "precision.txt"), "w", encoding="utf-8").write(t)
    print(t)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
