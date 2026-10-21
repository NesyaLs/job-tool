/* Indicateur d'enregistrement automatique, en bas de chaque section du profil :
   « Enregistrement… », « Enregistré automatiquement · Annuler », ou l'erreur du serveur
   avec « Réessayer ». Région `aria-live` : l'état est annoncé sans voler le focus. */

import { el, fill } from '../core/dom.js';
import { subscribeWhile } from '../core/store.js';
import { profileState, retrySave, undoLastSave } from './profile-data.js';

export function SaveStatus() {
  const node = el('div', { class: 'save-status', role: 'status', 'aria-live': 'polite' });

  function paint() {
    const s = profileState;
    node.dataset.state = s.save;
    if (s.save === 'pending' || s.save === 'saving') {
      fill(node, el('i', { class: 'save-dot', 'aria-hidden': 'true' }), 'Enregistrement…');
    } else if (s.save === 'error') {
      fill(node, el('i', { class: 'save-dot', 'aria-hidden': 'true' }),
        el('span', {}, `Non enregistré : ${s.saveError}`),
        el('button', { class: 'btn-link', type: 'button', onClick: retrySave }, 'Réessayer'));
    } else if (s.save === 'saved') {
      fill(node, el('i', { class: 'save-dot', 'aria-hidden': 'true' }), 'Enregistré automatiquement',
        s.undo && el('button', { class: 'btn-link', type: 'button', onClick: undoLastSave }, 'Annuler'));
    } else {
      fill(node, el('span', { class: 'field-hint' }, 'Chaque modification est enregistrée automatiquement.'));
    }
  }

  paint();
  subscribeWhile(node, 'profile-save', paint);
  subscribeWhile(node, 'profile', paint);
  return node;
}
