# Code du programme client — mises à jour EN LIGNE

Ce dossier contient le code de la dernière version du programme client (PC et téléphone).
À chaque changement ici, le robot (`.github/workflows/appli.yml` → `publier_appli.py`) fabrique
`maj.zip`, le **signe** avec le secret GitHub `MAJ_CLE` et le publie dans la release `appli`.
Les programmes installés chez les clients le téléchargent seuls (au lancement, en arrière-plan)
et l'utilisent **au lancement suivant**. Un paquet mal signé est refusé.

Pour publier une modification :
1. remplacer le fichier ici (même nom) ;
2. augmenter `VERSION` dans `version_appli.py` (AAAAMMJJHHMM) ;
3. enregistrer (commit) : la publication est automatique.

| Fichier | Dans le projet MeteoMarineTN |
|---|---|
| `serveur.py`, `parametres.html` | `android/src/meteomarinetn/` |
| `donnees_carte.py`, `carte.html` | `carte/` |
| `visionneuse.py` (carte A.C.I.S du programme PC) | racine |
| les autres `.py` | racine |

Jamais ici : clés, mots de passe, `cle_privee.pem`, `admin/`.
Ne sont jamais remplacés en ligne : `maj_en_ligne.py`, `ed25519.py`, les clés publiques.
