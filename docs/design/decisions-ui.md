# Nouvelle interface : choix par défaut

Chaque entrée : le choix, pourquoi, l'alternative. Ces choix sont provisoires :
l'interface sera retravaillée dans Claude Design.

## Organisation

**Le nouveau front vit dans `Interface/`, comme l'ancien.**
Pourquoi : c'est le dossier que l'API sert déjà (`FRONT_DIR`) ; le déplacer toucherait le
backend sans rien apporter. Le nouveau code est rangé dans `css/`, `js/` et `fonts/`, et
l'ancien est retiré à l'étape 7.
Alternative : un dossier `web/` et un `FRONT_DIR` configurable.

**Un seul store d'état (`js/core/store.js`) avec abonnements par clé.**
Pourquoi : l'ancien front repeignait des régions nommées à la main (`repaint('offres:recherche')`).
Un store qui notifie ses abonnés garde le même principe (on ne reconstruit pas ce qui
n'a pas changé) sans registre de noms à tenir.
Alternative : des Web Components avec leur propre état.

**Navigation par l'URL (`#/offres/nouvelles`, `#/profil/experiences`).**
Pourquoi : le bouton Retour du navigateur et les liens directs marchent sans code en plus,
et l'URL est la seule source de « où suis-je ».
Alternative : l'API History avec des chemins réels (demanderait au serveur de renvoyer
`index.html` pour toute route inconnue).

**La page de connexion est livrée dès l'étape 1 (au lieu de l'étape 6).**
Pourquoi : sans elle, le mode multi-comptes ne démarre plus dès que l'ancien index est
remplacé. Elle prend déjà le style Carnet ; l'étape 6 n'a plus qu'à la relire.
Alternative : garder l'ancien écran de connexion jusqu'à l'étape 6.

**« Réglages » regroupe la collecte (maintenant, et automatique au démarrage) et l'aide des raccourcis.**
Pourquoi : aucun réglage n'existe côté serveur ; ce sont les deux seules préférences
utiles aujourd'hui, retenues dans le navigateur.
Alternative : pas d'entrée « Réglages » tant qu'il n'y a pas de préférence de compte.

**Aucun style en ligne, y compris depuis le JS : `el()` refuse l'attribut `style`.**
Pourquoi : la règle demandée, tenue par le code plutôt que par la relecture. Les seules
valeurs calculées (position d'une zone retouchable sur l'aperçu) passent par des
propriétés CSS personnalisées posées avec `style.setProperty`, lues par la feuille de
style.
Alternative : des classes générées par valeur (impossible pour des pourcentages libres).

## Espace Offres

**Nouvelles = offres au statut `new` et non favorites.**
Pourquoi : c'est la définition de la pile « Découvrir » côté serveur. Une offre mise en
favori part dans Favoris, une offre masquée dans Masquées : chaque offre est dans un seul
dossier.
Alternative : Nouvelles = toutes les offres `new`, favoris compris.

**« Non lue » est retenu dans le navigateur.**
Pourquoi : le serveur ne connaît pas l'état « ouverte » et cette passe n'ajoute pas de
migration. Ouvrir une offre la marque lue sur ce poste (`localStorage`, liste bornée).
Alternative : une colonne `opened_at` dans `user_offers` (noté dans le plan).

**Favoris = favorites au statut `new`, triées par date de publication.**
Pourquoi : « pas encore postulées » exclut `applied` ; une favorite masquée va dans
Masquées. Il n'existe pas de date d'ajout en favori.
Alternative : colonne `favorited_at` (noté dans le plan).

**Les pastilles de filtre (poste, lieu, contrat, date) ne s'affichent que dans Nouvelles.**
Pourquoi : ce sont les critères de la recherche d'offres. Filtrer ses candidatures par
contrat n'a pas d'usage évident. La recherche de la barre du haut, elle, filtre tous les
dossiers.
Alternative : les mêmes pastilles dans chaque dossier.

**Le score est calculé sur une pastille « Poste recherché » (mots-clés), en plus du lieu et du contrat.**
Pourquoi : le score du serveur (`aggregator/matching.py`) mesure l'adéquation aux
critères de recherche, surtout aux mots-clés. Sans mots-clés, toutes les offres
plafonnent vers 50 %. La maquette ne montre que lieu, contrat et date : la pastille
« Poste » a été ajoutée devant elles.
Alternative : un score fondé sur le profil (demande un chantier serveur).

**Collecte automatique au démarrage si la dernière date de plus de 6 heures.**
Pourquoi : « quelques heures ». Le serveur refuse déjà les collectes trop rapprochées
(15 min) et les collectes simultanées : le front peut demander sans risque.
Alternative : un réglage dans le menu de l'avatar.

**Une route ajoutée : `GET /offers/ranked`, pour lister chaque dossier avec un score.**
Pourquoi : `/offers` ne note pas les offres ; `/offers/stack` les note mais ses critères
lieu et contrat sont souples, il écarte les offres sous 25 % et ne sait pas filtrer par
texte. La nouvelle route reprend les filtres de `/offers` (durs) et le score de la pile,
sans seuil. Additive : aucune route existante ne change. Testée dans
`tests/test_offers_ranked.py`.
Alternative : un paramètre `order=score` sur `/offers` (touchait une route existante).

**Liste paginée par 30, avec « Afficher plus » (et préchargement en approchant du bas au clavier).**
Pourquoi : une boîte de réception se parcourt en continu ; les boutons Précédent/Suivant
de l'ancien écran coupaient la lecture.
Alternative : défilement infini automatique (moins prévisible au clavier).

**Les compteurs du menu ignorent la recherche de la barre du haut.**
Pourquoi : un compteur dit ce que contient un dossier ; la recherche ne change que ce
qu'on regarde. Le compteur de Nouvelles suit en revanche les pastilles (ce sont les
critères de la recherche d'offres).
Alternative : compteurs filtrés par la recherche, comme dans un client de messagerie.

**Une candidature ne se masque pas (bouton désactivé dans Candidatures).**
Pourquoi : côté serveur, changer le statut d'une offre postulée efface sa date de
candidature ; l'annulation ne pourrait pas la rendre.
Alternative : autoriser, avec une confirmation qui prévient de la perte de la date.

**La ligne « pourquoi » reprend le détail des critères calculé par le serveur.**
Pourquoi : c'est la seule explication disponible (« Mots-clés : 2/2 dans l'intitulé ·
Lieu : département 75 demandé · publiée il y a 3 jours »). Sans poste recherché, elle
invite à en indiquer un.
Alternative : une phrase fondée sur le profil (demande un score fondé sur le profil).

**Masquées : « masquée il y a N jours » et le rappel de la règle de purge.**
Pourquoi : une offre masquée n'a pas d'échéance propre ; elle suit la purge du catalogue
(30 jours sans parution). La date de masquage est désormais exposée (`hidden_at`, colonne
déjà en base).
Alternative : exposer `last_seen_at` et la durée de rétention pour afficher une date.

**Une offre ajoutée à la main s'affiche dans Nouvelles même si les filtres l'écartent.**
Pourquoi : son lieu est souvent du texte libre que la normalisation ne reconnaît pas ;
elle disparaîtrait sous « Toute la France » juste après sa création.
Alternative : toujours inclure les offres privées dans Nouvelles, côté serveur.

## Tiroir Postuler

**Le tiroir est un grand panneau latéral modal (`<dialog>`), sur toute la hauteur.**
Pourquoi : l'aperçu d'un CV a besoin de place ; en modal, la liste reste intacte derrière
et le focus revient au bouton Postuler à la fermeture. Rouvrir le tiroir sur la même
offre retrouve le document, l'onglet et le mode.
Alternative : un tiroir non modal qui laisse la liste manipulable.

**Le choix du template est une liste déroulante (avec miniature avant génération), la galerie vit dans Profil › Templates.**
Pourquoi : dans le tiroir, on génère pour une offre ; choisir sa mise en page préférée
se fait une fois, dans le profil. Les options du template restent réglables ici.
Alternative : les cartes à miniatures de l'ancien écran dans le tiroir.

**Changer de template d'un CV oublie l'aperçu ; pour une lettre, recompile la même lettre.**
Pourquoi : c'est le comportement du serveur (un CV par template et options ; une lettre
se recompile dans l'autre mise en page sans rappeler le modèle).
Alternative : régénérer automatiquement le CV (contraire à « jamais automatiquement »).

**Les trois coches lisent les contrôles du serveur :** « 1 page » (aucune violation de
pages), « Aucun chiffre inventé » (aucune violation de traçabilité), « Lisible par les
ATS » (aucun texte manquant à l'extraction). Les autres points (budget, mise en page) sont
listés sous « N point(s) à revoir ». En mode avancé, la traçabilité n'est pas jugée : la
coche le dit.
Alternative : une coche par famille de contrôle (cinq au lieu de trois).

**Pas de ligne « mis en avant ».**
Pourquoi : la réponse du document ne dit pas quelles expériences ont été retenues (voir
le plan, besoins côté API).
Alternative : la déduire des zones de l'aperçu (fragile).

**Dans Candidatures, le bouton Postuler devient « Documents ».**
Pourquoi : l'offre est déjà postulée ; le tiroir sert alors à revoir ou télécharger les
documents, et affiche la date de candidature à la place de « J'ai postulé ».
Alternative : masquer le bouton dans Candidatures.

## Espace Profil

**Enregistrement automatique 0,8 s après la dernière frappe ; « Annuler » en ligne, sous le formulaire, pendant 6 s.**
Pourquoi : un message flottant à chaque pause de frappe serait envahissant ; l'indicateur
« Enregistré automatiquement · Annuler » reste à sa place et est annoncé aux lecteurs
d'écran. « Annuler » renvoie le profil d'avant la dernière série de modifications.
Alternative : un message « Annuler » flottant, comme dans l'espace Offres.

**Une ligne encore vide (langue, réalisation, expérience sans intitulé) n'est pas envoyée au serveur.**
Pourquoi : elle vient d'être ajoutée et va être remplie ; le serveur la refuserait. Elle
reste à l'écran et part dès qu'elle a un contenu.
Alternative : un bouton « Valider la ligne ».

**Profil vide au premier lancement : ouverture sur Profil › Expériences, avec un encart qui propose l'entretien IA.**
Pourquoi : « propose », pas « impose » ; le panneau ne s'ouvre pas tout seul. Un profil
est vide quand il n'a ni expérience ni compétence. Une adresse demandée (#/offres/…) est
toujours respectée.
Alternative : ouvrir directement le panneau d'entretien.

**Éditeur d'expérience : une réalisation par ligne, Entrée ajoute une ligne, Retour arrière sur une ligne vide la retire.**
Les compétences liées à chaque réalisation (déjà dans le profil) restent éditables dans
un petit champ sous la ligne, pour ne rien perdre de l'ancien écran. Le type d'expérience
et le résumé sont gardés. Les dates utilisent le sélecteur de mois du navigateur, avec
une case « En cours ».
Alternative : masquer les compétences par réalisation derrière un bouton.

**Entretien IA : le récapitulatif de l'ancien écran devient une sélection dans la colonne des propositions (« Tout sélectionner », « Accepter (n) », « Refuser (n) »).**
Pourquoi : le panneau montre déjà toutes les propositions en attente à droite ; un
second dialogue par-dessus le premier alourdirait. Le lien d'un élément du profil vers le
message qui l'a fondé est conservé.
Alternative : garder le récapitulatif en dialogue.

**Le sélecteur de profil (admins) est en haut du menu de l'espace Profil, avec « Gérer les profils » (créer, renommer, supprimer) en dialogue.**
Alternative : dans le menu de l'avatar.

**La photo est lue avec le jeton (URL `blob:`), comme les aperçus de documents.**
Pourquoi : l'ancien écran posait `/profile/photo` directement dans `<img src>`, qui part
sans en-tête d'authentification ; en multi-comptes, l'aperçu échouait en 401.
Alternative : aucune, c'est une correction.

## Tri rapide, jauge, pont vers le profil

**Découvrir est une page de l'espace Offres (`#/offres/decouvrir`), sous Nouvelles.**
Elle a remplacé le dialogue « Tri rapide ». Elle reprend les critères des pastilles de
Nouvelles (mêmes filtres, retenus sur ce poste), la pile notée par le serveur (offres
compatibles à 25 % au moins), une pile de cartes à glisser (souris et tactile, un lancer
rapide suffit), ← et →, et Retour arrière (ou Z) pour annuler. Chaque geste relit les
compteurs du menu. La recherche de la barre du haut ramène à Nouvelles, puisqu'une carte
seule ne se filtre pas.
Alternative : garder le dialogue lancé depuis Nouvelles.

**Jauge « Profil complet à X % » : cinq critères de même poids.**
Identité remplie (prénom, nom, e-mail, téléphone, au prorata), au moins une expérience,
au moins une compétence, au moins une langue, part des expériences dont une réalisation
contient un chiffre. Le lien ouvre la section du critère le plus faible. C'est le calcul
par défaut demandé ; il vit dans `js/profil/completeness.js`.
Alternative : pondérer les expériences plus lourdement.

**« Ajouter au profil » repose sur un petit vocabulaire de compétences courantes.**
Pourquoi : le serveur n'extrait pas les compétences d'une offre. Le front cherche environ
70 compétences répandues (mot entier, sans casse) dans l'intitulé et la description, et
propose la première absente du profil. Les noms trop ambigus (« R », « Go ») sont exclus.
Le bouton ouvre Profil › Compétences avec la compétence déjà saisie : Entrée confirme.
Alternative : une extraction côté serveur, comparée au profil (noté dans le plan).

## Menu de l'avatar, administration, connexion

**Une route ajoutée : `GET /auth/me` (`{is_admin}`), pour n'afficher Administration et Journal LLM qu'aux admins.**
Pourquoi : le front n'avait aucun moyen de connaître les droits sans appeler une route
d'administration. La protection reste `require_admin` (403) sur les trois routes
`/admin/*`, vérifiée par des tests qui les appellent directement en non-admin. Si
`/auth/me` échoue, l'interface se comporte comme pour un non-admin.
Alternative : déduire les droits de `GET /profiles` (`allowed`), qui suit aujourd'hui la
même règle mais pour une autre raison.

**Administration et Journal LLM forment un troisième espace (`#/admin/…`), sans onglet actif dans l'interrupteur.**
Pourquoi : leur contenu ne change pas et n'appartient ni aux offres ni au profil. Le menu
de gauche y liste les deux écrans ; un non-admin qui force l'adresse voit « Accès réservé ».
Alternative : les ouvrir en dialogue par-dessus l'espace courant.

**Le menu de l'avatar : en-tête (compte ou « Poste local »), Réglages, Collecter maintenant, puis Administration et Journal LLM (admins), puis Se déconnecter (multi-comptes).**

## Logo

**Le logo de `jobtool-logo/` est utilisé tel quel, dans ses couleurs d'origine.**
Pourquoi : ses verts (#2F8F62, #1E4436) sont proches de la palette Papier vert sans y être
identiques ; `logo.md` demande de ne pas les retoucher. Seul le symbole (sans le nom
« jobtool ») est posé dans la barre du haut et sur la page de connexion, à la demande de
l'utilisateur.
Alternative : la version monochrome teintée en `--ink`, si le contraste des deux verts
gêne à côté de l'accent graphite de l'espace Profil.

## Itération 2

**La recherche du haut n'apparaît que dans l'espace Offres ; ailleurs, son créneau porte le nom de l'espace (« Mon profil », « Administration »).**
Pourquoi : la recherche ne filtre que des offres. Le créneau garde sa largeur quel que soit
l'espace, donc la barre ne « saute » pas. L'espace Administration suit la même règle que
Profil, pour ne pas garder une recherche qui ferait quitter l'écran.
Alternative : un créneau vide côté Profil, ou la recherche conservée en Administration.

**Le détail d'une offre suit une seule mesure de lecture : `--detail-measure` (660 px).**
Pourquoi : chaque bloc (actions, description, encarts, sources) prend toute la largeur du
panneau jusqu'à cette limite, qui donne environ 90 à 100 caractères par ligne de
description (mesuré dans le navigateur). Une valeur en pixels plutôt qu'en `ch` : les blocs
n'ont pas la même taille de police et doivent pourtant s'aligner au même bord.
Alternative : aucune limite (lignes de 150 caractères sur un écran de 2 560 px).

**Le logo occupe une boîte de la largeur du menu (`--sidebar-w`), qui annule le retrait de la barre.**
Pourquoi : il se centre ainsi exactement au-dessus du menu, et la recherche démarre
alignée sur le titre du dossier (même retrait que la liste). Sur écran étroit, le menu est
replié : le logo reprend sa taille naturelle.
Alternative : une barre du haut en grille qui reprend les colonnes du cadre.
