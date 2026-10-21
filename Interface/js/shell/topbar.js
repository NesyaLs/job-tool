/* Barre du haut : logo, recherche, état de la collecte, avatar. */

import { el, icon, initialsOf } from '../core/dom.js';
import { navigate } from '../core/router.js';
import { notify, state, subscribe } from '../core/store.js';
import { AvatarMenu } from './avatar-menu.js';
import { collectDetail, collectLabel } from './collect.js';

const SEARCH_DELAY_MS = 250;
const SPACE_TITLES = { profil: 'Mon profil', admin: 'Administration' };

export function Topbar({ onToggleMenu }) {
  /* Recherche : filtre le dossier ouvert de l'espace Offres, le seul où elle s'affiche.
     Taper depuis Découvrir (qui ne montre qu'une carte) ramène aux Nouvelles. */
  let timer = null;
  const input = el('input', {
    class: 'search-input', type: 'search', id: 'global-search',
    placeholder: 'Rechercher un poste, une entreprise…', autocomplete: 'off',
    onInput: (event) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        state.query = event.target.value.trim();
        if (state.route.section === 'decouvrir') navigate('offres', 'nouvelles');
        notify('query');
      }, SEARCH_DELAY_MS);
    },
  });

  const status = el('div', { class: 'collect-status', 'aria-live': 'polite' },
    el('i', { class: 'collect-dot', 'aria-hidden': 'true' }),
    el('span', { class: 'collect-text' }));
  function paintStatus() {
    status.dataset.state = state.collect.status;
    status.querySelector('.collect-text').textContent = collectLabel();
    status.title = collectDetail();
  }
  paintStatus();
  subscribe('collect', paintStatus);

  const menuToggle = el('button', {
    class: 'icon-btn menu-toggle', type: 'button', 'aria-label': 'Ouvrir le menu',
    'aria-controls': 'sidebar', 'aria-expanded': 'false',
    onClick: onToggleMenu,
  }, icon('menu'));

  // Initiales : l'e-mail du compte, sinon (mono-poste) le nom saisi dans le profil.
  const avatar = el('button', { class: 'avatar', type: 'button', 'aria-label': 'Compte et réglages' });
  function paintAvatar() {
    const p = state.profile;
    const name = p ? `${p.prenom} ${p.nom}`.trim() : '';
    avatar.textContent = initialsOf(state.me.email || name);
  }
  paintAvatar();
  subscribe('profile', paintAvatar);

  /* Hors de l'espace Offres, la place de la recherche porte le nom de l'espace : le
     créneau garde sa largeur, la barre ne bouge pas au changement d'espace. */
  const title = el('p', { class: 'topbar-title' });
  function paintTitle() {
    title.textContent = SPACE_TITLES[state.route.space] || '';
  }
  paintTitle();
  subscribe('route', paintTitle);

  return el('header', { class: 'topbar' },
    menuToggle,
    el('a', { class: 'logo', href: '#/offres/nouvelles' },
      el('img', { class: 'logo-img', src: 'assets/logo/jobtool-symbole-couleur.svg',
        alt: 'Retour aux offres', width: '28', height: '28' })),
    el('div', { class: 'topbar-slot' },
      el('div', { class: 'search', role: 'search' },
        icon('search'),
        el('label', { class: 'visually-hidden', for: 'global-search' }, 'Rechercher dans les offres'),
        input),
      title),
    status,
    AvatarMenu(avatar),
  );
}
