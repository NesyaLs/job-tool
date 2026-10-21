# documents/drafts — retouche manuelle d'un document généré

Permet de reprendre à la main le texte d'un CV/lettre déjà produit, de recompiler, et de
revérifier le format — sans rappeler le LLM.

## Ce qu'est un brouillon

```
brouillon = render_input (état généré, FIGÉ) + overrides {id de champ -> texte}
```

Le PDF n'est pas un état : c'est un produit **dérivé**, recalculé à chaque enregistrement.
Garder l'original séparé des retouches est ce qui rend « revenir au texte généré »
possible champ par champ, et ce qui rend visible quels faits ont été réécrits.

## Comment on sait où est chaque phrase dans le PDF

C'est le **compilateur** qui le dit, pas le front. Chaque zone retouchable des templates est
enveloppée dans `field(id, texte)` (voir `cv/templates/_edit.typ`), qui émet une `metadata`
de taille nulle portant l'id, le texte rendu et la boîte mesurée pendant la mise en page :

```
typst eval 'query(<jtfield>).map(it => it.value)' --in <template> --format json
```

Conséquence : aucun appariement de texte approximatif sur le PDF, aucune bibliothèque PDF
côté navigateur. Le front reçoit des coordonnées en pourcentages de page et pose des zones
cliquables sur un aperçu PNG issu de **la même compilation** que le PDF — les deux ne
peuvent pas diverger.

`field` n'altère pas la mise en page : `metadata` est de taille nulle, `layout`/`measure`
n'émettent rien. Vérifié en comparant le rendu instrumenté au rendu d'origine (texte extrait
identique, 12 pixels d'écart d'anticrénelage sur 2,17 M).

## Identifiants de champs

Pour une puce de CV, l'id d'édition **est** l'id de traçabilité (l'achievement reformulé) :
un seul système d'identifiants, celui qui existait déjà. Les autres zones portent un nom
fixe (`hard_skills`, `soft_skills`, `interests`, `accroche` ; côté lettre `objet`,
`paragraphe_vous`, `paragraphe_moi`, `paragraphe_nous`, `formule`).

## Les contrôles sont rejoués

Une retouche ne crée pas d'angle mort : `checks.py` rejoue les **mêmes** bornes dures que la
génération — une page, extractibilité ATS (cibles recalculées sur le texte édité), budget du
template, traçabilité. Le `render_input` reste réversible en `TailoredCV` (les ids y sont
conservés), donc `check_budget` et `check_traceability` s'appliquent sans dupliquer une seule
de leurs règles.

⚠ **Ce que le code ne peut pas garantir après une retouche** : l'anti-fabrication. La
traçabilité vérifie qu'une puce *pointe* vers un fait réel, pas que son texte en soit une
reformulation fidèle. Un humain qui réécrit une puce sort donc volontairement de la garantie
« chaque fait est tracé ». C'est l'intérêt même de la retouche — à condition que ça reste
visible : `edited_field_ids` remonte jusqu'à l'écran, qui signale les zones concernées.

## Où vit l'état, et pourquoi côté serveur

Le texte retouché est l'**entrée de la prochaine compilation Typst**, qui tourne sur le
serveur. Le garder dans le navigateur obligerait à le renvoyer à chaque rendu et le perdrait
au moindre rechargement d'onglet. Le navigateur ne détient donc rien d'autoritaire : il
affiche une image et poste du texte.

- brouillons (donnée)   : `data/documents/drafts.db`   — `JOBTOOL_DRAFTS_DB`
- artefacts (jetables)  : `output/documents/<doc_id>/` — `JOBTOOL_DOCUMENTS_DIR`

Chaque document compile dans **son propre** dossier (copie des `.typ` + son
`render_input.json`), ce qui rend les recompilations indépendantes — contrairement à
`render_typst`, qui écrit un `render_input.json` partagé à côté du template installé.
