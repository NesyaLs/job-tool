# Template de CV « Contrasté » (`modern_cv`)

Fiche de référence du template. Toute valeur chiffrée ici a été **mesurée dans ce dépôt**,
avec les polices embarquées et la vraie boucle de remplissage ; les chiffres d'un rapport
produit ailleurs ne valent que comme point de départ.

## Identité

| | |
|---|---|
| Nom technique | `modern_cv` (référence canonique : `modern_cv?cle=valeur&…`) |
| Libellé à l'écran | **Contrasté** — badge « bêta » (`TemplateSpec.beta = True`) |
| Fichier | `src/jobtool/documents/cv/templates/cv_modern_cv.typ` |
| Profil | `profile_modern_cv` (`documents/cv/profiles.py`) |

Pourquoi « Contrasté » et non « Moderne » : « Moderne » est déjà le libellé d'un template
de **lettre**, et deux mises en page ne peuvent pas porter le même nom à l'écran. Ce qui
distingue visuellement ce template de « Classique » et « Stylé » n'est pas son filet de
section — les deux autres en ont un aussi — mais son nom en deux tons (prénom fin coloré,
nom gras noir), ses contacts à icônes et sa police sans-sérif.

## Versions et licence

| | |
|---|---|
| Package | `@preview/modern-cv:0.10.0` (épinglé dans le `.typ`) |
| Licence | MIT — Copyright (c) 2024 Paul Tsouchlos |
| Dépôt | https://github.com/ptsouchlos/modern-cv |
| Compilateur minimal déclaré | Typst 0.12.0 (mesuré ici avec **0.15.1**) |
| Dépendances Typst | `@preview/fontawesome:0.6.0`, `@preview/linguify:0.5.0` |
| Polices | Source Sans 3, Roboto, Font Awesome 7 (Free / Free Solid / Brands) |

Toutes ces polices sont **embarquées** dans `src/jobtool/core/rendering/fonts/` et imposées
à Typst par `--font-path` + `--ignore-system-fonts` (`core/rendering/typst_renderer.py`).
Sur une machine neuve, rien n'est à télécharger côté polices ; seul le **package** est
récupéré par Typst à la première compilation (réseau requis une fois, puis cache).

Risque de rupture connu : le package appelle une fonction *privée* de `linguify`
(`lflib._linguify`). Sans conséquence tant que la version reste épinglée ; à revérifier à
toute montée de version.

## Méthode de calibration

Les budgets se mesurent sur le PDF rendu, jamais par estimation :

```
.venv/bin/python scripts/calibrate_template.py modern_cv
```

Le script part de la base fictive `tests/fixtures/calibration/base.yaml` (réservoir = la
base entière, dans son ordre, sans appel au modèle) et lance la **vraie** boucle
`documents/cv/fill.py`, qui ajoute une puce, recompile, compte les pages et s'arrête au
débordement. Il balaie toutes les combinaisons d'options de **mise en page**, puis fait
varier `max_projects` pour couvrir plusieurs tailles de CV et en déduire `bullet_budget`.

À relancer après : une modification du `.typ`, une montée de version du package ou de
Typst, un changement de police, ou une modification de la base fictive.

Deux précautions de lecture :

- le nombre de puces n'est pas une mesure fine de la place restante. `fill.py` s'arrête au
  premier débordement avec une fenêtre de 3 sondes (`OVERFLOW_PROBE_WINDOW`), donc un creux
  d'une hauteur de ligne peut rester non comblé. Un écart de ±1 puce n'est pas significatif ;
- `bullet_budget` borne la sortie du **modèle** (`check_budget`), pas le remplissage.
  `fill.py` ne le lit pas : il ne connaît que `max_bullets_per_entry` et `max_projects`.

## Calibration mesurée

Mesuré le 18/09/2026, Typst 0.15.1, polices embarquées, base fictive
`tests/fixtures/calibration/base.yaml` (3 stages épinglés, 4 projets).

### Effet de chaque option de mise en page

Entrées et puces obtenues par `fill.py`, page remplie jusqu'au débordement dans tous les cas.

| accroche | savoir-être | pied de page | Entrées | Puces |
|---|---|---|---|---|
| non | non | non | 5 | 20 |
| non | oui | non | 5 | 17 |
| **oui** | **oui** | **non** *(défaut)* | **5** | **16** |
| oui | non | non | 5 | 18 |
| non | non | oui | 5 | 17 |
| non | oui | oui | 5 | 15 |
| oui | non | oui | 5 | 16 |
| oui | oui | oui | 5 | 14 |

Coût de chaque option, toutes choses égales : accroche ≈ −2 puces, savoir-être ≈ −3,
pied de page ≈ −3. Les trois sont donc bien des options de **mise en page**.

### `bullet_budget`

Mesuré en forçant `min_projects = max_projects = k` (les stages sont épinglés : sans cela
la boucle produit toujours 5 entrées). Trois exécutions ont donné le même résultat.

| Entrées affichées | Puces tenues | Ce qui borne |
|---|---|---|
| 4 | 16 | `max_bullets_per_entry` (4 × 4), pas la page |
| 5 | 16 | la page |
| 6 | 13 | la page |

→ `bullet_budget = {4: 16, 5: 16, 6: 13}`.

### Choix de `max_bullets_per_entry`

| Valeur | 4 entrées | 5 entrées *(cas nominal)* | 6 entrées |
|---|---|---|---|
| **4** *(retenu)* | 16 | **16** | 13 |
| 5 | 19 | 13 | 13 |
| 6 | 19 | 15 | 13 |

Contre-intuitif mais mesuré : **relever le plafond dégrade le cas nominal**. Avec un
plafond plus haut, le remplissage glouton empile davantage de puces sur les premières
entrées, sature la page plus tôt, et la fenêtre de sondage (`OVERFLOW_PROBE_WINDOW = 3`)
ne suffit pas à combler le creux. On garde 4, optimal là où le template sera réellement
utilisé.

### Écarts avec le rapport d'évaluation

Le rapport (bac à sable, hors projet) proposait `{4: 19, 5: 16, 6: 13}` comme point de
départ. Les clés **5 et 6 sont confirmées à l'identique**. La clé 4 passe de 19 à **16** :
le rapport mesurait la géométrie pure, sans appliquer `max_bullets_per_entry`, alors que
c'est ce plafond qui borne réellement ici. Les autres valeurs du rapport (`min_projects=2`,
`max_projects=3`, `max_bullets_per_entry=4`, `max_title_chars=80`) sont conservées.

## Options

| Option | Type | Valeurs | Défaut | Nature |
|---|---|---|---|---|
| `couleur` | choix | `bleu`, `bordeaux`, `vert`, `noir` | `bleu` | **visuelle** |
| `petites_capitales` | booléen | oui / non | oui | **visuelle** |
| `pied_de_page` | booléen | oui / non | non | **mise en page** |
| `accroche` | booléen | oui / non | oui | **mise en page** (`show_accroche`) |
| `soft_skills` | booléen | oui / non | oui | **mise en page** (`show_soft_skills`) |

- Les options **visuelles** (`affects_budget=False`) ne déplacent rien : elles ne touchent
  pas au profil, sont seulement transmises au `.typ`, et chaque combinaison a sa miniature.
- `pied_de_page` est une option de mise en page **sans champ de profil**
  (`profile_field=None`) : le package réserve alors 20 mm en bas au lieu de 10 mm. Rien
  n'est appliqué au profil — c'est `fill.py` qui mesure la page telle qu'elle sera rendue.
  C'est le premier template du projet à utiliser ce cas, jusque-là prévu mais inemployé.
- `accroche` et `soft_skills` pilotent des booléens du profil, comme sur les autres
  templates.

## Choix d'intégration

Écarts assumés par rapport à l'usage courant du package, tous documentés dans l'en-tête du
`.typ` :

- `profile-picture: none` est **obligatoire** : la valeur par défaut du package est la
  *fonction* `image`, pas `none`, et le rendu échouerait ;
- `date: ""` et pied de page désactivé : la date par défaut du package est celle du **jour**,
  en anglais — le rendu ne serait plus déterministe ;
- les liens passent par `custom` avec l'URL complète : les champs natifs `github`/`linkedin`
  attendent un *nom d'utilisateur* et préfixent eux-mêmes, tandis que `website` ne préfixe
  rien. Nos champs arrivent sans schéma (`render.py::_split_links`), on ajoute `https://` ;
- `positions` est requis par le package : les données n'ont pas de titre de poste, on passe
  une liste vide ;
- `accent-color` doit être **repassé à chaque `resume-entry`** : celui de `resume()` ne s'y
  propage pas ;
- la police est nommée explicitement (`Source Sans 3`) : la liste par défaut du package
  contient « Source Sans Pro », absente, et provoquait un avertissement à chaque compilation ;
- sections absentes du package et écrites à la main : accroche, projets, langues,
  savoir-être, centres d'intérêt.

Zones éditables `field()` : identiques à `cv_basic.typ` — l'accroche, chaque puce (par son
id de traçabilité), `hard_skills`, `soft_skills`, `interests`.

## Limites connues

- **Découpage du nom** au premier espace (le package exige `firstname`/`lastname`).
  « Marie-Hélène de La Tour-d'Auvergne » se coupe correctement, mais un double prénom sans
  trait d'union (« Jean Pierre Dupont ») donnerait « Jean » / « Pierre Dupont ».
- **Marges non paramétrables** : 15 mm à gauche et à droite, 10 mm en haut, 10 mm en bas
  (20 mm avec pied de page).
- **En-tête haut** : nom à 32 pt, adresse sur sa propre ligne, contacts sur deux lignes. Il
  consomme environ un cinquième de la page — c'est la raison de la capacité plus faible que
  « Classique ».
- Le lien `tel:` conserve les espaces du numéro. Accepté par la plupart des lecteurs, mais
  non conforme à la RFC 3966.
- **Huit miniatures** : les deux options visuelles se combinent (4 couleurs × petites
  capitales). C'est le mécanisme existant — une vignette par combinaison, pour que la carte
  de choix suive les réglages. Retirer `petites_capitales` des options ramènerait ce nombre
  à quatre, si le coût de stockage ou de régénération devenait gênant.

## Marqueur de mesure

La dernière ligne du `.typ` porte `// [mesure:fin-principale]`. Ce commentaire est **inerte**
et ne sert pas à la calibration décrite ici, qui compte des pages. Il marque l'endroit où
insérer un jalon `metadata` si l'on veut un jour mesurer la place restante en points plutôt
qu'en puces (méthode du rapport d'évaluation). Le conserver coûte une ligne et évite d'avoir
à retrouver ce point d'ancrage.
