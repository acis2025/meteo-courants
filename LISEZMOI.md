# Robot des courants — Météo Marine Tunisie (A.C.I.S Marine)

Chaque jour à 17 h 40 UTC, ce robot prépare `courants_tunisie.grb2`, avec les courants de surface de la zone Tunisie (Copernicus Marine, Méditerranée).
Il le publie à cette adresse :

    https://github.com/<compte>/<depot>/releases/download/courants/courants_tunisie.grb2

L'appli Android et le programme PC le téléchargent avec le vent et les vagues.

Données : E.U. Copernicus Marine Service Information (MEDSEA_ANALYSISFORECAST_PHY_006_013).

## Contrôle de la précision (automatique)

Chaque heure (à :45), le robot « Précision météo » archive tout nouveau run du fichier `Tunisie_1h.grb2` envoyé aux clients. Il le compare ensuite aux relevés réels de 7 aéroports côtiers : Tabarka, Tunis, Enfidha, Monastir, Sfax, Gabès et Djerba (METAR, archive IEM). Les résultats portent sur les 30 derniers jours, par échéance de J+0 à J+6. Ils sont publiés ici :

    https://github.com/acis2025/meteo-courants/releases/download/verif/precision.txt

Rien n'est modifié dans les fichiers des clients. Script : `verifier_precision.py`.
