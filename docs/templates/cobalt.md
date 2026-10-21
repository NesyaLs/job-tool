# Template de CV « Cobalt » (`cobalt`)

Fiche de référence du template. Toute valeur chiffrée ici a été **mesurée dans ce dépôt**,
avec les polices embarquées et la vraie boucle de remplissage.

## Identité

| | |
|---|---|
| Nom technique | `cobalt` |
| Libellé à l'écran | **Cobalt** — badge « bêta » (`TemplateSpec.beta = True`) |
| Fichier | `src/jobtool/documents/cv/templates/cv_cobalt.typ` |
| Profil | `profile_cobalt` (`documents/cv/profiles.py`) |

## Versions et licence

| | |
|---|---|
| Origine | modèle `@preview/cobalt-cv:0.1.0` — **recopié, pas importé** |
| Licence | MIT — Copyright (c) 2026 Vikram Saraph |
| Mention à conserver | `src/jobtool/documents/cv/templates/LICENSE-cobalt-cv` |
| Dépendance Typst restante | `@preview/fontawesome:0.6.0` (Font Awesome 7) |
| Polices | Noto Sans, Noto Serif, Libertinus Serif (binaire Typst), Font Awesome 7 |

**Pourquoi une recopie et non un import.** `cobalt-cv` n'est pas une bibliothèque : son
point d'entrée (`template/main.typ`) *est* un CV d'exemple. L'importer n'apporterait que des
fonctions liées à sa configuration figée (couleur `#002366`, polices Noto) et aucune de ses
règles `set`/`show`, qui restent locales au module. L'usage prévu par l'auteur est
`typst init` puis édition. On possède donc une soixantaine de lignes, ce qui est plutôt un
avantage : aucune montée de version ne peut casser le rendu. En contrepartie, **la mention
MIT doit rester** dans l'en-tête du `.typ` et le fichier `LICENSE-cobalt-cv` être conservé.

Note d'empaquetage : `prepare_work_dir` ne copie que les `*.typ` dans le dossier de
compilation, donc la licence n'y suit pas — sans conséquence, elle n'est pas lue au rendu.
Elle est en revanche déclarée dans `package-data` (`LICENSE-*`) pour être distribuée.

## Méthode de calibration

```
.venv/bin/python scripts/calibrate_template.py cobalt
```

Voir `docs/templates/modern_cv.md` pour le détail de la méthode et ses deux précautions de
lecture (granularité du remplissage, rôle exact de `bullet_budget`).

## Calibration mesurée

Mesuré le 18/09/2026, Typst 0.15.1, polices embarquées, base fictive
`tests/fixtures/calibration/base.yaml`.

### Effet de chaque option de mise en page

| accroche | savoir-être | barre | Entrées | Puces |
|---|---|---|---|---|
| non | non | normale | 5 | 21 |
| non | oui | normale | 5 | 21 |
| **oui** | **oui** | **normale** *(défaut)* | **5** | **20** |
| oui | non | normale | 5 | 20 |
| non | non | large | 5 | 21 |
| non | oui | large | 5 | 21 |
| oui | oui | large | 5 | 20 |
| oui | non | large | 5 | 20 |

Lecture importante :

- l'**accroche** coûte une puce : elle est en pleine largeur, entre les deux filets ;
- le **savoir-être** ne coûte **rien** sur la colonne principale : il vit dans la barre
  latérale, qui a de la marge. L'option reste déclarée `affects_budget=True` parce qu'elle
  pilote `show_soft_skills` — elle change bien le rendu, simplement pas le nombre de puces ;
- la **largeur de barre** ne change pas non plus le compte sur cette base, alors qu'elle
  rétrécit réellement la colonne principale. Elle reste classée « mise en page » : la
  classer visuelle serait faux (et la sortirait du balayage de calibration). Conséquence
  assumée du mécanisme existant : les options de mise en page n'ont pas de miniature, donc
  la carte de choix ne montre pas cette variante.

### `bullet_budget` et plafond par entrée

| `max_bullets_per_entry` | 4 entrées | 5 entrées *(cas nominal)* | 6 entrées |
|---|---|---|---|
| 5 | 19 | 20 | 17 |
| **6** *(retenu)* | **22** | **20** | **18** |
| 7 | 22 | 19 | 17 |

6 est meilleur ou égal à 5 partout ; 7 dégrade le cas nominal. Contrairement à
« Contrasté », la barre latérale libère assez de colonne principale pour qu'un plafond plus
haut serve réellement.

→ `bullet_budget = {4: 22, 5: 20, 6: 18}`, `max_bullets_per_entry = 6`.

### `max_projects` : 3, et non 4

À **7 entrées** (3 stages épinglés + 4 projets), le `fill.py` signale `floor_overflow` : le
plancher déborde déjà, à une seule puce par entrée. Le rapport d'évaluation proposait
`max_projects = 4` ; la page ne les tient pas ici. Ramené à **3**.

### Écarts avec le rapport d'évaluation

C'est le template où l'écart est le plus grand, et il est instructif :

| Entrées | Rapport | Mesuré ici |
|---|---|---|
| 4 | 34 | **22** |
| 5 | 31 | **20** |
| 6 | 28 | **18** |
| 7 | 24 | **impossible** (le plancher déborde) |

Le rapport donnait, pour `bullet_budget`, ses chiffres de **puces courtes** (55–65
caractères). La base réelle produit des puces qui vont jusqu'à `max_bullet_chars = 120`, et
une puce longue occupe deux lignes dans une colonne de ~130 mm. Recopier 34 aurait fait
produire au modèle une trentaine de puces dont la moitié aurait été jetée au remplissage.

## Options

| Option | Type | Valeurs | Défaut | Nature |
|---|---|---|---|---|
| `couleur` | choix | `cobalt`, `bordeaux`, `vert`, `gris` | `cobalt` | **visuelle** |
| `fond_barre` | choix | `gris-bleu`, `blanc` | `gris-bleu` | **visuelle** |
| `barre` | choix | `normale`, `large` | `normale` | **mise en page** |
| `accroche` | booléen | oui / non | oui | **mise en page** (`show_accroche`) |
| `soft_skills` | booléen | oui / non | oui | **mise en page** (`show_soft_skills`) |

**`barre = etroite` existe dans le `.typ` mais n'est pas proposée.** Le rapport l'a mesurée
inutilisable : la formation s'y coupe mot par mot et il ne reste qu'une trentaine de points
dans la barre. Une valeur acceptée par le template n'a pas à être offerte à l'utilisateur.

## Choix d'intégration

Écarts assumés par rapport au modèle d'origine, documentés dans l'en-tête du `.typ` :

- **contacts** : le modèle utilise une grille de 4 colonnes égales à 10 pt, où un vrai
  e-mail et une URL LinkedIn ne tiennent pas (retours à la ligne au milieu d'un élément).
  Remplacée par des boîtes insécables à 9 pt, construites depuis les données ;
- **police du corps nommée** (Libertinus Serif) : le modèle n'en fixe aucune et hérite du
  défaut de Typst, qui a déjà changé une fois (Linux Libertine → Libertinus en 0.12) ;
- **projets** : mêmes entrées que les expériences, dans la colonne principale ;
- **langues, savoir-être, centres d'intérêt** : rubriques ajoutées dans le style de la barre
  (`== libellé` puis valeurs séparées par ` | `) ;
- **formation et expériences** : les données n'ont pas de lieu séparé (il est inclus dans
  `school`/`organization`), d'où « organisation à gauche, dates à droite ».

Zones éditables `field()` : identiques aux autres templates. Le **texte enregistré** reste
`", ".join(...)` alors que le rendu affiche ` | ` — c'est le paramètre `body:` de `field()`
qui permet de mesurer la boîte du rendu stylé tout en enregistrant le texte brut, donc la
retouche manuelle fonctionne comme ailleurs.

## Limites connues

- **Nom long** : rendu en capitales espacées à 28 pt, il passe sur deux lignes au-delà
  d'environ 25 caractères.
- **Titre d'entrée long** : un intitulé de 80 caractères (`max_title_chars`) prend deux
  lignes.
- **Huit miniatures** (4 couleurs × 2 fonds de barre), comme pour « Contrasté ».
- Le fond de la barre s'arrête à la hauteur de la ligne de grille, c'est-à-dire à la plus
  haute des deux colonnes — il ne descend pas jusqu'au bas de la page.
