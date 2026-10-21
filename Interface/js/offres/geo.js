/* =================================================================
   Référentiel géographique — régions et départements français
   =================================================================
   EMBARQUÉ, et non chargé depuis une API : l'interface doit pouvoir s'ouvrir sans réseau
   autre que sa propre API, et cette liste ne change qu'à la faveur d'une réforme
   territoriale. Une dépendance externe pour 101 lignes stables serait un point de panne
   gratuit.

   Les codes sont ceux qu'attend France Travail dans son paramètre `departement` : deux
   chiffres en métropole (« 2A »/« 2B » pour la Corse), trois outre-mer. Plusieurs codes se
   transmettent séparés par des virgules — c'est ce qui permet la sélection multiple.
   Aucun code = recherche NATIONALE (voir SearchCriteria.resolve_geo côté serveur). */

export const FR_REGIONS = [
  { nom: 'Île-de-France', deps: [
    ['75', 'Paris'], ['77', 'Seine-et-Marne'], ['78', 'Yvelines'], ['91', 'Essonne'],
    ['92', 'Hauts-de-Seine'], ['93', 'Seine-Saint-Denis'], ['94', 'Val-de-Marne'],
    ['95', 'Val-d’Oise'],
  ]},
  { nom: 'Auvergne-Rhône-Alpes', deps: [
    ['01', 'Ain'], ['03', 'Allier'], ['07', 'Ardèche'], ['15', 'Cantal'], ['26', 'Drôme'],
    ['38', 'Isère'], ['42', 'Loire'], ['43', 'Haute-Loire'], ['63', 'Puy-de-Dôme'],
    ['69', 'Rhône'], ['73', 'Savoie'], ['74', 'Haute-Savoie'],
  ]},
  { nom: 'Bourgogne-Franche-Comté', deps: [
    ['21', 'Côte-d’Or'], ['25', 'Doubs'], ['39', 'Jura'], ['58', 'Nièvre'],
    ['70', 'Haute-Saône'], ['71', 'Saône-et-Loire'], ['89', 'Yonne'],
    ['90', 'Territoire de Belfort'],
  ]},
  { nom: 'Bretagne', deps: [
    ['22', 'Côtes-d’Armor'], ['29', 'Finistère'], ['35', 'Ille-et-Vilaine'], ['56', 'Morbihan'],
  ]},
  { nom: 'Centre-Val de Loire', deps: [
    ['18', 'Cher'], ['28', 'Eure-et-Loir'], ['36', 'Indre'], ['37', 'Indre-et-Loire'],
    ['41', 'Loir-et-Cher'], ['45', 'Loiret'],
  ]},
  { nom: 'Corse', deps: [['2A', 'Corse-du-Sud'], ['2B', 'Haute-Corse']] },
  { nom: 'Grand Est', deps: [
    ['08', 'Ardennes'], ['10', 'Aube'], ['51', 'Marne'], ['52', 'Haute-Marne'],
    ['54', 'Meurthe-et-Moselle'], ['55', 'Meuse'], ['57', 'Moselle'], ['67', 'Bas-Rhin'],
    ['68', 'Haut-Rhin'], ['88', 'Vosges'],
  ]},
  { nom: 'Hauts-de-France', deps: [
    ['02', 'Aisne'], ['59', 'Nord'], ['60', 'Oise'], ['62', 'Pas-de-Calais'], ['80', 'Somme'],
  ]},
  { nom: 'Normandie', deps: [
    ['14', 'Calvados'], ['27', 'Eure'], ['50', 'Manche'], ['61', 'Orne'],
    ['76', 'Seine-Maritime'],
  ]},
  { nom: 'Nouvelle-Aquitaine', deps: [
    ['16', 'Charente'], ['17', 'Charente-Maritime'], ['19', 'Corrèze'], ['23', 'Creuse'],
    ['24', 'Dordogne'], ['33', 'Gironde'], ['40', 'Landes'], ['47', 'Lot-et-Garonne'],
    ['64', 'Pyrénées-Atlantiques'], ['79', 'Deux-Sèvres'], ['86', 'Vienne'],
    ['87', 'Haute-Vienne'],
  ]},
  { nom: 'Occitanie', deps: [
    ['09', 'Ariège'], ['11', 'Aude'], ['12', 'Aveyron'], ['30', 'Gard'],
    ['31', 'Haute-Garonne'], ['32', 'Gers'], ['34', 'Hérault'], ['46', 'Lot'],
    ['48', 'Lozère'], ['65', 'Hautes-Pyrénées'], ['66', 'Pyrénées-Orientales'],
    ['81', 'Tarn'], ['82', 'Tarn-et-Garonne'],
  ]},
  { nom: 'Pays de la Loire', deps: [
    ['44', 'Loire-Atlantique'], ['49', 'Maine-et-Loire'], ['53', 'Mayenne'],
    ['72', 'Sarthe'], ['85', 'Vendée'],
  ]},
  { nom: 'Provence-Alpes-Côte d’Azur', deps: [
    ['04', 'Alpes-de-Haute-Provence'], ['05', 'Hautes-Alpes'], ['06', 'Alpes-Maritimes'],
    ['13', 'Bouches-du-Rhône'], ['83', 'Var'], ['84', 'Vaucluse'],
  ]},
  { nom: 'Outre-mer', deps: [
    ['971', 'Guadeloupe'], ['972', 'Martinique'], ['973', 'Guyane'],
    ['974', 'La Réunion'], ['976', 'Mayotte'],
  ]},
];

/* Index code -> nom, pour afficher « 75 · Paris » sans reparcourir les régions. */
const FR_DEP_NAMES = {};
FR_REGIONS.forEach(r => r.deps.forEach(([code, nom]) => { FR_DEP_NAMES[code] = nom; }));

/* Libellé lisible d'une sélection. Vide = France entière — c'est le défaut, et il est dit
   explicitement plutôt que laissé deviner par une absence. */
export function describeSelection(codes) {
  if (!codes || codes.length === 0) return 'Toute la France';
  if (codes.length === 1) return `${codes[0]} · ${FR_DEP_NAMES[codes[0]] || ''}`.trim();
  // Une région entièrement cochée s'annonce par son nom, pas par ses douze départements.
  const set = new Set(codes);
  const pleines = FR_REGIONS.filter(r => r.deps.every(([c]) => set.has(c)));
  const couverts = new Set(pleines.flatMap(r => r.deps.map(([c]) => c)));
  const restants = codes.filter(c => !couverts.has(c));
  const morceaux = pleines.map(r => r.nom).concat(restants);
  if (morceaux.length <= 2) return morceaux.join(', ');
  return `${morceaux.slice(0, 2).join(', ')} +${morceaux.length - 2}`;
}
