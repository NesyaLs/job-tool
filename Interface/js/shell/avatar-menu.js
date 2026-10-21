/* Menu de l'avatar : réglages, collecte, déconnexion (multi-comptes) et, pour les
   administrateurs seulement, Administration et Journal LLM. */

import { session, storeSession } from '../api/client.js';
import { el, icon } from '../core/dom.js';
import { navigate } from '../core/router.js';
import { state } from '../core/store.js';
import { toast } from '../core/toast.js';
import { runCollection } from './collect.js';
import { MenuButton } from './menu-button.js';
import { openSettings } from './settings.js';

function Item(label, iconName, onClick) {
  return el('button', { class: 'menu-item', type: 'button', role: 'menuitem', tabindex: '-1', onClick },
    icon(iconName), label);
}

export function AvatarMenu(button) {
  return MenuButton({
    button,
    label: 'Compte',
    buildItems: () => [
      el('div', { class: 'menu-head', role: 'presentation' },
        el('div', { class: 'menu-head-name' }, state.me.email || 'Poste local'),
        el('div', { class: 'menu-head-sub' },
          state.me.authRequired ? 'Compte connecté' : 'Mono-poste, sans connexion')),
      Item('Réglages', 'settings', () => openSettings()),
      Item('Collecter les offres maintenant', 'refresh', async () => {
        const message = await runCollection();
        toast(message || 'Collecte lancée : les offres arrivent au fil de l’eau.');
      }),
      state.me.isAdmin && el('div', { class: 'menu-sep', role: 'separator' }),
      state.me.isAdmin && Item('Administration', 'shield', () => navigate('admin', 'overview')),
      state.me.isAdmin && Item('Journal LLM', 'journal', () => navigate('admin', 'journal')),
      session.token && el('div', { class: 'menu-sep', role: 'separator' }),
      session.token && Item('Se déconnecter', 'logout', () => {
        storeSession(null, null);
        location.reload();
      }),
    ],
  });
}
