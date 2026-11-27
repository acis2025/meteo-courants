# Coordination entre les conversations Claude du projet Météo Marine

**Ce fichier est la mémoire commune.** Toute conversation Claude qui travaille sur le projet
(chat claude.ai, PC Dell, PC HP, téléphone) le **lit avant chaque travail** et **y écrit après** :
ce qu'elle a changé, ce qu'elle a publié, ce qui reste en cours. Toujours sur la branche `main`
(`git pull` juste avant d'écrire, pour ne rien écraser).

Aucun secret ici (dépôt public) : pas de clé, mot de passe, token, code d'activation, ni donnée client.

## Où en est le code

| Quoi | Où | Version |
|---|---|---|
| Code en ligne (tous les PC et téléphones) | branche `main`, dossier `appli/` → release `appli` | **202611252045** (25/11 20:45) |
| Carte intégrée au programme PC (« ACIS MétéoNav ») | branche `carte-acis-pc` (conversation du Dell) | 202611272030 — pas encore publiée |
| `serveur.py` du téléphone | `appli/en_attente/` (pas dans le paquet en ligne) | version avec jeton restée sur le HP |

## Règles pour publier (branche `main`, dossier `appli/`)

1. Ce qui est sur `main` dans `appli/` part chez **tous les clients** (PC et téléphones). On ne publie qu'après un test comme un client et avec l'accord de l'utilisateur.
2. **Monter `VERSION`** dans `appli/version_appli.py` à chaque publication. Un programme n'utilise le code en ligne que s'il est plus récent que sa propre `VERSION`, et seulement au lancement suivant.
3. Un exe de test doit avoir une `VERSION` plus haute que la release en ligne, sinon son nouveau code est remplacé au 2e lancement.
4. **Refusionner `main`** dans sa branche juste avant de publier (`main` change souvent : corrections, robot).
5. **Pas de nouvel import** d'une bibliothèque que les anciens exe n'ont pas : PyInstaller n'a mis dans l'exe que ce que l'ancien code importait. Sinon monter `BASE_MIN` (les anciens programmes ignorent alors la mise à jour).
6. Code compatible Python 3.8 (Windows 7) : `ast.parse(code, feature_version=(3, 8))`.
7. Ne jamais toucher : `maj_en_ligne.py`, `ed25519.py`, `cle_publique.py`, `cle_maj_publique.py` (jamais remplacés en ligne), ni la liste `INTERDITS`.
8. Rien de secret sur GitHub ni dans le code. L'accès à GitHub passe par l'application Claude reliée au compte `acis2025`.
9. Le numéro de version doit toujours être visible dans les programmes (demande de l'utilisateur).

## À faire

- [ ] Dell (`carte-acis-pc`) : remplacer `glob` par `os.listdir` dans `fichiers_meteo()` de `meteo_marine.py` (règle 5) ; tester l'exe comme un client ; puis publier avec l'accord de l'utilisateur.
- [ ] Republier `serveur.py` (téléphone) avec le jeton du HP, en choisissant le fichier par date (`Tunisie_complet_*`), pas par ordre alphabétique.

## Journal (le plus récent en haut)

- **27/11 20:20 — chat claude.ai** : création de ce fichier. Revue de la branche `carte-acis-pc` : contient bien toutes les corrections de `main` jusqu'au 25/11 20:45 ; syntaxe 3.8 OK ; seul point : `glob` (voir À faire).
- **25/11 20:45 — chat claude.ai** : publié 202611252045 (un seul rechargement de la carte ; lecture GRIB et calcul de la carte 2 à 3 fois plus rapides).
- **25/11 13:00 — chat claude.ai** : publié 202611251300 (morceaux d'un téléchargement interrompu : nom provisoire `en_cours_*`, supprimés au téléchargement suivant).
- **24/11 19:45 — chat claude.ai** : publié 202611241930 et 202611241945 (3 essais pour le fichier du robot, avancement en %, coupures réseau gérées).
