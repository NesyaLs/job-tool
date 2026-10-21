/* Sélecteur de lieu : départements groupés par région, cases à cocher. Un clic sur une
   région coche ou décoche tous ses départements. Aucun département = toute la France.

   `onChange(patch)` reçoit `{ departments }` ou `{ includeUnknown }` à chaque geste : le
   filtre s'applique aussitôt. Le panneau est construit une fois ; seules les cases sont
   mises à jour, pour que le focus clavier reste où il est. */

import { el, uid } from '../core/dom.js';
import { FR_REGIONS } from './geo.js';

export function LocationPicker({ selected, includeUnknown, onChange }) {
  const current = new Set(selected);
  const depBoxes = new Map();      // code -> case
  const regionBoxes = [];          // { box, codes }
  const summary = el('span', { class: 'field-label', 'aria-live': 'polite' });
  const reset = el('button', { class: 'btn-link', type: 'button', onClick: () => { current.clear(); commit(); } },
    'Toute la France');

  function sync() {
    summary.textContent = current.size ? `${current.size} département(s)` : 'Toute la France';
    reset.disabled = current.size === 0;
    for (const [code, box] of depBoxes) box.checked = current.has(code);
    for (const { box, codes } of regionBoxes) {
      const count = codes.filter(code => current.has(code)).length;
      box.checked = count === codes.length;
      box.indeterminate = count > 0 && count < codes.length;
    }
  }

  function commit() {
    sync();
    onChange({ departments: [...current] });
  }

  const regions = FR_REGIONS.map(region => {
    const codes = region.deps.map(([code]) => code);
    const regionBox = el('input', {
      type: 'checkbox',
      onChange: () => {
        const all = codes.every(code => current.has(code));
        codes.forEach(code => (all ? current.delete(code) : current.add(code)));
        commit();
      },
    });
    regionBoxes.push({ box: regionBox, codes });
    const nameId = uid('reg');
    return el('fieldset', { class: 'location-region', 'aria-labelledby': nameId },
      el('label', { class: 'check location-region-name', id: nameId }, regionBox, region.nom),
      el('div', { class: 'location-deps' },
        ...region.deps.map(([code, name]) => {
          const box = el('input', {
            type: 'checkbox',
            onChange: (event) => {
              if (event.target.checked) current.add(code); else current.delete(code);
              commit();
            },
          });
          depBoxes.set(code, box);
          return el('label', { class: 'check location-dep' }, box, `${code} ${name}`);
        })));
  });

  const body = el('div', { class: 'location-picker' },
    el('div', { class: 'location-head' }, summary, reset),
    el('div', { class: 'location-regions' }, ...regions),
    el('label', { class: 'check location-unknown' },
      el('input', {
        type: 'checkbox', checked: includeUnknown,
        onChange: (event) => onChange({ includeUnknown: event.target.checked }),
      }),
      'Inclure les offres sans lieu précis'));
  sync();
  return body;
}
