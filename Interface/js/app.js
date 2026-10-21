/* DÉMARRAGE — le seul module qui connaît tous les espaces.

   1. Lire la configuration d'authentification : elle décide de ce qu'on affiche.
   2. Sans jeton sur une instance qui en exige un : page de connexion, rien d'autre.
   3. Sinon : le cadre (barre du haut, menu, contenu), les vues de chaque espace, la
      navigation par l'URL, puis la collecte automatique si les offres sont anciennes. */

import { fetchMe, loadAuthConfig, loadStoredSession, session, setSessionExpiredHandler } from './api/client.js';
import { initAdmin } from './admin/admin-views.js';
import { mountLogin } from './auth/login.js';
import { el } from './core/dom.js';
import { startRouter } from './core/router.js';
import { state, subscribe } from './core/store.js';
import { initOffres } from './offres/offres-view.js';
import { initApplyDrawer } from './postuler/drawer.js';
import { initProfil } from './profil/profil-view.js';
import { initCollection } from './shell/collect.js';
import { Sidebar } from './shell/sidebar.js';
import { Topbar } from './shell/topbar.js';
import { showView } from './shell/views.js';

function registerViews({ firstVisit }) {
  initOffres();
  initApplyDrawer();
  initProfil({ openIfEmpty: firstVisit });
  initAdmin();
}

async function mountApp() {
  const root = document.getElementById('app');
  root.className = 'app';
  state.me.email = session.email || '';
  state.me.authRequired = !!(session.config && session.config.auth_required);
  // Droits du compte : décident des entrées Administration et Journal LLM. La protection,
  // elle, reste au serveur (403 sur /admin/*).
  try {
    state.me.isAdmin = !!(await fetchMe()).is_admin;
  } catch (err) {
    state.me.isAdmin = false;
  }

  const main = el('main', { class: 'main', id: 'main', tabindex: '-1' });
  const backdrop = el('div', { class: 'sidebar-backdrop', onClick: () => setMenuOpen(false) });
  let topbar = null;

  function setMenuOpen(open) {
    root.classList.toggle('is-menu-open', open);
    topbar.querySelector('.menu-toggle').setAttribute('aria-expanded', String(open));
  }

  // Les espaces s'inscrivent AVANT la construction du cadre : le menu affiche leurs pieds
  // (jauge du profil, bouton d'entretien) dès le premier rendu. Sans adresse demandée
  // (premier lancement), l'application choisit l'espace : Offres, ou Profil s'il est vide.
  registerViews({ firstVisit: !location.hash });
  topbar = Topbar({ onToggleMenu: () => setMenuOpen(!root.classList.contains('is-menu-open')) });
  const sidebar = Sidebar({ onNavigate: () => setMenuOpen(false) });

  root.replaceChildren(
    el('a', { class: 'skip-link', href: '#main', onClick: (event) => { event.preventDefault(); main.focus(); } },
      'Aller au contenu'),
    topbar, sidebar, backdrop, main);

  subscribe('route', () => showView(main, state.route));
  startRouter();
  initCollection();
}

async function start() {
  await loadAuthConfig();
  loadStoredSession();
  const root = document.getElementById('app');
  // Session expirée en cours d'usage : après reconnexion, on repart d'une page neuve plutôt
  // que de remonter le cadre par-dessus l'ancien (et ses abonnements).
  setSessionExpiredHandler((message) => mountLogin(root, { message, onSignedIn: () => location.reload() }));
  if (session.config.auth_required && !session.token) {
    mountLogin(root, { onSignedIn: mountApp });
    return;
  }
  mountApp();
}

start();
