/* Messages éphémères, avec une action facultative (« Annuler »).

   Le conteneur est une région `role="status"` : les lecteurs d'écran annoncent chaque
   message sans déplacer le focus. */

import { el } from './dom.js';

const UNDO_DELAY_MS = 6000;
const DEFAULT_DELAY_MS = 4000;

let region = null;

function container() {
  if (!region || !region.isConnected) {
    region = el('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' });
    document.body.appendChild(region);
  }
  return region;
}

/* `action` : { label, run } — le bouton ferme le message puis lance `run`. */
export function toast(message, { kind = 'info', action = null, duration } = {}) {
  const delay = duration || (action ? UNDO_DELAY_MS : DEFAULT_DELAY_MS);
  let timer = null;
  const close = () => { clearTimeout(timer); node.remove(); };
  const node = el('div', { class: 'toast', dataset: { kind } },
    el('span', { class: 'toast-text' }, message),
    action && el('button', {
      class: 'toast-action', type: 'button',
      onClick: () => { close(); action.run(); },
    }, action.label),
  );
  container().appendChild(node);
  timer = setTimeout(close, delay);
  return close;
}

export const toastError = (message) => toast(message, { kind: 'error', duration: 6000 });

/* Message « fait, avec Annuler » : le geste le plus courant de l'interface. */
export function toastUndo(message, undo) {
  return toast(message, { action: { label: 'Annuler', run: undo } });
}
