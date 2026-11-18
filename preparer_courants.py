"""
Robot des courants — A.C.I.S Marine / Météo Marine Tunisie.
Tourne chaque jour chez GitHub (voir .github/workflows/courants.yml) :
  1. lit les courants de surface Copernicus Marine (Méditerranée, ~4 km, horaires) sur la zone Tunisie ;
  2. garde 1 point sur 2 (~9 km) et 1 heure sur 3 pour un fichier léger ;
  3. écrit courants_tunisie.grb2 (GRIB2 : UOGRD / VOGRD), publié ensuite comme fichier de « release » ;
  4. écrit marees_tunisie.grb2 : hauteur d'eau horaire (marée + effet du vent/pression), 1 point sur 3 (~14 km),
     écart au niveau moyen de chaque point (le modèle Copernicus Méditerranée inclut la marée).
Identifiants Copernicus : secrets GitHub COPERNICUSMARINE_SERVICE_USERNAME / _PASSWORD (jamais dans le code).
Données : E.U. Copernicus Marine Service Information — MEDSEA_ANALYSISFORECAST_PHY_006_013.
"""
import datetime as dt
import math
import os
import sys

import copernicusmarine
import numpy as np

from grib2_ecrire import message

DATASET = "cmems_mod_med_phy-cur_anfc_4.2km-2D_PT1H-m"
DATASET_NIVEAU = "cmems_mod_med_phy-ssh_anfc_4.2km-2D_PT1H-m"     # zos : hauteur de la mer, horaire
PAS_POINTS_MAREE = 3    # 1/24° -> 1/8°
ZONE = {"sud": 30.2, "nord": 39.0, "ouest": 7.0, "est": 14.0}
PAS_POINTS = 2          # 1/24° -> 1/12°
PAS_HEURES = 3
SORTIE = sys.argv[1] if len(sys.argv) > 1 else "courants_tunisie.grb2"


def main():
    maintenant = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    ds = copernicusmarine.open_dataset(
        dataset_id=DATASET, variables=["uo", "vo"],
        minimum_longitude=ZONE["ouest"], maximum_longitude=ZONE["est"],
        minimum_latitude=ZONE["sud"], maximum_latitude=ZONE["nord"],
        start_datetime=(maintenant - dt.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S"),
    )
    if "depth" in ds.dims:
        ds = ds.isel(depth=0)
    ds = ds.sortby("latitude").sortby("longitude")
    ds = ds.isel(latitude=slice(None, None, PAS_POINTS), longitude=slice(None, None, PAS_POINTS))
    temps = [dt.datetime.fromtimestamp(t.astype("datetime64[s]").astype(int), dt.timezone.utc) for t in ds["time"].values]
    garder = [k for k, t in enumerate(temps) if round((t - temps[0]).total_seconds() / 3600) % PAS_HEURES == 0]
    lat, lon = ds["latitude"].values, ds["longitude"].values
    ni, nj = len(lon), len(lat)
    dlat = float(np.round((lat[-1] - lat[0]) / (nj - 1), 6))
    dlon = float(np.round((lon[-1] - lon[0]) / (ni - 1), 6))
    ref = temps[garder[0]]
    print(f"Grille {ni}x{nj} pas {dlon:.4f}°, {len(garder)} échéances du {ref:%d/%m %H:%M} au {temps[garder[-1]]:%d/%m %H:%M} UTC")
    morceaux = []
    for k in garder:
        minutes = int(round((temps[k] - ref).total_seconds() / 60))
        for var, num in (("uo", 2), ("vo", 3)):
            a = ds[var].isel(time=k).values.astype(float)          # [lat, lon], sud -> nord
            vals = [None if math.isnan(v) else float(v) for v in a.ravel()]
            morceaux.append(message(10, 1, num, ref, minutes, float(lat[0]), float(lon[0]), dlat, dlon, ni, nj, vals))
    with open(SORTIE + ".part", "wb") as f:
        f.write(b"".join(morceaux))
    os.replace(SORTIE + ".part", SORTIE)
    print(f"Écrit : {SORTIE} ({os.path.getsize(SORTIE) / 1e6:.2f} Mo)")


def marees(sortie: str = "marees_tunisie.grb2") -> None:
    """Hauteur d'eau horaire (m) autour du niveau moyen de chaque point, pour la courbe de marée de l'appli."""
    maintenant = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    ds = copernicusmarine.open_dataset(
        dataset_id=DATASET_NIVEAU, variables=["zos"],
        minimum_longitude=ZONE["ouest"], maximum_longitude=ZONE["est"],
        minimum_latitude=ZONE["sud"], maximum_latitude=ZONE["nord"],
        start_datetime=(maintenant - dt.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S"),
    )
    ds = ds.sortby("latitude").sortby("longitude")
    ds = ds.isel(latitude=slice(None, None, PAS_POINTS_MAREE), longitude=slice(None, None, PAS_POINTS_MAREE))
    z = ds["zos"].values.astype(float)                       # [temps, lat, lon]
    z = z - np.nanmean(z, axis=0)                            # écart au niveau moyen de la période
    temps = [dt.datetime.fromtimestamp(t.astype("datetime64[s]").astype(int), dt.timezone.utc) for t in ds["time"].values]
    lat, lon = ds["latitude"].values, ds["longitude"].values
    ni, nj = len(lon), len(lat)
    dlat = float(np.round((lat[-1] - lat[0]) / (nj - 1), 6))
    dlon = float(np.round((lon[-1] - lon[0]) / (ni - 1), 6))
    ref = temps[0]
    morceaux = []
    for k, t in enumerate(temps):
        minutes = int(round((t - ref).total_seconds() / 60))
        vals = [None if math.isnan(v) else float(v) for v in z[k].ravel()]
        morceaux.append(message(10, 3, 1, ref, minutes, float(lat[0]), float(lon[0]), dlat, dlon, ni, nj, vals))
    with open(sortie + ".part", "wb") as f:
        f.write(b"".join(morceaux))
    os.replace(sortie + ".part", sortie)
    print(f"Marées : grille {ni}x{nj}, {len(temps)} heures du {ref:%d/%m %H:%M} au {temps[-1]:%d/%m %H:%M} UTC, "
          f"{os.path.getsize(sortie) / 1e6:.2f} Mo")


if __name__ == "__main__":
    main()
    try:                                     # la marée ne doit jamais empêcher la publication des courants
        marees()
    except Exception as e:                   # noqa: BLE001
        print("Marées non préparées :", e)
