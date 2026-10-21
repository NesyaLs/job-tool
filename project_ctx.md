# jobtool — contexte du projet

## 1. L'idée

Chercher un emploi coûte surtout du temps *répétitif* : ouvrir cinq sites, relire des
annonces déjà vues, retailler son CV pour chacune, réécrire la même lettre en changeant le
nom de l'entreprise. **jobtool automatise ce qui est répétitif et laisse à l'humain ce qui
demande un jugement.**

Concrètement, l'outil :

1. **ramène les offres** de plusieurs sources dans un catalogue unique, sans doublons ;
2. **les rend cherchables** sur des critères homogènes (lieu, contrat, mots-clés), alors que
   chaque source les écrit à sa façon ;
3. **génère un CV et une lettre adaptés à une offre**, à partir d'une base d'expériences
   écrite une fois pour toutes ;
4. **garde la trace** de ce qui a été vu, aimé, masqué, postulé.

Ce n'est pas un produit grand public : c'est un outil personnel, utilisable seul sur son
ordinateur ou hébergé pour quelques comptes.

## 2. Le principe qui tient tout le reste

> **Le LLM propose, le code vérifie, la boucle corrige.**

Le modèle fait du **jugement** : classer des expériences par pertinence, reformuler une
phrase, choisir un angle. Le code mesure les **bornes dures** : combien de caractères, combien
de pages, quel texte est extractible du PDF, quel fait est tracé jusqu'à la base
d'expériences. **Le LLM ne compte ni ne mesure jamais.** Quand une borne est violée, le code
ne répare pas : il redemande au modèle, avec le manquement précis, dans une boucle bornée.

Corollaire **anti-fabrication** : chaque fait d'un CV ou d'une lettre est tracé vers la base
par un identifiant stable. Un chiffre qui n'existe pas dans la source ne peut pas apparaître
dans un document.

Le même principe gouverne la collecte : le code normalise, mesure et dédoublonne ; aucun
modèle n'intervient.

## 3. Deux modes, un seul interrupteur

| | Mono-poste | Multi-comptes |
|---|---|---|
| Base | fichiers SQLite locaux | Postgres partagé (Supabase) |
| Comptes | un seul, implicite | un par utilisateur, authentifié |
| Déclencheur | `DATABASE_URL` absente | `DATABASE_URL` présente |
| Authentification | aucune | `SUPABASE_JWKS_URL` (jetons vérifiés par clé publique) |

**Aucun code métier ne sait dans quel mode il tourne.** Il reçoit un moteur ou un chemin de
fichier, et une identité de compte. C'est `core/db/engine.database_for` qui tranche, à un
seul endroit. En mono-poste, le schéma est créé par le code ; en Postgres, il appartient à
Alembic.

## 4. Invariants d'architecture

**Sens des dépendances**, jamais l'inverse :

```
cli/ , api/  →  services/  →  domaines (aggregator, documents, interview)  →  core/
```

- `cli/` et `api/` ne contiennent **aucun SQL** : ils reçoivent, appellent un service,
  renvoient du texte ou du JSON ;
- `services/` est **printless** (aucun `print`) et n'importe aucune couche d'interface ;
- un domaine n'importe jamais un autre domaine : ce qu'ils partagent vit dans `core/`.

**Multi-comptes** : `user_id` est un paramètre **explicite, sans valeur par défaut** dans le
stockage. Un défaut transformerait un oubli de câblage en fuite silencieuse d'un compte vers
un autre ; sans défaut, l'oubli est une `TypeError` immédiate. L'identité et la base arrivent
toujours par `Depends` — une dépendance appelée à la main sort du graphe FastAPI et ne peut
plus être surchargée en test.

**Coût des accès distants** : sur Postgres, un aller-retour SQL coûte ~150 ms. C'est le
*nombre* de requêtes, pas leur complexité, qui décide de la durée. Toute écriture portant sur
une collection passe par un chemin groupé ; une boucle qui écrit ligne par ligne est un
défaut, même si elle est correcte.

## 5. Carte du code

```
src/jobtool/
├── aggregator/        DOMAINE : offres (collecte, catalogue, dédoublonnage)
│   ├── francetravail/ adzuna/ smartrecruiters/ workday/   un connecteur par source
│   ├── config.py      lecture du config.yaml (recherches, sources, ATS)
│   ├── registry.py    nom de source → fabrique de connecteur
│   ├── pipeline.py    une collecte : sources en parallèle → catalogue
│   ├── geo.py         lieu normalisé (pays, région, département, ville)
│   ├── contracts.py   contrat normalisé (cdi, cdd, stage, alternance…)
│   ├── dedup.py       règles PURES de dédoublonnage (clés, similarité, priorités)
│   ├── catalogue.py   SQL du catalogue : occurrences, offre canonique, fusions, purge
│   ├── storage.py     SQL de la vue d'UN compte (recherche + état)
│   ├── runs.py        suivi et verrou des collectes
│   └── matching.py    score de compatibilité d'une offre (dossiers, tri rapide)
├── documents/         DOMAINE : CV et lettres
│   ├── cv/            schéma, prompts, checks, remplissage mesuré, rendu Typst
│   ├── cover_letter/  passe 1 (routage/faits) puis passe 2 (rédaction), rendu
│   └── drafts/        brouillons de retouche manuelle
├── interview/         DOMAINE : entretien guidé qui construit la base d'expériences
├── core/
│   ├── db/            schéma unique (code + Alembic), moteurs, écritures groupées
│   ├── experience_base/  la base d'expériences : fichier YAML ou ligne en base
│   ├── llm/           appel Mistral, retry 429, boucle de correction, journal
│   └── rendering/     Typst (polices embarquées), extraction PDF
├── services/          orchestration, sans print ni SQL d'interface
├── api/               FastAPI : routers + deps (composition root)
└── cli/               Typer : aggregate, batch, cv, letter, api, catalogue
Interface/             front statique servi par l'API (ES modules, sans build)
```

## 6. Le catalogue d'offres

### 6.1 Collecte et recherche sont deux gestes distincts

| | Collecte | Recherche |
|---|---|---|
| Rôle | interroger les sources, remplir le catalogue | filtrer le catalogue déjà en base |
| Déclenchement | `jobtool aggregate`, au démarrage de l'interface si les offres datent de plus de 6 h, ou le menu de l'avatar | la recherche et les pastilles de l'interface |
| Durée | ~1 min 40 s pour ~1 400 offres, en arrière-plan | immédiat (une requête SQL) |
| Effet sur l'état | **aucun** | **aucun** |

Le catalogue est **commun à tous les comptes** : une offre collectée est visible par tous.
Ce qui appartient à un compte, ce sont ses gestes.

### 6.2 Ce qui est interrogé — `data/aggregator/config.yaml`

```yaml
criteres:                       # valeurs par défaut des recherches
  departement: "75,92,93,94"
  max_results: 150
recherches:                     # France Travail et Adzuna s'interrogent par mots-clés
  - motsCles: "data scientist"
  - motsCles: "data analyst"
sources:                        # activation et plafond, source par source
  france_travail: {enabled: true, max_results: 150}   # par RECHERCHE
  smartrecruiters: {enabled: true, max_results: 400}  # au TOTAL
smartrecruiters:                # les ATS n'ont pas de mots-clés : tout le catalogue
  companies: [BoschGroup, ALTEN, Devoteam, ...]
  country: fr
  max_per_company: 60
workday:
  sites: [https://bdf.wd103.myworkdayjobs.com/fr-FR/recrutement-banque-de-france, ...]
  country: France
```

Les quatre sources :

| Source | Accès | Filtrage | Particularité |
|---|---|---|---|
| **France Travail** | OAuth (`.env`) | mots-clés + départements INSEE | 204 = aucun résultat, pas une erreur |
| **Adzuna** | clés en query (`.env`) | mots-clés, **national** | description = extrait seulement |
| **SmartRecruiters** | API publique | par entreprise + pays | un identifiant faux répond 200 + liste vide |
| **Workday** | API publique | par site carrière + pays | `limit` plafonné à 20, `total` fiable page 0 |

Les ATS demandent un appel de détail par offre (N+1), borné par les plafonds. Pour ajouter
une entreprise, le `config.yaml` donne la commande `curl` de vérification ;
`data/aggregator/entreprises_ats.csv` liste 1 168 entreprises avec leurs volumes par pays.

### 6.3 Déroulé d'une collecte

1. **Inscription et verrou** (`aggregator/runs.py`) : une ligne `collection_runs`. Une seule
   collecte à la fois (sinon HTTP 409, avec l'identifiant à suivre), pas plus d'une par quart
   d'heure depuis l'interface (429 + `Retry-After`) ; la CLI n'est pas soumise au délai. Une
   collecte interrompue est libérée après 2 h.
2. **Interrogation en parallèle** (`pipeline.fetch_all`) : un fil par source, les recherches
   d'une source à la suite. Chaque source garde son limiteur de débit — le parallélisme
   n'accélère que ce qui ne se gêne pas. Une source en panne n'interrompt ni la collecte ni
   les autres : son erreur est notée.
3. **Normalisation** de chaque offre (§ 6.4).
4. **Écriture au catalogue** avec dédoublonnage (§ 6.5), en une dizaine de requêtes pour
   toute la collecte.
5. **Purge** des offres expirées (§ 6.7), puis enregistrement du bilan.

L'avancement est écrit en base au fil de l'eau : l'interface affiche « Actualisation en cours
(2/4 sources) — France Travail 387 · Adzuna 400 · SmartRecruiters … » en interrogeant
`GET /aggregate/{id}` toutes les 2 s.

### 6.4 Normalisation (déterministe, sans LLM)

**Le lieu** (`aggregator/geo.py`) : pays, région, département, ville, et une **précision**
(`commune`, `commune_homonyme`, `departement`, `region`, `pays`, `inconnu`). On lit d'abord
les champs propres à la source — code INSEE chez France Travail, hiérarchie `area` chez
Adzuna, code postal chez SmartRecruiters, ville + pays chez Workday — puis on retombe sur le
texte libre (« 75 - Paris », « Lyon (69) », « 69003 Lyon »). Le référentiel des
34 969 communes est **embarqué dans le paquet** (`aggregator/geo_data/`, généré par
`scripts/build_geo_referential.py`) : aucun appel réseau. Les homonymes prennent la commune la
plus peuplée, et c'est tracé par la précision.

**Le contrat** (`aggregator/contracts.py`) : `cdi`, `cdd`, `stage`, `alternance`, `interim`,
`freelance`, `inconnu`. Ordre de confiance : l'**intitulé** d'abord pour stage et alternance
(SmartRecruiters classe « Apprenti Développeur » en `permanent`), puis les champs structurés
de la source, puis le texte libre du contrat. La **description n'est jamais lue** (« après un
stage… » n'est pas un stage) et les segments niés de l'intitulé sont retirés (« hors stage et
alternance »). Le temps de travail (plein / partiel) est rangé à part : ce n'est pas un
contrat.

### 6.5 Dédoublonnage entre sources

Modèle : chaque **occurrence** (une source, un identifiant natif) garde son contenu propre et
pointe vers une **offre canonique**, recalculée depuis ses occurrences selon la priorité
ATS > France Travail > Adzuna > saisie manuelle. Rien n'est supprimé pour dédoublonner ; tous
les liens de candidature sont conservés.

```
offer_occurrences ──► offers ──► user_offers   (l'état de chaque compte)
        │                │
        │                └── offer_aliases     (ancien identifiant → offre absorbante)
        └── occurrence_urls                    (toutes les identités d'URL)
```

Trois niveaux, du plus sûr au moins sûr :

1. **identifiant** — même source, même identifiant natif : l'occurrence est mise à jour ;
2. **lien** — même URL normalisée (paramètres de suivi retirés, liens ATS ramenés à leur
   identifiant : `smartrecruiters:boschgroup:744…`, `workday:thales:R0339415`). France Travail
   relaie des offres de partenaires dont le lien d'origine est celui de l'ATS : c'est ce lien
   qui les réunit. Un lien **générique** exige en plus un intitulé quasi identique ;
3. **empreinte approchée** — conditions **cumulatives** : entreprise normalisée identique et
   non vide, même département (ou ville), contrats compatibles, dates à moins de 21 jours,
   intitulés normalisés similaires à **0,90** ou plus.

**Prudence** : fusionner deux offres différentes est pire que laisser passer un doublon. D'où
les refus explicites — entreprise vide (fréquent chez France Travail), deux annonces d'une
**même source**, villes différentes, stage contre CDI, publications trop éloignées.

Chaque rattachement garde son **niveau et son score**. Lors d'une fusion, l'état des comptes
est reporté sans perte (favori si l'une l'était, statut le plus engagé, date de candidature la
plus ancienne) et les anciens identifiants restent résolus.

### 6.6 Recherche et filtres

Tout est filtré **par la base** (`storage._job_filters`), jamais en Python après coup : c'est
la condition pour que la pagination et le compteur « 15 sur 369 » soient justes.

| Paramètre de `GET /offers` | Effet |
|---|---|
| `q` | **chaque mot** doit figurer dans l'intitulé ou l'entreprise, sans casse ni accents |
| `departments` | codes INSEE sur le lieu **normalisé** ; le télétravail complet passe toujours |
| `include_unknown_location` | garde les offres au lieu inexploitable (écartées par défaut) |
| `country` | pays ISO2 ; l'interface envoie `FR` pour « Toute la France » |
| `contract` | familles normalisées, répétable |
| `max_age_days` | publiée dans les N derniers jours (les offres sans date sont écartées) |
| `keyword`, `location` | sous-chaînes du texte d'origine (filtres d'affinage) |
| `status`, `favorite` | l'état du compte ; **absence de ligne = `new`** |
| `source` | une source exacte |
| `order`, `limit`, `offset` | tri en liste blanche ; total dans l'en-tête `X-Total-Count` |

Deux règles s'ajoutent toujours : la **visibilité** (catalogue commun + offres privées du
compte) et l'**expiration** (une offre expirée disparaît, sauf pour le compte qui y tient).

### 6.7 Expiration et purge

En fin de collecte : une offre qu'aucune collecte n'a revue depuis **30 jours**, alors que
**toutes ses sources ont été collectées avec succès** entre-temps, est expirée. La seconde
condition compte : ne plus voir l'offre d'une source en panne ne prouve rien.

Une offre expirée est supprimée, **sauf** si un compte y tient — favorite, postulée, refusée,
décrite à la main, ou citée par un document généré. Elle est alors conservée et signalée
`expired_at`, visible de ce seul compte. Une nouvelle apparition efface la marque. Une offre
simplement masquée n'est pas protégée : masquer, c'est déjà la rejeter.

### 6.8 L'état d'un compte

Une ligne de `user_offers` n'existe que si le compte a **fait quelque chose** de l'offre :
favori, masquage (`seen`, récupérable), candidature (`applied`, horodatée), refus
(`rejected`), description corrigée. Les offres **saisies à la main** sont privées à leur
auteur (`offers.owner_user_id`) et ne sont jamais candidates au dédoublonnage du catalogue
commun.

## 7. Génération de CV et de lettres

### 7.1 La matière première : la base d'expériences

Un YAML (`data/moi.yaml` en mono-poste) ou une ligne de `experience_bases` (multi-comptes) :
profil, expériences, projets, compétences, formations. Chaque élément porte un **identifiant
stable**, qui sert de preuve de traçabilité dans les documents générés.

### 7.2 Le CV

Deux appels LLM **indépendants**, puis un remplissage **mesuré** :

- **appel A** — un *réservoir* d'expériences et de bullets **classé par pertinence** ; le
  modèle classe large, il ne décide pas combien tiennent sur la page. Boucle de correction :
  traçabilité, longueur des bullets, plancher de projets ;
- **appel B** — les compétences (techniques, humaines, centres d'intérêt), ordonnées ; boucle
  de correction : traçabilité vers les listes maîtres, plafonds de comptage ;
- **remplissage** (`cv/fill.py`) — le code part d'un plancher obligatoire puis ajoute les
  éléments dans l'ordre de pertinence **tant que le PDF rendu tient sur une page**. Le PDF
  mesuré est la vraie borne, pas une estimation de caractères.

Corriger une famille ne peut pas casser l'autre : les deux appels sont étanches. Les checks
finaux sont recalculés sur le CV **rempli**.

Sept templates Typst (`cv_basic`, `cv_classic`, `cv_cobalt`, `cv_compact`, `cv_lavandula`,
`cv_metronic`, `cv_modern_cv`) ; quatre ont une fiche dans `docs/templates/`. Une vérification ATS
s'assure que le texte attendu est réellement **extractible** du PDF (`pdftotext`).

### 7.3 La lettre

Deux passes étanches :

- **passe 1** — routage et sélection des faits : quel mode (projet, expérience, générique),
  quels identifiants d'achievements mobiliser ;
- **passe 2** — rédaction des paragraphes. Bornes dures vérifiées par le code
  (`draft_checks.py`) : aucun placeholder résiduel (`[entreprise]` qui partirait à l'envoi),
  plafond de caractères par champ, cohérence mode/inserts, et **anti-chiffre inventé** —
  aucun nombre absent des achievements sources ne peut apparaître.

Deux templates (`lettre`, `lettre_moderne`).

### 7.4 Retouche manuelle

Une fois le document généré, il est éditable **sans LLM** : chaque enregistrement réapplique
les retouches au `render_input` d'origine, relance Typst et **rejoue les bornes dures** (une
page, extractibilité ATS, budget du template, traçabilité). Le brouillon est persisté en base ;
le PDF et ses aperçus PNG sont des artefacts dérivés, reconstructibles.

### 7.5 Observabilité du modèle

Deux chemins indépendants : `llm_usage` **compte** les appels et les jetons (la comptabilité
doit survivre à tout), `llm_runs` / `llm_exchanges` en gardent la **trace** (ce qui est parti,
ce qui est revenu) pour l'onglet d'administration. Un retry avec back-off couvre les 429 du
palier gratuit Mistral, de façon transparente pour la boucle de correction.

## 8. Entretien guidé et profils

**L'entretien** (`interview/`) construit la base d'expériences en dialoguant : le modèle pose
des questions, propose des ajouts, et l'utilisateur accepte ou rejette chaque proposition. Un
tour ne modifie la session qu'en cas de succès — si le modèle échoue, rien n'est perdu.

**Les profils multiples** (administrateurs) : plusieurs bases d'expériences pour un même
compte, avec leur photo. Le profil actif est résolu **une fois par requête** et injecté dans
tous les dépôts (base, photo, documents, entretien).

## 9. L'interface web

HTML / CSS / JavaScript **vanilla**, modules ES natifs, **aucun build, aucun npm**. Servie par
l'API en même origine (donc sans CORS). Style « Carnet », palette « Papier vert » : maquette,
plan et choix par défaut dans `docs/design/` ; organisation du code dans `Interface/README.md`.

Deux espaces, et un tiroir :

- **Offres** — une boîte de réception : Nouvelles (triées par score), Favoris, Candidatures,
  Masquées ; liste à gauche, détail à droite, gestes au clavier et annulables ; « Tri
  rapide » (l'ancienne pile « Découvrir », notée par `matching.py`) ;
- **Profil** — Identité, Expériences, Compétences, Langues, Templates, enregistrés
  automatiquement ; l'entretien IA en panneau ;
- **Postuler** — un tiroir CV | Lettre : génération au clic, contrôles en coches, retouche
  des zones de l'aperçu, mode avancé (JSON), « J'ai postulé ».

Administration et Journal LLM s'ouvrent depuis le menu de l'avatar, pour les
administrateurs. La collecte se lance au démarrage si les offres datent de plus de 6 h.

## 10. API

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/aggregate` | lance une collecte (202 + identifiant ; 409 si occupée ; 429 si trop tôt) |
| `GET` | `/aggregate/latest`, `/aggregate/{id}` | suivi d'une collecte |
| `GET` | `/offers` | **recherche** dans le catalogue (§ 6.6) |
| `POST` | `/offers` | ajout manuel d'une offre (privée) |
| `GET` | `/offers/ranked` | offres filtrées comme `/offers` et notées (dossiers de l'interface) |
| `GET` | `/offers/stack` | pile du tri rapide, triée par compatibilité |
| `PATCH` | `/offers/{id}/status`, `/favorite`, `/description` | gestes de l'utilisateur |
| `POST` | `/offers/{id}/generate/cv`, `/generate/letter` | génération |
| `GET/PUT` | `/profile`, `/profile/photo`, `/profiles` | base d'expériences et profils |
| `GET/POST` | `/interview…` | entretien guidé |
| `GET/PUT/PATCH` | `/documents…` | retouche, template, PDF, aperçus |
| `GET` | `/admin/overview`, `/admin/llm-runs` | administration |
| `GET` | `/auth/config`, `/auth/me`, `/health` | configuration publique, droits du compte, sonde |

## 11. CLI

```bash
jobtool aggregate      # collecte (toutes les sources activées)
jobtool catalogue …    # stats | doublons | dedoublonner | renormaliser
jobtool cv             # CV de bout en bout (appel LLM réel)
jobtool letter         # lettre de bout en bout
jobtool batch          # un CV + une lettre par offre .txt d'un dossier
jobtool api            # démarre l'API et sert l'interface
```

Les imports lourds sont différés : `jobtool --help` répond sans clé ni réseau.

## 12. Base de données

`users`, `offers`, `offer_occurrences`, `occurrence_urls`, `offer_aliases`, `user_offers`,
`collection_runs`, `experience_bases`, `profiles`, `profile_photos`, `documents`,
`interview_sessions`, `llm_usage`, `llm_runs`, `llm_exchanges`.

Le schéma est **défini une seule fois** (`core/db/schema.py`) : le code y construit ses
requêtes, et Alembic compare ce même `metadata` à la base réelle. Types volontairement
neutres (les horodatages sont du texte ISO-8601), sauf le JSON qui devient `jsonb` sur
Postgres.

## 13. Configuration et secrets

Tout passe par `settings.py` (pydantic-settings, lit l'environnement puis `.env`) : clés
Mistral / France Travail / Adzuna, `DATABASE_URL`, `SUPABASE_*`, chemins par défaut
(`JOBTOOL_AGG_DB`, `JOBTOOL_AGG_CONFIG`, `JOBTOOL_AGG_OUT`, `JOBTOOL_BASE`…), rétention du
catalogue et délai entre collectes. **Aucun secret en dur, aucun secret journalisé** ; les URL
de base sont affichées avec le mot de passe masqué.

## 14. Tests et vérifications

```bash
.venv/bin/pytest          # ~800 tests
.venv/bin/ruff check .    # lint
.venv/bin/ruff format .   # format
.venv/bin/mypy src        # types
```

Trois règles apprises à leurs dépens :

- **la suite n'atteint ni les vraies données ni le réseau** : une fixture de session
  neutralise `DATABASE_URL`, `SUPABASE_JWKS_URL`, la base d'expériences, **la base de
  l'agrégateur, son dossier d'export et son config**, et vide les clés d'API ;
- **un test qui remplace la couche d'écriture ne prouve rien sur ce qui est écrit** : les
  tests de collecte laissent tourner le stockage réel et ne remplacent que les connecteurs ;
- **SQLite en test, Postgres en production** : ce qui dépend du dialecte (`ON CONFLICT`,
  JSONB, types) se vérifie aussi contre un vrai Postgres jetable.

L'état de référence **n'est pas vierge** : `ruff.toml` l'emporte sur `pyproject.toml`
(longueur de ligne 88), `ruff check` remonte une quinzaine d'avertissements préexistants et
`mypy` sept erreurs. On compare à cet état ; on ne « nettoie » pas sans demande explicite.

**Binaires externes obligatoires** pour le rendu (et ses tests) : `typst`, `pdfinfo`,
`pdftotext`. Leur absence ressemble à un bug de code.

## 15. Exploitation

```bash
alembic upgrade head                          # schéma Postgres (jamais créé par le code)
python scripts/set_admin.py <e-mail>          # promouvoir un administrateur
python scripts/drop_legacy_tables.py          # nettoyer une base SQLite héritée (sauvegarde d'abord)
python scripts/build_geo_referential.py       # rafraîchir le référentiel des communes
python scripts/import_local_to_postgres.py    # reprise locale → Postgres (documents, base d'XP)
```

L'environnement de travail : outils **préfixés par `.venv/bin/`**, commandes lancées **depuis
la racine** du dépôt (base, config et `output/` y sont résolus relativement).

## 16. Limites connues

- **Recoupement faible entre ATS et jobboards** : les fusions observées sont surtout France
  Travail ↔ Adzuna. Le seuil de 0,90 se recalibre avec `jobtool catalogue doublons`.
- **Pas de séparation après coup** : corriger une règle ne défait pas une fusion déjà faite.
- **Pile du tri rapide et `/offers/ranked`** : elles chargent les offres candidates pour
  les noter en mémoire ; sur un catalogue de plusieurs milliers d'offres, un pré-filtre
  SQL deviendra nécessaire.
- **Expiration** : une offre écartée par les plafonds (`max_results`) cesse d'être revue et
  expire au bout de 30 jours, même si elle est toujours en ligne.
- **Géo hors de France** : les ATS publient à l'étranger ; seul le pays est alors normalisé.
