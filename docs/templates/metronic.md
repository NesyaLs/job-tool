# Template de CV « Metronic » (`metronic`)

Fiche de référence du template. Toute valeur chiffrée ici a été **mesurée dans ce dépôt**,
avec les polices embarquées et la vraie boucle de remplissage.

C'est le template le plus coûteux à maintenir des quatre, et le moins capacitaire. Deux
défauts du package ont dû être corrigés avant qu'il soit utilisable — voir « Mise en page
réécrite » ci-dessous, qui est la section la plus importante de cette fiche.

## Identité

| | |
|---|---|
| Nom technique | `metronic` |
| Libellé à l'écran | **Metronic** — badge « bêta » (`TemplateSpec.beta = True`) |
| Fichier | `src/jobtool/documents/cv/templates/cv_metronic.typ` |
| Profil | `profile_metronic` (`documents/cv/profiles.py`) |

## Versions et licence

| | |
|---|---|
| Package | `@preview/metronic:1.1.0` (épinglé dans le `.typ`) |
| Licence | MIT — Copyright (c) 2025 Patrick Rabier |
| Dépôt | https://github.com/patrixr/metronic-cv |
| Dépendance Typst | `@preview/fontawesome:0.5.0` → **Font Awesome 6** |
| Polices | Inter, Font Awesome 6 Free / Free Solid / Brands |

⚠ Ce template est le seul à utiliser **Font Awesome 6** ; les trois autres utilisent la 7.
Les deux jeux sont embarqués dans `src/jobtool/core/rendering/fonts/` (`fa6/` et `fa7/`) :
supprimer `fa6/` ferait disparaître les icônes de ce template sans aucune erreur.

## Mise en page réécrite : deux défauts corrigés

**1. Troncature silencieuse (le plus grave).** `resume-page`, le seul point d'entrée prévu
par le package, place les deux colonnes dans `grid(rows: (100%))` — une ligne de grille de
hauteur **fixe**. Un contenu trop long y est **coupé sans erreur** : le PDF reste à une
page et le texte perdu n'apparaît nulle part, pas même dans la couche d'extraction. Or la
vérification « tient sur une page » de ce projet compte les pages : **un CV amputé aurait
passé tous les contrôles**.

Le template n'utilise donc **pas** `resume-page`. Une fonction `page-layout` d'une quinzaine
de lignes la remplace : mêmes proportions (1,9 fr / 3 fr), mêmes retraits, barre dessinée en
fond de page, mais en **hauteur automatique** — un débordement crée une deuxième page,
comme pour tous les autres templates, et les contrôles le voient.

**Il ne faut en aucun cas revenir à `resume-page`.**

**2. Taille de texte native inutilisable.** `render-area` fixe le texte à 12 pt, non
paramétrable. À cette taille, 3 expériences + 3 projets avec une seule puce longue chacun
**débordent déjà**. Le template expose une option `densite` (`normale` = 12 pt,
`serree` = 10 pt) et **force `serree` par défaut**. Cette option n'est pas déclarée côté
Python : la valeur `normale` existe dans le `.typ` mais n'est jamais utilisée.

**3. Formation déplacée.** L'exemple du package met formation et compétences dans la barre
latérale. Avec toutes les sections exigées ici, la barre **déborde à elle seule**, à 12 pt
comme à 10 pt. La formation est donc rendue dans la colonne principale.

## Méthode de calibration

```
.venv/bin/python scripts/calibrate_template.py metronic
```

Voir `docs/templates/modern_cv.md` pour le détail de la méthode.

## Calibration mesurée

Mesuré le 18/09/2026, Typst 0.15.1, polices embarquées, densité serrée, base fictive
`tests/fixtures/calibration/base.yaml`.

### Effet des options de mise en page : aucun

| accroche | savoir-être | Entrées | Puces |
|---|---|---|---|
| toutes les combinaisons (4) | | 5 | 14 |

Comme pour « Lavande », l'accroche et le savoir-être sont dans la barre latérale : ils ne
retirent aucune puce à la colonne principale. Elles restent `affects_budget=True` puisqu'elles
pilotent `show_accroche` et `show_soft_skills`.

### `bullet_budget` et plafond par entrée

| `max_bullets_per_entry` | 4 entrées | 5 entrées *(cas nominal)* | 6 entrées | 7 entrées |
|---|---|---|---|---|
| 3 *(proposé par le rapport)* | 12 | 14 | 12 | 10 |
| **4** *(retenu)* | **16** | **15** | 12 | **12** |
| 5 | 17 | 14 | 13 | 13 |

4 est le meilleur au cas nominal. Le rapport proposait 3, qui est dominé partout.

→ `bullet_budget = {4: 16, 5: 15, 6: 12, 7: 12}`, `max_bullets_per_entry = 4`.

### `max_projects` : 4

7 entrées tiennent (12 puces), le plancher ne déborde pas.

### Écarts avec le rapport d'évaluation

| Entrées | Rapport | Mesuré ici |
|---|---|---|
| 4 | 28 | **16** |
| 5 | 24 | **15** |
| 6 | 21 | **12** |

Même cause que pour « Cobalt » et « Lavande » : les chiffres du rapport étaient ceux des
puces **courtes**.

## Options

| Option | Type | Valeurs | Défaut | Nature |
|---|---|---|---|---|
| `couleur` | choix | `turquoise`, `marine`, `corail` | `turquoise` | **visuelle** |
| `accroche` | booléen | oui / non | oui | **mise en page** (`show_accroche`) |
| `soft_skills` | booléen | oui / non | oui | **mise en page** (`show_soft_skills`) |

`densite` n'est **pas** exposée (voir plus haut). `photo` et `taille_photo` non plus : le
template sait afficher une photo, mais aucune donnée ne la lui fournit encore — voir la
section suivante.

## Photo de profil

Le `.typ` est le sur-ensemble décrit au §7 bis du rapport : photo ronde en tête de la barre
latérale, ajustée automatiquement à la place libre, avec un avertissement `<jtwarning>` si
le disque doit être réduit ou omis. Sans la clé `profile.photo`, le rendu est **identique
au pixel** à celui sans photo — vérifié ici en comparant le rendu à la miniature versionnée.

### Chaîne complète

| Étape | Où |
|---|---|
| Validation et normalisation, **à l'envoi** | `core/experience_base/photo.py` |
| Stockage, une ligne par compte | table `profile_photos`, `core/experience_base/photo_store.py` |
| Référence dans le profil | `Profile.photo` = **empreinte SHA-256**, jamais un chemin |
| Transmission au rendu | `render_cv(..., photo=(sha, jpeg))` → `profile.photo = "photo-<12 hex>.jpg"` |
| Mesure du remplissage | `fill_to_one_page(..., photo=...)` : la page est mesurée telle qu'elle sera rendue |

### Pourquoi valider à l'envoi et pas au rendu

Typst ne sait pas rattraper une erreur d'image, et surtout **ne détecte pas un JPEG
tronqué** : il le rend en silence, avec un aplat gris. Il intègre par ailleurs l'image
telle quelle dans le PDF. On ne stocke donc qu'une image déjà décodée et normalisée —
JPEG carré 600 px, environ 45 Kio — et le rendu n'a plus rien à vérifier.

Refusés à l'envoi, avec un message lisible : fichier vide, non reconnu, tronqué, de plus
de 10 Mio, format hors JPEG/PNG/WebP, **image animée** (elle serait figée sur sa première
vue sans explication), et « bombe de décompression » (Pillow lève alors une exception qui
n'hérite pas de `OSError` — sans une branche dédiée, elle remonterait en erreur 500).

### Pourquoi un dossier de compilation privé

Le chemin de rendu ordinaire (`render_typst`) écrit **dans le dossier des templates
installé**, partagé par tout le process. Y déposer l'image d'un compte les ferait
s'accumuler et casserait les rendus concurrents. Dès qu'une photo accompagne la
compilation, `render_cv` passe donc par `render_typst_isolated`, qui travaille dans un
dossier privé.

### Pourquoi une table plutôt que des fichiers

L'état d'un compte vit déjà entièrement dans la base, et `DATABASE_URL` reste le seul
interrupteur du mode. Des fichiers locaux au serveur seraient invisibles depuis une autre
instance en multi-comptes, et survivraient à la suppression du compte.

### Options

`photo` (booléen, défaut oui) et `taille_photo` (`petite` 80 pt / `moyenne` 100 pt /
`grande` 120 pt), toutes deux **visuelles** : mesuré, la photo ne retire aucune puce à la
colonne principale, et le template réduit ou omet le disque si la barre manque de place.
`photo` est lue par `render.py`, pas par le `.typ` : elle décide si l'image est transmise,
ce qui permet de masquer sa photo pour une candidature sans la supprimer.

### Envoi depuis l'interface

L'écran **Profil** porte un aperçu rond, un bouton « Ajouter / Remplacer » et un bouton
« Supprimer ». Trois routes :

| Route | Rôle |
|---|---|
| `PUT /profile/photo` | multipart ; 413 si > 10 Mio, 422 si l'image est inutilisable |
| `GET /profile/photo` | sert le JPEG (`Cache-Control: no-store`) ; 404 sans photo |
| `DELETE /profile/photo` | idempotent |

L'image ne transite **jamais** par le profil : `GET /profile` n'annonce que sa présence
(`a_photo`), et l'aperçu va la chercher à sa propre URL, avec un paramètre de version pour
que le navigateur ne serve pas l'ancienne après un remplacement.

`ProfileService` pilote les deux écritures — l'image dans `profile_photos` et son empreinte
dans `Profile.photo` — parce qu'elles doivent rester cohérentes : le rendu ne retient la
photo que si les deux concordent (`PhotoStore.load_if`).

### Ce qui reste à faire

La suppression de la photo **avec le compte** n'est pas câblée : la clé étrangère rattache
la ligne à `users`, mais aucun code ne supprime un compte aujourd'hui.

## Limites connues

- **Contraste faible** : blanc sur turquoise `#61B7AE` donne un rapport d'environ 2,3:1,
  sous le seuil d'accessibilité. La variante « marine » est nettement plus lisible.
- **Le moins capacitaire des quatre** : 15 puces au cas nominal, contre 20–21 pour
  « Cobalt » et « Lavande ».
- Les titres du package prennent la taille par défaut de Typst : ses règles `show` sont
  écrites au niveau du module et ne s'exportent pas à l'import. Un changement de ces
  défauts dans une future version de Typst changerait le rendu.
- Le package n'émet **aucun lien** (`contact` affiche du texte brut) : les liens sont
  construits par le template.
- **Trois miniatures** (une par couleur).
