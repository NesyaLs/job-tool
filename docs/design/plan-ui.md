# Nouvelle interface : inventaire et plan

Documents lus : `project_ctx.md`, `README.md`, `Interface/README.md`, tout le code de
`Interface/` (11 écrans, ~5 000 lignes de JS), les routers de `src/jobtool/api/` et
les tests qui lisent le front (`tests/test_api.py`, `tests/test_offers_stack.py`,
`tests/test_profile_photo.py`, `tests/test_template_previews.py`).

Cible visuelle : [`maquette_reference_carnet.html`](maquette_reference_carnet.html).
Choix par défaut : [`decisions-ui.md`](decisions-ui.md).

## 1. Inventaire : chaque fonctionnalité actuelle et sa nouvelle place

Légende des places : **O›** espace Offres, **P›** espace Profil, **T** tiroir Postuler,
**A** menu de l'avatar, **H** barre du haut.

### Démarrage, session, cadre

| Fonctionnalité actuelle | Appels API | Nouvelle place |
| --- | --- | --- |
| Lecture de la config d'authentification, choix connexion / application | `GET /auth/config` | Inchangé (démarrage) |
| Connexion et création de compte (Supabase, en direct) | Supabase `/auth/v1/token`, `/auth/v1/signup` | Page de connexion au style Carnet |
| Jeton posé sur chaque appel, 401 = retour à la connexion | tous (via `authFetch`) | Module unique `js/api/client.js` |
| Déconnexion, e-mail du compte affiché | — | **A** (déconnexion en multi-comptes seulement) |
| Barre latérale : 8 entrées à plat | — | Interrupteur **Offres \| Profil**, et un menu propre à chaque espace |
| Sélecteur de profil actif (admins, dès 2 profils) | `GET /profiles`, `PUT /profiles/active` | En haut de l'espace **P** |

### Écran « Offres »

| Fonctionnalité actuelle | Appels API | Nouvelle place |
| --- | --- | --- |
| Liste paginée des offres `new` (15 par page, total `X-Total-Count`) | `GET /offers?status=new&order=posted_date&limit&offset` | **O› Nouvelles**, triées par score (voir § 3, route ajoutée) |
| Recherche « Quoi ? » (mots dans l'intitulé ou l'entreprise) | `GET /offers?q=` | **H** recherche : filtre le dossier ouvert (`q`) |
| Sélecteur de lieu : régions et départements | `GET /offers?departments=&country=FR` | Pastille « Lieu » en haut de la liste, qui ouvre le même sélecteur |
| Contrat | `GET /offers?contract=` | Pastille « Contrat » |
| Inclure les offres sans lieu précis | `include_unknown_location` | Case dans le panneau de la pastille « Lieu » |
| Filtres d'affinage (titre/entreprise, lieu texte) | `keyword`, `location` | Remplacés par la recherche **H** (`q`) et la pastille Lieu. Le paramètre `location` (texte libre) reste disponible dans le panneau Lieu |
| Fraîcheur (24 h, 7 j, 30 j) | `max_age_days` | Pastille « Date » |
| Actualiser les offres (collecte) et suivi de progression | `POST /aggregate`, `GET /aggregate/{id}`, `GET /aggregate/latest` | Collecte automatique au démarrage si le catalogue date de plus de 6 h. État dans **H** (« Offres à jour il y a 2 h »), bouton discret dans **A** |
| Favori (cœur) | `PATCH /offers/{id}/favorite` | Action « Favori » du détail, touche F, avec « Annuler » |
| Masquer | `PATCH /offers/{id}/status {seen}` | Action « Masquer » du détail, touche H, avec « Annuler » |
| Déplier une offre : description et liens de toutes les sources | (données de la liste) | Détail à droite : description et « Aussi publiée sur … » |
| Bloc « Mes favoris » | `GET /offers?favorite=true` | Dossier **O› Favoris** |
| Ajouter une offre à la main (titre et lien obligatoires, 409 si déjà connue) | `POST /offers` | Bouton « + Ajouter une offre » en haut de la liste, formulaire en dialogue |

### Écran « Découvrir »

| Fonctionnalité actuelle | Appels API | Nouvelle place |
| --- | --- | --- |
| Pile de cartes triées par compatibilité, préchargement | `GET /offers/stack` | Mode **Tri rapide**, lancé par un bouton dans Nouvelles |
| Glisser à droite = favori, à gauche = masquer (souris et tactile) | `PATCH …/favorite {favorite}`, `PATCH …/status` | Idem dans le Tri rapide |
| Touches ← et →, annuler (Retour arrière, Ctrl+Z) | idem, écriture inverse | Idem |
| Jauge du score et détail par critère | (réponse de la pile) | Carte du Tri rapide ; le détail par critère alimente aussi la ligne « pourquoi » du détail d'offre |
| Critères propres à la pile (mots-clés, lieu, contrats, fraîcheur) | paramètres de `/offers/stack` | Repris des pastilles de Nouvelles (mêmes critères partout) |
| Pile vide : élargir la recherche, actualiser | — | Idem dans le Tri rapide |

### Écran « Postuler » et écran « Document »

| Fonctionnalité actuelle | Appels API | Nouvelle place |
| --- | --- | --- |
| Liste des favoris non postulés, détail complet | `GET /offers?favorite=true`, `GET /offers/{id}` | **O› Favoris** et le détail d'offre |
| Description éditable, enregistrée à la perte du focus | `PATCH /offers/{id}/description` | Détail d'offre (description modifiable) |
| Copier la description | — (presse-papiers) | Détail d'offre, bouton « Copier » près de la description |
| Retirer des favoris | `PATCH /offers/{id}/favorite` | Action « Favori » (bascule) |
| Choix de la mise en page du CV : cartes, miniatures, agrandissement, badge bêta | `GET /documents/templates` | **T** onglet CV : choix du template (défaut = celui du profil, **P› Templates**) |
| Options de la mise en page, construites depuis le serveur | (réponse de `/documents/templates`) | **T** : panneau d'options sous le choix du template |
| Choix de la mise en page de la lettre | `GET /documents/templates?kind=letter` | **T** onglet Lettre |
| Générer le CV ou la lettre (reprise du brouillon s'il existe) | `POST /documents` | **T** bouton « Générer », au clic seulement |
| Aperçu PNG des pages | `GET /documents/{id}/preview/{n}.png` (en blob, avec jeton) | **T** aperçu |
| Bandeau de validité (pages, ATS, budget, traçabilité) | (réponse du document) | **T** : coches « 1 page », « Aucun chiffre inventé », « Lisible par les ATS » |
| Modifier le texte : zones cliquables sur l'aperçu, enregistrer, revenir au texte généré, tout réinitialiser | `PATCH /documents/{id}/fields`, `POST /documents/{id}/reset` | **T** bouton « Retoucher » |
| Mode pro : données JSON, recompiler, revenir aux données générées | `GET/PUT /documents/{id}/source`, `POST /documents/{id}/source/reset` | **T** « … › Mode avancé » |
| Changer la mise en page d'une lettre sans rappeler le modèle | `PUT /documents/{id}/template` | **T** onglet Lettre, sélecteur de template |
| Régénérer (confirmation si retouches) | `POST /documents {force_new: true}` | **T** « Régénérer » |
| Télécharger le PDF | `GET /documents/{id}/pdf` (en blob) | **T** « Télécharger » |
| Valider « j'ai postulé » | `PATCH /offers/{id}/status {applied}` | **T** bouton « J'ai postulé » (clic obligatoire) et action « Postuler » du détail qui ouvre **T** |

### Écrans « Candidatures » et « Masquées »

| Fonctionnalité actuelle | Appels API | Nouvelle place |
| --- | --- | --- |
| Historique des candidatures, date de candidature, lien | `GET /offers?status=applied&order=applied_at` | **O› Candidatures** |
| Offres masquées, lien, badge favori | `GET /offers?status=seen` | **O› Masquées** (en gris) |
| Récupérer une offre masquée | `PATCH /offers/{id}/status {new}` | Action « Masquer » du détail, qui devient « Remettre » dans Masquées |

### Écran « Profil » et entretien

| Fonctionnalité actuelle | Appels API | Nouvelle place |
| --- | --- | --- |
| Nom, prénom, téléphone, e-mail | `GET/PUT /profile` | **P› Identité** |
| Photo : envoi, aperçu rond, remplacement, suppression | `PUT/GET/DELETE /profile/photo` | **P› Identité** |
| Soft skills et hard skills (pastilles) | `PUT /profile` | **P› Compétences**, regroupées par catégorie |
| Langues | `PUT /profile` (« Nom — Niveau ») | **P› Langues** : langue et niveau séparés |
| Expériences et projets : intitulé, organisation, type, dates, résumé, réalisations et leurs compétences, ajout et suppression | `PUT /profile` | **P› Expériences** : cartes à gauche, éditeur à droite (une réalisation par ligne ; résumé, type et compétences d'une réalisation gardés dans l'éditeur) |
| Bouton « Enregistrer le profil » | `PUT /profile` | Enregistrement automatique, avec « Annuler » |
| Gestion des profils (admins) : créer vide ou par copie, renommer, supprimer, activer | `GET/POST /profiles`, `PATCH/DELETE /profiles/{key}`, `PUT /profiles/active` | Sélecteur en haut de **P**, avec un menu « Gérer les profils » |
| Entretien guidé : conversation, plan des sections, progression, passer ou aller à une section, recommencer | `GET /interview`, `POST /interview/messages`, `/restart`, `/sections/{s}/goto`, `/sections/{s}/skip` | Bouton permanent « Entretien IA » en bas du menu **P** : panneau conversation à gauche, propositions à droite |
| Propositions : accepter, modifier puis accepter, refuser, avertissements bloquants | `POST /interview/proposals/accept`, `/reject` | Colonne de droite du panneau : Accepter, Modifier, Refuser |
| Récapitulatif : accepter ou refuser une sélection | idem | Colonne de droite : « Tout accepter » sur la sélection |
| Profil en direct pendant l'entretien, traçabilité vers le message d'origine | (réponse de l'entretien) | Colonne de droite, sous les propositions |

### Administration et Journal LLM

| Fonctionnalité actuelle | Appels API | Nouvelle place |
| --- | --- | --- |
| Vue d'ensemble : comptes, volumes, base | `GET /admin/overview` | **A › Administration** (admins seulement), contenu inchangé |
| Journal LLM : liste, détail des échanges | `GET /admin/llm-runs`, `GET /admin/llm-runs/{id}` | **A › Journal LLM** (admins seulement), contenu inchangé |

Protection côté serveur, vérifiée : les trois routes `/admin/*` dépendent de
`require_admin` (`src/jobtool/api/routers/admin.py`), qui renvoie 403 à un compte non
admin (tests existants dans `tests/test_admin.py` et `tests/test_llm_journal.py`). Les
écritures sur `/profiles` renvoient aussi 403 sans droits. Masquer les liens ne sert
qu'au confort ; la sécurité reste au serveur.

## 2. Le logo (`jobtool-logo/`)

Fichiers d'origine, jamais modifiés :

| Fichier | Variante | Format |
| --- | --- | --- |
| `jobtool-symbole-couleur.svg` | symbole seul, couleur | SVG 32 × 32 |
| `jobtool-symbole-mono.svg` | symbole seul, monochrome (#1E4436) | SVG 32 × 32 |
| `jobtool-symbole-sombre.svg` | symbole seul, pour fond sombre | SVG 32 × 32 |
| `jobtool-logo-couleur.svg` | symbole + nom, couleur | SVG (nom vectorisé) |
| `jobtool-logo-mono.svg` | symbole + nom, monochrome | SVG |
| `jobtool-logo-sombre.svg` | symbole + nom, pour fond sombre | SVG |
| `favicon.svg` | symbole, bascule clair/sombre (`prefers-color-scheme`) | SVG |
| `favicon.ico` | symbole, 16 et 32 px | ICO |
| `apple-touch-icon.png` | symbole, 180 px | PNG |

Couleurs du logo : vert #2F8F62, vert profond #1E4436, nom #1B231F. Elles diffèrent
légèrement de la palette Papier vert : le logo est gardé tel quel.

Utilisation, sur des copies placées dans `Interface/assets/logo/` :
- barre du haut : `jobtool-logo-couleur.svg` (symbole + nom), à la place du logo de la
  maquette ;
- favicon : `favicon.svg`, `favicon.ico` (16, 32) et `apple-touch-icon.png` (180). Toutes
  les tailles existent déjà, rien à générer ;
- page de connexion : `jobtool-logo-couleur.svg`.

## 3. Structure du nouveau front

Toujours dans `Interface/` (dossier servi par l'API, rien à changer côté serveur) :

```
Interface/
  index.html
  css/        tokens.css (couleurs, polices), base.css, layout.css, components.css,
              offres.css, drawer.css, profil.css, interview.css, admin.css, auth.css
  fonts/      Fraunces et Inter en woff2 (OFL), auto-hébergées
  js/
    app.js            démarrage : config d'auth, session, montage, collecte auto
    api/client.js     SEUL module qui parle au réseau
    core/             state (store), router (espaces et sections), dom, format, keys, toast
    shell/            topbar, sidebar, avatar-menu
    offres/           folders, list, detail, filters, location-picker, manual-offer, triage
    postuler/         drawer, template-picker, preview, retouche, advanced
    profil/           identity, experiences, skills, languages, templates, profiles, completeness
    interview/        panel
    admin/            overview, journal
    auth/             login
  assets/templates/   miniatures des templates (inchangées)
```

L'ancien front (`app.js`, `data.js`, `screens/`, `styles.css`…) est retiré à l'étape 7.

## 4. Manques côté API

### Ajoutés dans cette passe (additifs, rétrocompatibles, testés)

| Ajout | Pourquoi | Détail |
| --- | --- | --- |
| `GET /offers/ranked` | Nouvelles doit être trié par score, avec des filtres durs (pastilles) et la recherche. `/offers` ne note pas les offres ; `/offers/stack` note mais ses critères lieu et contrat sont souples, et il écarte les offres sous 25 %. Les lignes de tous les dossiers affichent aussi un score. | Mêmes filtres que `/offers`, plus `keywords` (critère de score) et `order` (`score`, `posted_date`, `applied_at`). Renvoie des `StackCard` (offre, score, détail par critère), paginées, total dans `X-Total-Count` |
| `GET /auth/me` | Le front doit savoir si le compte est admin pour afficher Administration et Journal LLM | `{is_admin}`. La garde reste `require_admin` sur les routes `/admin/*` |
| `hidden_at` dans `OfferSummary` | Masquées affiche depuis quand l'offre est masquée | Colonne déjà en base (`user_offers.hidden_at`), il ne manquait que sa lecture |

### Notés pour plus tard (demandent une migration ou un chantier serveur)

| Besoin | Ce qui manque | Contournement dans cette passe |
| --- | --- | --- |
| Offre « lue » / « non lue » | Aucun état « ouverte » par compte (`user_offers` n'a que favori, statut, dates de candidature et de masquage) | Les offres ouvertes sont retenues dans le navigateur (`localStorage`), par poste |
| Tri des Favoris par date d'ajout | Pas de colonne `favorited_at` | Favoris triés par date de publication |
| Statut de suivi d'une candidature (envoyée, relancée, entretien, refus) | Le statut `applied` n'a pas de sous-état ; `rejected` existe mais n'est pas daté | Candidatures affiche seulement la date. À prévoir : colonne `application_stage` et son historique |
| Délai avant purge d'une offre masquée | La purge porte sur le catalogue (30 jours sans parution, `catalogue_retention_days`) ; une offre masquée n'a pas d'échéance propre, et `last_seen_at` n'est pas exposé | Masquées affiche « masquée il y a N j » et rappelle la règle de purge du catalogue |
| Template par défaut du profil | Aucun champ de profil pour la mise en page préférée | Retenu dans le navigateur (`localStorage`), comme l'ancien front le faisait déjà |
| « Mis en avant » dans l'aperçu du CV | Le document ne dit pas quelles expériences ou réalisations ont été retenues | Ligne absente ; à exposer depuis le `render_input` (ids retenus) |
| « Pourquoi » fondé sur le profil | Le score mesure l'adéquation aux critères de recherche (mots-clés, lieu, contrat, fraîcheur), pas au profil | La ligne « pourquoi » reprend le détail par critère renvoyé par le serveur |
| Compétences demandées par l'offre | Aucune extraction côté serveur | Encart « Ajouter au profil » : un petit vocabulaire de compétences courantes, cherché dans la description et comparé au profil |
| Savoir si un document existe déjà pour une offre, sans générer | `POST /documents` reprend le brouillon **ou** génère | Le tiroir ne montre l'aperçu qu'après un clic sur « Générer » (qui rouvre le brouillon existant sans rappeler le modèle) |
| Niveau de langue structuré | Le profil transporte « Nom — Niveau » en texte | Le front sépare et recompose sur ce séparateur |

## 5. Tests du front à migrer

Les tests actuels lisent le texte du JS (« `actFavorite` est présent »). À l'étape 7, ils
sont réécrits dans `tests/test_front.py` pour garder les mêmes garanties sur le
nouveau code :

- chaque appel à l'API passe par le client qui pose le jeton (aucun `fetch` direct) ;
- le lien externe s'ouvre avec `rel="noopener noreferrer"`, défini à un seul endroit ;
- masquer (`seen`) a son inverse (`new`) ; favori ; description enregistrée ;
- génération explicite (au clic), régénération confirmée, réponse périmée ignorée ;
- aperçu en PNG, PDF téléchargé en blob ;
- options et templates construits depuis le serveur (aucun template codé en dur) ;
- filtres envoyés au serveur, pagination par `X-Total-Count` ;
- photo envoyée en `FormData`, aperçu versionné ;
- tri rapide : gestes souris et tactile, flèches, mouvement réduit ;
- front servi en `no-cache`, point d'entrée en module ES.

`test_front_previews_generated_pdfs` échoue sur master depuis longtemps (il attend un
lien `href` alors que le PDF est téléchargé en blob). Sa réécriture sur le nouveau front
le fera repasser.

## 6. Étapes

0. Inventaire et plan (ce document).
1. Variables CSS, polices, cadre : barre du haut, menu, interrupteur, navigation.
2. Espace Offres : dossiers, liste, détail, actions, raccourcis, « Annuler ».
3. Tiroir Postuler : génération, retouche, mode avancé, « J'ai postulé ».
4. Espace Profil : cinq sections, enregistrement automatique, entretien IA.
5. Tri rapide, jauge du profil, encart « Ajouter au profil ».
6. Menu de l'avatar (admin, Journal LLM, collecte), page de connexion.
7. Nettoyage : retrait de l'ancien front, tests, relecture.
