/* Navigation par l'URL (#/espace/section). Le bouton Retour du navigateur et les liens
   directs marchent donc sans effort, et une seule source dit où l'on est : l'URL.

   Ce module ne connaît AUCUN écran : il tient la liste des sections valides par espace,
   met à jour `state.route` et notifie le sujet 'route'. */

import { notify, state } from './store.js';

const SPACES = {
  offres: ['nouvelles', 'decouvrir', 'favoris', 'candidatures', 'masquees'],
  profil: ['identite', 'experiences', 'competences', 'langues', 'templates'],
  admin: ['overview', 'journal'],
};

const DEFAULT_ROUTE = { space: 'offres', section: 'nouvelles' };

function parse(hash) {
  const [space, section] = hash.replace(/^#\/?/, '').split('/');
  if (!SPACES[space]) return null;
  return { space, section: SPACES[space].includes(section) ? section : SPACES[space][0] };
}

export function hrefOf(space, section) {
  return `#/${space}/${section || SPACES[space][0]}`;
}

export function navigate(space, section) {
  const target = hrefOf(space, section);
  if (location.hash === target) return;
  location.hash = target;
}

function apply() {
  const route = parse(location.hash);
  if (!route) {
    // URL inconnue : on la remplace (sans entrée d'historique en plus).
    history.replaceState(null, '', hrefOf(state.route.space, state.route.section));
    notify('route');
    return;
  }
  state.route = route;
  document.getElementById('app').dataset.space = route.space;
  notify('route');
}

/* Démarre l'écoute. `initial` sert quand l'URL ne dit rien (premier lancement). */
export function startRouter(initial = DEFAULT_ROUTE) {
  window.addEventListener('hashchange', apply);
  if (!parse(location.hash)) history.replaceState(null, '', hrefOf(initial.space, initial.section));
  apply();
}
