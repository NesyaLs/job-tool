/* Menu à gauche : interrupteur Offres | Profil, puis les sections de l'espace ouvert,
   chacune avec son compteur. Le bas du menu dépend de l'espace (jauge du profil côté
   Offres, bouton « Entretien IA » côté Profil) : chaque espace y inscrit son composant.

   Le menu ne connaît aucun écran : il lit `state.counts` et les pieds inscrits. */

import { el } from '../core/dom.js';
import { hrefOf } from '../core/router.js';
import { state, subscribe } from '../core/store.js';

const SECTIONS = {
  offres: [
    { id: 'nouvelles', label: 'Nouvelles' },
    { id: 'decouvrir', label: 'Découvrir' },
    { id: 'favoris', label: 'Favoris' },
    { id: 'candidatures', label: 'Candidatures' },
    { id: 'masquees', label: 'Masquées', dim: true },
  ],
  profil: [
    { id: 'identite', label: 'Identité' },
    { id: 'experiences', label: 'Expériences' },
    { id: 'competences', label: 'Compétences' },
    { id: 'langues', label: 'Langues' },
    { id: 'templates', label: 'Templates' },
  ],
  admin: [
    { id: 'overview', label: 'Administration' },
    { id: 'journal', label: 'Journal LLM' },
  ],
};

const footers = {};
const headers = {};

/* Composant affiché en bas (ou en haut) du menu pour un espace. `build()` est rappelé à
   chaque changement d'espace ; le composant s'abonne lui-même à ce qui le concerne. */
export function registerSidebarFooter(space, build) { footers[space] = build; }
export function registerSidebarHeader(space, build) { headers[space] = build; }

export function Sidebar({ onNavigate }) {
  const header = el('div', { class: 'sidebar-header' });
  const nav = el('ul', { class: 'nav-list' });
  const footer = el('div', { class: 'sidebar-footer' });
  const tabs = {
    offres: el('a', { class: 'space-tab', href: hrefOf('offres'), onClick: onNavigate }, 'Offres'),
    profil: el('a', { class: 'space-tab', href: hrefOf('profil'), onClick: onNavigate }, 'Profil'),
  };
  let paintedSpace = null;

  function paintNav() {
    const { space, section } = state.route;
    for (const key in tabs) {
      if (key === space) tabs[key].setAttribute('aria-current', 'page');
      else tabs[key].removeAttribute('aria-current');
    }
    const counts = (state.counts && state.counts[space]) || {};
    nav.replaceChildren(...SECTIONS[space].map(item => el('li', {},
      el('a', {
        class: 'nav-item' + (item.dim ? ' is-dim' : ''),
        href: hrefOf(space, item.id),
        'aria-current': item.id === section ? 'page' : null,
        onClick: onNavigate,
      },
        el('span', {}, item.label),
        counts[item.id] != null && el('span', {
          class: 'nav-count num',
          'aria-label': `${counts[item.id]} élément(s)`,
        }, String(counts[item.id]))),
    )));
    if (paintedSpace !== space) {
      paintedSpace = space;
      header.replaceChildren(...(headers[space] ? [headers[space]()] : []));
      footer.replaceChildren(...(footers[space] ? [footers[space]()] : []));
    }
  }

  paintNav();
  subscribe('route', paintNav);
  subscribe('counts', paintNav);

  return el('nav', { class: 'sidebar', id: 'sidebar', 'aria-label': 'Navigation' },
    el('div', { class: 'space-switch', role: 'group', 'aria-label': 'Espace' }, tabs.offres, tabs.profil),
    header,
    nav,
    el('div', { class: 'sidebar-grow' }),
    footer,
  );
}
