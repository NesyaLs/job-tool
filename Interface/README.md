# Interface web de jobtool

HTML, CSS et JavaScript **vanilla**, en **modules ES natifs** : aucun framework, aucun
bundler, aucune étape de build, aucun npm. Le navigateur résout le graphe d'imports depuis
`js/app.js`. Style « Carnet », palette « Papier vert » : voir
[`docs/design/`](../docs/design/) (maquette, plan, choix par défaut).

## Lancer

Le front est servi par l'API, en même origine (donc sans CORS) :

```bash
jobtool api            # puis ouvrir http://127.0.0.1:8000
jobtool api --local    # mono-poste forcé, même si .env déclare Supabase
```

Les modules ES imposent `http://` : ouvrir `index.html` par double-clic ne marche pas.
Aucune ressource externe : les polices (Fraunces, Inter, licence OFL) sont servies depuis
`fonts/`, l'interface marche hors ligne en mono-poste.

## Organisation

```
index.html          point d'entrée : feuilles de style, puis js/app.js
css/
  tokens.css        SEUL fichier de couleurs et de polices (--accent suit l'espace)
  base.css layout.css components.css
  offres.css drawer.css profil.css interview.css admin.css auth.css
fonts/              Fraunces et Inter en woff2, avec leurs licences
assets/logo/        logo et favicons (copies de jobtool-logo/)
assets/templates/   miniatures des templates (scripts/generate_template_previews.py)
js/
  app.js            démarrage : authentification, cadre, espaces, collecte automatique
  api/client.js     SEUL module qui parle au réseau (jeton, 401, délais, blobs)
  core/             dom (el, icon), store (état + abonnements), router (#/espace/section),
                    format, storage (préférences du poste), toast, dialog, keys
  shell/            barre du haut, menu à gauche, menu de l'avatar, collecte, réglages
  offres/           dossiers, liste, détail, pastilles, lieu, gestes, ajout manuel, tri rapide
  postuler/         tiroir Postuler : session d'un document, aperçu, retouche, mode avancé
  profil/           cinq sections, enregistrement automatique, profils, jauge, pont compétences
  interview/        panneau d'entretien IA
  admin/            Administration et Journal LLM
  auth/             page de connexion (multi-comptes)
```

## Principes

- **Un espace, une vue.** `js/app.js` inscrit chaque espace (`registerSpace`) ; le cadre ne
  connaît aucun écran. Un espace peut aussi inscrire un pied ou un en-tête de menu, un
  bouton dans l'en-tête d'un dossier, un encart sous le détail d'une offre : ce sont ces
  inscriptions qui évitent les imports croisés.
- **Repeints ciblés.** L'état vit dans `core/store.js` ; qui le modifie appelle
  `notify(sujet)`, et seuls les abonnés à ce sujet se repeignent. Une frappe dans un champ
  ne reconstruit jamais ce champ.
- **Le serveur filtre, trie et vérifie.** Aucun tri ni filtrage de liste en JavaScript ;
  le score, les contrôles d'un document, la validation du profil viennent de l'API.
- **Aucun style en ligne, aucun `innerHTML`.** `el()` refuse l'attribut `style` ; les seules
  valeurs calculées (position d'une zone sur l'aperçu) passent par des propriétés CSS.
- **Chaque écran a ses états** de chargement, de liste vide et d'erreur, avec un message
  utile et, si possible, « Réessayer ».
- **Clavier et lecteurs d'écran** : focus visible, rôles et libellés ARIA, dialogues
  natifs `<dialog>`, raccourcis qui se taisent quand on écrit dans un champ.

## L'aperçu cliquable du tiroir Postuler

Le navigateur n'interprète aucun PDF. Le serveur renvoie, issus de la même compilation
Typst, une image PNG par page et la boîte de chaque zone retouchable, en **pourcentages**
de la page. Le front pose des boutons aux positions reçues par-dessus l'image : les zones
restent alignées à toute largeur, sans calcul. Aperçus et PDF sont récupérés avec le jeton
puis exposés en URL `blob:` (un `<img src>` n'emporterait pas l'en-tête).

## Tests

Pas de moteur JavaScript dans la suite : `tests/test_front.py` vérifie les câblages sur
les modules réellement servis (jeton sur chaque appel, imports résolus, aucun style en
ligne, gestes annulables, génération au clic…). Le rendu se vérifie à l'œil, en comparant
à la maquette.
