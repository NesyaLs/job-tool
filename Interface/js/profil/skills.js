/* Profil › Compétences : des pastilles regroupées par catégorie (techniques, savoir-être).
   Entrée ajoute la saisie ; la croix d'une pastille la retire. */

import { el, icon } from '../core/dom.js';
import { state } from '../core/store.js';
import { editProfile } from './profile-data.js';
import { takeSkillPrefill } from './skill-bridge.js';

const SKILL_GROUPS = [
  { key: 'hard', label: 'Compétences techniques', placeholder: 'ex. SQL, Python, Power BI' },
  { key: 'soft', label: 'Savoir-être', placeholder: 'ex. Rigueur, Travail en équipe' },
];

/* Ajoute une compétence si elle n'y est pas déjà (sans tenir compte de la casse). */
function addSkill(key, value) {
  const name = value.trim();
  if (!name) return false;
  if (state.profile[key].some(s => s.toLowerCase() === name.toLowerCase())) return false;
  editProfile(profile => { profile[key] = [...profile[key], name]; });
  return true;
}

function Group(group, prefill = '') {
  const skills = state.profile[group.key];
  const inputId = `skill-input-${group.key}`;
  const input = el('input', {
    class: 'input chip-input', id: inputId, placeholder: group.placeholder, value: prefill,
    onKeydown: (event) => {
      if (event.key !== 'Enter' && event.key !== ',') return;
      event.preventDefault();
      const values = event.target.value.split(',');
      values.forEach(v => addSkill(group.key, v));
      requestAnimationFrame(() => document.getElementById(inputId)?.focus());
    },
  });
  return el('section', { class: 'skill-group', 'aria-labelledby': `${inputId}-title` },
    el('h2', { class: 'section-title', id: `${inputId}-title` }, group.label, el('span', { class: 'list-sub num' }, String(skills.length))),
    el('ul', { class: 'skill-chips' },
      ...skills.map((skill, index) => el('li', { class: 'chip' }, skill,
        el('button', {
          class: 'chip-remove', type: 'button', 'aria-label': `Retirer ${skill}`,
          onClick: () => editProfile(profile => { profile[group.key] = profile[group.key].filter((_, i) => i !== index); }),
        }, icon('close'))))),
    el('label', { class: 'visually-hidden', for: inputId }, `Ajouter : ${group.label.toLowerCase()}`),
    input,
    el('p', { class: 'field-hint' }, 'Entrée pour ajouter ; plusieurs à la fois en les séparant par des virgules.'));
}

export function SkillsSection() {
  // Arrivée depuis « Ajouter au profil » : la compétence est déjà saisie, Entrée l'ajoute.
  const prefill = takeSkillPrefill();
  if (prefill) requestAnimationFrame(() => document.getElementById('skill-input-hard')?.focus());
  return el('div', { class: 'profile-form' }, ...SKILL_GROUPS.map(g => Group(g, g.key === 'hard' ? prefill : '')));
}
