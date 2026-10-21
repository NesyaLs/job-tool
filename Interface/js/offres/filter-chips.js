/* Pastilles de filtre en haut de Nouvelles : poste recherché, lieu, contrat, date.

   Chaque pastille ouvre un petit panneau pour la modifier ; la croix la retire. Le poste
   ne filtre pas : il sert au score (voir decisions-ui.md). Les autres filtrent, côté
   serveur. */

import { el, icon, uid } from '../core/dom.js';
import { state, subscribeWhile } from '../core/store.js';
import { describeSelection } from './geo.js';
import { LocationPicker } from './location-picker.js';
import { CONTRACT_LABELS } from './offer-format.js';
import { setFilters } from './offres-data.js';

const AGE_OPTIONS = [
  { value: null, label: 'Toutes les dates' },
  { value: 1, label: 'Moins de 24 h' },
  { value: 7, label: 'Moins de 7 jours' },
  { value: 30, label: 'Moins de 30 jours' },
];

/* Panneau flottant non modal : focus à l'intérieur à l'ouverture, Échap ou clic ailleurs
   pour fermer, focus rendu à la pastille. */
function Popover(anchorWrap, trigger, buildContent, onClose) {
  const panel = el('div', { class: 'popover', role: 'dialog', 'aria-label': trigger.textContent });
  panel.append(...[].concat(buildContent(close)));
  function close({ restoreFocus = true } = {}) {
    document.removeEventListener('pointerdown', outside, true);
    panel.remove();
    trigger.setAttribute('aria-expanded', 'false');
    onClose(restoreFocus);
  }
  function outside(event) {
    if (!anchorWrap.contains(event.target)) close({ restoreFocus: false });
  }
  panel.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.stopPropagation(); close(); }
  });
  anchorWrap.appendChild(panel);
  trigger.setAttribute('aria-expanded', 'true');
  document.addEventListener('pointerdown', outside, true);
  panel.querySelector('input, select, button')?.focus();
  return close;
}

function Chip({ key, label, value, active, buildContent, onClear, onClosed }) {
  const wrap = el('div', { class: 'chip-wrap', dataset: { key } });
  let closeOpen = null;
  const trigger = el('button', {
    class: 'chip' + (active ? '' : ' is-empty'), type: 'button',
    'aria-haspopup': 'dialog', 'aria-expanded': 'false',
    onClick: () => {
      if (closeOpen) { closeOpen(); return; }
      closeOpen = Popover(wrap, trigger, buildContent, (restoreFocus) => {
        closeOpen = null;
        onClosed(key, restoreFocus);
      });
    },
  }, active ? value : label);
  wrap.append(trigger);
  if (active) {
    wrap.append(el('button', {
      class: 'chip-remove', type: 'button', 'aria-label': `Retirer le filtre ${label.toLowerCase()} : ${value}`,
      onClick: onClear,
    }, icon('close')));
  }
  return wrap;
}

function KeywordsContent(close) {
  const id = uid('kw');
  const input = el('input', { class: 'input', id, value: state.offres.filters.keywords, placeholder: 'ex. data analyst' });
  return el('form', { class: 'popover-form', onSubmit: (event) => {
    event.preventDefault();
    setFilters({ keywords: input.value.trim() });
    close();
  } },
    el('label', { class: 'field-label', for: id }, 'Poste recherché'),
    input,
    el('p', { class: 'field-hint' }, 'Sert à classer les offres par compatibilité ; n’en cache aucune.'),
    el('div', { class: 'dialog-actions' }, el('button', { class: 'btn btn-primary btn-sm', type: 'submit' }, 'Appliquer')));
}

function ContractContent() {
  const f = state.offres.filters;
  return el('fieldset', { class: 'popover-fieldset' },
    el('legend', { class: 'field-label' }, 'Contrat'),
    ...Object.entries(CONTRACT_LABELS).map(([value, label]) => el('label', { class: 'check' },
      el('input', {
        type: 'checkbox', checked: f.contracts.includes(value),
        onChange: (event) => {
          const next = event.target.checked
            ? [...state.offres.filters.contracts, value]
            : state.offres.filters.contracts.filter(c => c !== value);
          setFilters({ contracts: next });
        },
      }), label)));
}

function AgeContent(close) {
  const name = uid('age');
  return el('fieldset', { class: 'popover-fieldset' },
    el('legend', { class: 'field-label' }, 'Date de publication'),
    ...AGE_OPTIONS.map(option => el('label', { class: 'check' },
      el('input', {
        type: 'radio', name, checked: state.offres.filters.maxAgeDays === option.value,
        onChange: () => { setFilters({ maxAgeDays: option.value }); close(); },
      }), option.label)));
}

export function FilterChips() {
  const row = el('div', { class: 'chips', role: 'group', 'aria-label': 'Filtres de la recherche' });

  /* À la fermeture d'un panneau : repeindre (le filtre a pu changer), puis rendre le focus
     à la pastille reconstruite. */
  function onClosed(key, restoreFocus) {
    paint();
    if (restoreFocus) row.querySelector(`[data-key="${key}"] .chip`)?.focus();
  }

  function paint() {
    const f = state.offres.filters;
    const age = AGE_OPTIONS.find(o => o.value === f.maxAgeDays);
    row.replaceChildren(
      Chip({
        key: 'keywords', onClosed, label: 'Poste', value: f.keywords, active: !!f.keywords,
        buildContent: KeywordsContent, onClear: () => setFilters({ keywords: '' }),
      }),
      Chip({
        key: 'location', onClosed, label: 'Lieu', value: describeSelection(f.departments), active: f.departments.length > 0,
        buildContent: () => LocationPicker({
          selected: f.departments,
          includeUnknown: f.includeUnknown,
          onChange: (patch) => setFilters(patch),
        }),
        onClear: () => setFilters({ departments: [] }),
      }),
      Chip({
        key: 'contract', onClosed, label: 'Contrat', value: f.contracts.map(c => CONTRACT_LABELS[c] || c).join(', '),
        active: f.contracts.length > 0,
        buildContent: ContractContent, onClear: () => setFilters({ contracts: [] }),
      }),
      Chip({
        key: 'age', onClosed, label: 'Date', value: age ? age.label : '', active: f.maxAgeDays != null,
        buildContent: AgeContent, onClear: () => setFilters({ maxAgeDays: null }),
      }),
    );
  }

  paint();
  subscribeWhile(row, 'filters', () => {
    // Un panneau ouvert (cases du lieu, du contrat) reste ouvert : on ne repeint pas.
    if (!row.querySelector('.popover')) paint();
  });
  return row;
}
