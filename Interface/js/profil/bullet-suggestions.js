/* Profil › Expériences : puces proposées par l'IA, sans conversation.

   Un clic sur « Proposer avec l'IA » envoie l'expérience telle qu'elle est à l'écran ; le
   serveur y joint le reste du profil et renvoie quelques puces, déjà vérifiées (aucun chiffre
   inventé). On coche celles qu'on garde. Une consigne facultative oriente une nouvelle
   proposition (« insiste sur le travail en équipe »).

   L'état est tenu par expérience, hors du rendu : l'éditeur se reconstruit quand on ajoute
   une puce, les propositions restantes doivent survivre. */

import { suggestBullets } from '../api/client.js';
import { el, fill, icon, uid } from '../core/dom.js';

const boxes = new Map();   // id d'expérience -> état des propositions

function stateOf(expId) {
  if (!boxes.has(expId)) {
    boxes.set(expId, { open: false, status: 'idle', bullets: [], selected: new Set(), instruction: '', error: '' });
  }
  return boxes.get(expId);
}

/* Bouton à poser à côté de « Ajouter une réalisation ». `repaint` reconstruit l'éditeur. */
export function SuggestButton(exp, repaint) {
  return el('button', {
    class: 'btn-link add-line add-line-ia', type: 'button',
    onClick: () => { const s = stateOf(exp.id); s.open = true; propose(exp, s, repaint); },
  }, icon('sparkle'), 'Proposer avec l’IA');
}

async function propose(exp, s, repaint) {
  if (!exp.titre.trim()) {
    s.status = 'error';
    s.error = 'Donnez d’abord un intitulé à l’expérience : les propositions partent de lui.';
    repaint();
    return;
  }
  s.status = 'loading';
  s.error = '';
  repaint();
  try {
    const { bullets } = await suggestBullets(exp, s.instruction);
    s.bullets = bullets;
    s.selected = new Set();
    s.status = 'ready';
  } catch (err) {
    s.status = 'error';
    s.error = err.message;
  }
  repaint();
}

/* Encart des propositions, sous la liste des puces (null s'il est fermé). `onAdd(texts)`
   ajoute les puces retenues à l'expérience. */
export function SuggestionsBox(exp, { repaint, onAdd }) {
  const s = stateOf(exp.id);
  if (!s.open) return null;
  const box = el('section', { class: 'suggest-box', 'aria-label': 'Puces proposées par l’IA', 'aria-busy': String(s.status === 'loading') });

  const instructionId = uid('sg');
  const instruction = el('input', {
    class: 'input', id: instructionId, value: s.instruction,
    placeholder: 'Ex. : insister sur le travail en équipe, mentionner Python',
    onInput: (event) => { s.instruction = event.target.value; },
    onKeydown: (event) => { if (event.key === 'Enter') { event.preventDefault(); propose(exp, s, repaint); } },
  });

  const add = () => {
    const texts = s.bullets.filter(b => s.selected.has(b));
    s.bullets = s.bullets.filter(b => !s.selected.has(b));
    s.selected = new Set();
    if (!s.bullets.length) s.open = false;
    onAdd(texts);
  };

  let body;
  if (s.status === 'loading') {
    body = el('p', { class: 'field-hint', role: 'status' }, el('span', { class: 'spinner' }), ' L’IA lit votre profil et prépare des puces…');
  } else if (s.status === 'error') {
    body = el('p', { class: 'form-error', role: 'alert' }, s.error);
  } else if (!s.bullets.length) {
    body = el('p', { class: 'field-hint' }, 'Aucune proposition retenue. Précisez une consigne et relancez.');
  } else {
    body = el('ul', { class: 'suggest-list' },
      ...s.bullets.map(text => el('li', {},
        el('label', { class: 'check suggest-item' },
          el('input', {
            type: 'checkbox', checked: s.selected.has(text),
            onChange: (event) => {
              if (event.target.checked) s.selected.add(text); else s.selected.delete(text);
              addButton.disabled = s.selected.size === 0;
              addButton.lastChild.textContent = addLabel();
            },
          }),
          el('span', {}, text)))));
  }

  const addLabel = () => s.selected.size > 1 ? `Ajouter les ${s.selected.size} puces` : 'Ajouter la puce';
  const addButton = el('button', {
    class: 'btn btn-profil btn-sm', type: 'button', disabled: s.selected.size === 0, onClick: add,
  }, icon('plus'), el('span', {}, addLabel()));

  return fill(box,
    el('div', { class: 'suggest-head' },
      el('p', { class: 'field-label' }, icon('sparkle'), 'Proposées par l’IA'),
      el('button', {
        class: 'icon-btn', type: 'button', 'aria-label': 'Fermer les propositions',
        onClick: () => { s.open = false; repaint(); },
      }, icon('close'))),
    body,
    s.status === 'ready' && s.bullets.length > 0 && el('div', { class: 'suggest-actions' }, addButton,
      el('span', { class: 'field-hint' }, 'Chaque puce reste modifiable une fois ajoutée.')),
    el('div', { class: 'field suggest-instruction' },
      el('label', { class: 'field-label', for: instructionId }, 'Orienter la proposition (facultatif)'),
      el('div', { class: 'suggest-row' }, instruction,
        el('button', {
          class: 'btn btn-sm', type: 'button', disabled: s.status === 'loading',
          onClick: () => propose(exp, s, repaint),
        }, icon('refresh'), 'Proposer d’autres puces'))));
}
