/* Bouton à menu déroulant, accessible au clavier (motif « menu button » de l'ARIA APG) :
   Entrée / Espace / ↓ ouvrent, ↑ ↓ parcourent, Échap ferme et rend le focus au bouton,
   un clic ailleurs ferme. */

import { el, uid } from '../core/dom.js';

/* `button` : le déclencheur (déjà construit). `buildItems()` : nœuds du menu, relus à
   chaque ouverture (les droits ou l'état ont pu changer). Chaque élément actionnable
   porte `role="menuitem"`. */
export function MenuButton({ button, buildItems, label, align = 'right' }) {
  const menuId = uid('menu');
  const menu = el('div', { class: `menu menu-${align}`, role: 'menu', id: menuId, 'aria-label': label, hidden: true });
  button.setAttribute('aria-haspopup', 'menu');
  button.setAttribute('aria-expanded', 'false');
  button.setAttribute('aria-controls', menuId);

  const items = () => [...menu.querySelectorAll('[role="menuitem"]')];

  function open(focusLast = false) {
    menu.replaceChildren(...buildItems().filter(Boolean));
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    const list = items();
    (focusLast ? list[list.length - 1] : list[0])?.focus();
    document.addEventListener('pointerdown', onOutside, true);
  }

  function close({ restoreFocus = true } = {}) {
    if (menu.hidden) return;
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', onOutside, true);
    if (restoreFocus) button.focus();
  }

  function onOutside(event) {
    if (!wrap.contains(event.target)) close({ restoreFocus: false });
  }

  button.addEventListener('click', () => (menu.hidden ? open() : close()));
  button.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); open(); }
    if (event.key === 'ArrowUp') { event.preventDefault(); open(true); }
  });

  menu.addEventListener('keydown', (event) => {
    const list = items();
    const index = list.indexOf(document.activeElement);
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    else if (event.key === 'ArrowDown') { event.preventDefault(); list[(index + 1) % list.length].focus(); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); list[(index - 1 + list.length) % list.length].focus(); }
    else if (event.key === 'Home') { event.preventDefault(); list[0].focus(); }
    else if (event.key === 'End') { event.preventDefault(); list[list.length - 1].focus(); }
    else if (event.key === 'Tab') close({ restoreFocus: false });
  });

  // Choisir un élément ferme le menu (l'action elle-même est portée par l'élément).
  menu.addEventListener('click', (event) => {
    if (event.target.closest('[role="menuitem"]')) close({ restoreFocus: false });
  });

  const wrap = el('div', { class: 'menu-wrap' }, button, menu);
  return wrap;
}
