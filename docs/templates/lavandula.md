# Template de CV « Lavande » (`lavandula`)

Fiche de référence du template. Toute valeur chiffrée ici a été **mesurée dans ce dépôt**,
avec les polices embarquées et la vraie boucle de remplissage.

## Identité

| | |
|---|---|
| Nom technique | `lavandula` |
| Libellé à l'écran | **Lavande** — badge « bêta » (`TemplateSpec.beta = True`) |
| Fichier | `src/jobtool/documents/cv/templates/cv_lavandula.typ` |
| Profil | `profile_lavandula` (`documents/cv/profiles.py`) |

## Versions et licence

| | |
|---|---|
| Package | `@preview/lavandula:0.1.1` (épinglé dans le `.typ`) |
| Licence | MIT — Copyright (c) 2025 Valentino Ricotta |
| Dépôt | https://github.com/face0xff/lavandula |
| Compilateur minimal déclaré | Typst 0.13.1 (mesuré ici avec 0.15.1) |
| Dépendance Typst | `@preview/fontawesome:0.6.0` (Font Awesome 7) |
| Polices | Fira Sans (tout le texte), Font Awesome 7 Free / Free Solid / Brands |

Maintenance faible : un auteur, une poignée d'étoiles, dernière version publiée en
septembre 2025. Risque de rupture : le thème repose sur `state` + `context`, sensible à une
évolution des règles de contexte de Typst. Sans conséquence tant que la version est épinglée.

## Méthode de calibration

```
.venv/bin/python scripts/calibrate_template.py lavandula
```

Voir `docs/templates/modern_cv.md` pour le détail de la méthode.

## Calibration mesurée

Mesuré le 18/09/2026, Typst 0.15.1, polices embarquées, base fictive
`tests/fixtures/calibration/base.yaml`.

### Effet des options de mise en page : aucun

| accroche | savoir-être | largeur de barre | Entrées | Puces |
|---|---|---|---|---|
| toutes les combinaisons (8) | | | 5 | 20 |

Résultat net et attendu : **aucune des trois options ne change le nombre de puces**.
L'accroche (« À propos »), le savoir-être et les compétences vivent tous dans la barre
latérale, et la largeur de barre ne déplace pas assez la colonne principale pour coûter une
puce sur cette base.

Elles restent déclarées `affects_budget=True` :

- `accroche` et `soft_skills` pilotent des champs du profil (`show_accroche`,
  `show_soft_skills`) : elles changent réellement le rendu ;
- `largeur_barre` change la géométrie des deux colonnes. La classer « visuelle » serait faux
  et la sortirait du balayage de calibration.

Conséquence assumée : une option de mise en page n'a pas de miniature, donc la carte de
choix ne montre pas la variante « barre large ».

### `bullet_budget` et plafond par entrée

| `max_bullets_per_entry` | 4 entrées | 5 entrées *(cas nominal)* | 6 entrées | 7 entrées |
|---|---|---|---|---|
| 5 | 19 | 20 | 17 | 14 |
| **6** *(retenu)* | 22 | **21** | **18** | 16 |
| 7 | 23 | 20 | 17 | 17 |

6 est le meilleur là où le template servira. 7 gagne sur les tailles extrêmes (4 et 7
entrées) mais perd le cas nominal.

→ `bullet_budget = {4: 22, 5: 21, 6: 18, 7: 16}`, `max_bullets_per_entry = 6`.

### `max_projects` : 4

Contrairement à « Cobalt » et « Metronic », **7 entrées tiennent** (3 stages épinglés +
4 projets, 16 puces), sans débordement du plancher. `max_projects` reste donc à 4.

### Écarts avec le rapport d'évaluation

| Entrées | Rapport | Mesuré ici |
|---|---|---|
| 4 | 32 | **22** |
| 5 | 28 | **21** |
| 6 | 25 | **18** |
| 7 | 22 | **16** |

Même cause que pour « Cobalt » : le rapport donnait ses chiffres de **puces courtes**
(55–65 caractères), là où la base réelle va jusqu'à 120.

## Options

| Option | Type | Valeurs | Défaut | Nature |
|---|---|---|---|---|
| `couleur` | choix | `lavande`, `sauge`, `ardoise` | `lavande` | **visuelle** |
| `position_barre` | choix | `left`, `right` | `left` | **visuelle** |
| `largeur_barre` | choix | `normale`, `large` | `normale` | **mise en page** |
| `accroche` | booléen | oui / non | oui | **mise en page** (`show_accroche`) |
| `soft_skills` | booléen | oui / non | oui | **mise en page** (`show_soft_skills`) |

**`largeur_barre = etroite` (30 %) existe dans le `.typ` mais n'est pas proposée** : il ne
reste qu'une cinquantaine de points libres dans la barre, qui déborderait avec une liste de
compétences un peu fournie.

## Choix d'intégration

- **format A4 et marges nulles imposés** par `lavandula-theme` : les retraits viennent de
  l'`inset` de la grille (17 pt horizontal, 25 pt vertical), codés en dur dans le package ;
- **`section-element`** place le titre dans une colonne `auto` et la date dans le `1fr`
  restant : sans borne, un titre long écrase la date sur trois lignes. Le template **mesure**
  le titre et le borne à la largeur de colonne moins la place d'une date. Le correctif naïf
  en pourcentage ne marche pas — dans une colonne `auto`, `100 %` ne vaut pas la largeur de
  la colonne ;
- **langues** : `skill-levels` du package exige un *pourcentage* et une *image de drapeau*
  par langue, que les données n'ont pas (le niveau est du texte libre). Remplacé par une
  liste « **langue** — niveau » ;
- **puces** : `icon-list` exige une icône par élément ; on en met une fixe (chevron) ;
- **compétences, savoir-être, intérêts** : pastilles au style exact de `skill-group`,
  recopiées sans son en-tête à icône (le libellé est déjà le titre de section) ;
- **accroche** : section « À propos » de la barre, à l'emplacement de l'exemple du package ;
- `#set text(lang: "fr")` ajouté après le thème (aucune langue n'y est fixée).

Zones éditables `field()` : identiques aux autres templates ; comme pour « Cobalt », le
texte enregistré reste `", ".join(...)` alors que le rendu affiche des pastilles.

## Limites connues

- **Puces à 8 pt et dates à 7 pt.** C'est ce qui donne la capacité, et c'est le point à
  juger à l'impression : c'est nettement plus petit que les autres templates.
- **Barre latérale à surveiller** : son contenu ne dépend pas des puces mais des listes de
  compétences, savoir-être, intérêts et langues. Une base plus fournie que la base fictive
  peut la faire déborder — ce qui créerait une deuxième page, détectée par les contrôles.
- **Six miniatures** (3 couleurs × 2 côtés de barre).
