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
| Carte intégrée au programme PC (« ACIS MétéoNav ») | branche `carte-acis-pc` (conversation du Dell), commit `1b40cce` | 202611272355 — pas encore publiée (attend test Windows 7 + accord) ; installée sur le Dell pour le test |
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

- [x] **Dell (`carte-acis-pc`) — DÉCISION de l'utilisateur 27/11 20:43 : garder XyGrib en secours.** (fait le 27/11, `5db32fc`) Dans Paramètres, un bouton **« Ouvrir dans XyGrib »** : ouvre le dernier fichier `Tunisie_complet_*` dans XyGrib (reprendre la détection et le lancement de XyGrib du code de `main`) ; si XyGrib n'est pas installé : message clair. Si la carte plante, proposer aussi « Ouvrir dans XyGrib » dans le message d'erreur. Aucun nouvel import (règle 5).
- [ ] **Avant de publier la carte PC** : tester l'exe sur un vieux PC Windows 7 (vitesse de la carte), puis accord de l'utilisateur.
- [x] Dell (`carte-acis-pc`) : `glob` → `os.listdir` dans `fichiers_meteo()` (fait le 27/11, `77ef199`).
- [ ] Dell (`carte-acis-pc`) : l'utilisateur teste comme un client (programme installé sur le Dell, mis à jour à chaque changement : 202611272355) ; puis refusionner `main` et publier avec son accord.
- [ ] Générateur de codes (projet local MeteoMarineTN, pas dans ce dépôt) : correction « un renouvellement garde le n° du client » faite le 27/11 dans `generateur.py` et `serveur_gen.py` ; à recompiler (`build_generateur.bat`, `build_generateur_android.bat`).
- [ ] Republier `serveur.py` (téléphone) avec le jeton du HP, en choisissant le fichier par date (`Tunisie_complet_*`), pas par ordre alphabétique.

## Journal (le plus récent en haut)

- **27/11 23:55 — Claude Code (Dell, `carte-acis-pc`)** : ESSAI RÉEL du programme installé sur le Dell (licence réelle, vraies touches du clavier, vrai clic) : flèches = déplacement OK, Page suiv. = heure OK, « - » pavé num. OK, **bouton XyGrib OK** (ouvre le dernier `Tunisie_complet_*` dans XyGrib 1.2.6, installé sur le Dell). Défauts trouvés et corrigés : « + » d'un clavier AZERTY ne zoomait pas (→ reconnu par le caractère tapé) ; traits blancs droits au bord de la carte en dézoomant (côtés de découpage des terres, plus tracés) ; ligne Lune coupée (« Précision mesurée » seulement sans point survolé). VERSION 202611272355, commit `1b40cce`, pas publié ; exe du Dell mis à jour. Rappel : ← → ne bougent rien quand toute la zone tient en largeur (normal).
- **27/11 23:30 — Claude Code (Dell, `carte-acis-pc`)** : DÉCISIONS de l'utilisateur — (1) bouton **« XyGrib »** placé **en haut à droite de l'en-tête bleu** du panneau de droite (à droite de l'heure et de l'abonnement) ; le bouton Secours de Paramètres reste ; (2) **les flèches du clavier déplacent la carte** (quart d'écran) ; **+ / -** zoom ; l'heure passe sur **Page préc. / Page suiv.** (ou **Ctrl + ← / →**) ; Espace = animation. Fait et testé (touches simulées) : VERSION 202611272330, commit `ee867ce`, pas publié ; exe installé sur le Dell mis à jour.
- **27/11 23:10 — Claude Code (Dell, `carte-acis-pc`)** : fait « Ouvrir dans XyGrib » (secours) : Paramètres → Secours → « Ouvrir dans XyGrib » (dernier `Tunisie_complet_*` ; si XyGrib introuvable : message + choix de XyGrib.exe, gardé dans la config) ; si la carte plante : « Annuler » propose XyGrib puis WhatsApp. Détection reprise de `main`, aucun nouvel import, plus d'ouverture automatique. VERSION 202611272310, commit `5db32fc`, pas publié. XyGrib absent du Dell : seul le cas « introuvable » a pu être testé.
- **27/11 22:50 — Claude Code (Dell, `carte-acis-pc`)** : DÉCISIONS de l'utilisateur — (1) **une seule chose à la fois** sur la carte : vent/rafales = barbules, pluie/nuages = couleurs + valeurs (plus de barbules), marée = hauteur d'eau, vagues/courant = flèches ; (2) nouvelle couche **« Carte »** (carte seule, aucune donnée) ; (3) **« Couleurs : Mer / Terre / Partout »** pour vent, rafales, pluie, nuages (gardé par couche) ; (4) **terre toujours visible** (côtes et frontières en double trait sombre + clair) ; (5) **« + Flèches du courant »** ajoutables à toute couche, flèches **plus grandes** ; (6) l'exe installé sur le Dell est **mis à jour à chaque changement**. Fait : VERSION 202611272250, commit `e91022b`. Aussi : ligne Lune de la fiche raccourcie.
- **27/11 20:45 — chat claude.ai** : DÉCISION de l'utilisateur — garder un bouton de secours « Ouvrir dans XyGrib » dans Paramètres (voir À faire). Rappel : publier la carte PC fait passer **tous** les PC clients de XyGrib à la carte au 2e lancement → test préalable sur un vieux PC Windows 7.
- **27/11 22:10 — Claude Code (Dell)** : DÉCISION — chaque décision de l'utilisateur est désormais notée dans ce journal.
- **22–27/11 — Claude Code (Dell, `carte-acis-pc`) : DÉCISIONS de l'utilisateur pour le programme PC** (déjà appliquées sur la branche) :
  - **Plus de XyGrib** : le programme tourne sans XyGrib ; la carte ACIS MétéoNav est le seul affichage.
  - **La carte est la fenêtre principale**, ouverte **en plein écran** et **nette** (zoom Windows géré) ; **aucun écran d'attente** (sans météo : carte vide « Pas encore de météo ») ; si la carte plante : petit message Windows + « Envoyer le problème à A.C.I.S (WhatsApp) ».
  - **Téléchargement automatique à l'ouverture** (désactivable) ; bouton **« Télécharger »** toujours visible : fond blanc, **ancre A.C.I.S** (image fournie, `appli/ancre.txt`) inclinée et collée au mot, mot **rouge drapeau, très gras, centré**.
  - **Menu de l'ancienne première page dans Paramètres**, ouvert en **cliquant sur le drapeau** ; « Valeurs » et « Marge de sécurité » aussi dans Paramètres ; « A.C.I.S Marine » retiré à côté du titre.
  - **Nom du client + validité de l'abonnement en haut** (panneau de droite), **date et heure Internet** (jamais l'horloge du PC) au-dessus.
  - **Coordonnées au format A.C.I.S : `N34°38.808-E10°45.341`** (lettre devant, minutes à 3 décimales, tiret).
  - **Flèches ◀ ▶ = deux grands triangles** ; boutons des couches (Vent, Vagues…) et texte du panneau de droite **plus grands**.
  - **Lune dans la bande bleue à gauche**, dessinée selon sa phase ; au survol : sa fiche (phase, lever/coucher, marée, prochaines pleine/nouvelle lune).
  - **Couches courant, pluie, nuages, marée** + fiche du point avec marée (PM/BM, marnage, vive-eau/morte-eau), soleil, lune ; **marée cochée par défaut**.
  - **Logo A.C.I.S en filigrane** (« gravé ») sur la terre Algérie–Tunisie, sans barbules dessus (`appli/logo.txt`).
  - **Frise du temps comme XyGrib**, avec les jours.
  - Générateur de codes : **un renouvellement garde le numéro du client** (reconnu par son nom, accents/espaces/majuscules ignorés).
- **27/11 22:00 — Claude Code (Dell, `carte-acis-pc`)** : carte PC, frise du temps comme XyGrib (jours « ven. 27 », heures 06h/12h/18h, trait rouge = maintenant, curseur jaune, clic/glisser) à la place du curseur simple ; VERSION 202611272200 ; commit `872e5f1`, pas publié. Aucun nouvel import, Python 3.8 OK.
- **27/11 21:30 — Claude Code (Dell, `carte-acis-pc`)** : `glob` → `os.listdir` dans `fichiers_meteo()` (fichiers complets d'abord, morceaux ensuite) ; VERSION 202611272130 ; commit `77ef199`, pas publié. Règle 5 vérifiée sur l'exe Windows 7 actuel (`dist7`, 20/11) : tous les modules importés par `appli/` y sont (aucun nouvel import, `BASE_MIN` inchangé). Avant : fusion de `main` (25/11) dans la branche (`a80da41`), exe de test refait, traces de test effacées sur ce PC. `maj_en_ligne.py`, clés et `INTERDITS` non modifiés.
- **27/11 20:20 — chat claude.ai** : création de ce fichier. Revue de la branche `carte-acis-pc` : contient bien toutes les corrections de `main` jusqu'au 25/11 20:45 ; syntaxe 3.8 OK ; seul point : `glob` (voir À faire).
- **25/11 20:45 — chat claude.ai** : publié 202611252045 (un seul rechargement de la carte ; lecture GRIB et calcul de la carte 2 à 3 fois plus rapides).
- **25/11 13:00 — chat claude.ai** : publié 202611251300 (morceaux d'un téléchargement interrompu : nom provisoire `en_cours_*`, supprimés au téléchargement suivant).
- **24/11 19:45 — chat claude.ai** : publié 202611241930 et 202611241945 (3 essais pour le fichier du robot, avancement en %, coupures réseau gérées).
