/* Profil › Langues : une ligne par langue, avec son niveau. Le serveur les transporte en
   « Langue — Niveau » : le front sépare et recompose sur ce séparateur. */

import { el, icon } from '../core/dom.js';
import { state } from '../core/store.js';
import { editProfile, joinLanguage, splitLanguage } from './profile-data.js';

const LEVELS = ['Langue maternelle', 'Courant', 'Professionnel', 'Intermédiaire', 'Notions',
  'C2', 'C1', 'B2', 'B1', 'A2', 'A1'];

export function LanguagesSection() {
  const rows = state.profile.langues.map(splitLanguage);

  const update = (index, patch) => editProfile(profile => {
    profile.langues[index] = joinLanguage({ ...splitLanguage(profile.langues[index]), ...patch });
  }, { repaint: false });

  const levelList = el('datalist', { id: 'language-levels' }, ...LEVELS.map(level => el('option', { value: level })));

  return el('div', { class: 'profile-form' },
    levelList,
    rows.length === 0 && el('p', { class: 'field-hint' }, 'Aucune langue pour l’instant.'),
    el('ul', { class: 'lang-list' },
      ...rows.map((row, index) => el('li', { class: 'lang-row' },
        el('input', {
          class: 'input', value: row.name, 'aria-label': `Langue ${index + 1}`, placeholder: 'Langue',
          onInput: (event) => update(index, { name: event.target.value }),
        }),
        el('input', {
          class: 'input', value: row.level, list: 'language-levels', 'aria-label': `Niveau en langue ${index + 1}`,
          placeholder: 'Niveau (ex. C1, Courant)',
          onInput: (event) => update(index, { level: event.target.value }),
        }),
        el('button', {
          class: 'icon-btn', type: 'button', 'aria-label': `Retirer ${row.name || `la langue ${index + 1}`}`,
          onClick: () => editProfile(profile => { profile.langues = profile.langues.filter((_, i) => i !== index); }),
        }, icon('close'))))),
    el('button', {
      class: 'btn-link add-line', type: 'button',
      onClick: () => {
        editProfile(profile => { profile.langues = [...profile.langues, '']; });
        requestAnimationFrame(() => [...document.querySelectorAll('.lang-row input')].at(-2)?.focus());
      },
    }, icon('plus'), 'Ajouter une langue'));
}
