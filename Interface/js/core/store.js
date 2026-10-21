/* L'état de l'application et ses abonnements.

   Un objet mutable partagé, et des SUJETS auxquels un composant s'abonne. Qui modifie
   l'état appelle `notify(sujet)` ; seuls les composants abonnés à ce sujet se repeignent.
   C'est ce qui évite de reconstruire l'écran entier (et d'arracher le focus d'un champ en
   cours de saisie) à chaque changement. */

export const state = {
  // Identité : compte connecté, droits, mode (mono-poste ou multi-comptes).
  me: { email: '', isAdmin: false, authRequired: false },

  // Navigation : espace ouvert et section (dossier ou rubrique du profil).
  route: { space: 'offres', section: 'nouvelles' },

  // Recherche de la barre du haut : filtre le dossier ouvert.
  query: '',

  // Dernière collecte connue (GET /aggregate/latest, puis suivi).
  collect: { status: 'idle', run: null, message: '' },

  // Compteurs du menu, par espace puis par section : { offres: { nouvelles: 12 } }.
  counts: {},

  // Espace Offres — voir js/offres/store-offres.js pour le détail.
  offres: null,

  // Espace Profil — voir js/profil/profile-data.js.
  profile: null,
  profiles: { allowed: false, active: '', list: [] },
};

const listeners = new Map();

export function subscribe(topic, fn) {
  if (!listeners.has(topic)) listeners.set(topic, new Set());
  listeners.get(topic).add(fn);
  return () => listeners.get(topic).delete(fn);
}

export function notify(topic) {
  for (const fn of listeners.get(topic) || []) fn();
}

/* Abonnement lié à la vie d'un nœud : quand le nœud quitte le document, l'abonnement se
   défait au prochain appel. Évite de garder des écrans démontés en mémoire. */
export function subscribeWhile(node, topic, fn) {
  const off = subscribe(topic, () => {
    if (!node.isConnected) { off(); return; }
    fn();
  });
  return off;
}
