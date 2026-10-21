/* Dialogue modal, sur l'élément natif <dialog> : le navigateur rend le reste de la page
   inerte, garde le focus à l'intérieur et ferme sur Échap. Ce module ajoute le titre relié
   (`aria-labelledby`), le bouton de fermeture, et rend le focus à l'élément qui a ouvert
   le dialogue. */

import { el, icon, uid } from './dom.js';

/* `content` : nœud(s) du corps. `wide` : grand format (panneaux de travail).
   Renvoie { node, close }. `onClose` est appelé une fois, quelle que soit la manière de
   fermer (bouton, Échap, `close()`). */
export function openDialog({ title, content, wide = false, onClose = () => {}, className = '' }) {
  const opener = document.activeElement;
  const titleId = uid('dlg');
  const node = el('dialog', {
    class: 'dialog' + (wide ? ' dialog-wide' : '') + (className ? ' ' + className : ''),
    'aria-labelledby': titleId,
  },
    el('div', { class: 'dialog-body' },
      el('div', { class: 'dialog-head' },
        el('h2', { class: 'dialog-title', id: titleId }, title),
        el('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Fermer', onClick: () => close() },
          icon('close')),
      ),
      content,
    ),
  );
  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    if (node.open) node.close();
    node.remove();
    onClose();
    if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
  }
  node.addEventListener('close', close);
  document.body.appendChild(node);
  node.showModal();
  return { node, close };
}

/* Confirmation d'une action destructive. Résout vrai si l'utilisateur confirme. */
export function confirmAction({ title, message, confirmLabel = 'Confirmer', danger = false }) {
  return new Promise((resolve) => {
    let answer = false;
    const confirm = el('button', {
      class: 'btn ' + (danger ? 'btn-danger' : 'btn-primary'), type: 'button',
      onClick: () => { answer = true; dialog.close(); },
    }, confirmLabel);
    const dialog = openDialog({
      title,
      content: [
        el('p', {}, message),
        el('div', { class: 'dialog-actions' },
          el('button', { class: 'btn', type: 'button', onClick: () => dialog.close() }, 'Annuler'),
          confirm),
      ],
      onClose: () => resolve(answer),
    });
    confirm.focus();
  });
}
