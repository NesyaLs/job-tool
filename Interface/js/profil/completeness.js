/* Jauge « Profil complet à X % », en bas du menu de l'espace Offres.

   Calcul (voir decisions-ui.md) : cinq critères de même poids —
     identité remplie (prénom, nom, e-mail, téléphone),
     au moins une expérience,
     au moins une compétence,
     au moins une langue,
     part des expériences dont une réalisation contient un chiffre.
   Le lien ouvre la section la moins remplie. */

import { el, fill } from '../core/dom.js';
import { hrefOf } from '../core/router.js';
import { state, subscribeWhile } from '../core/store.js';
import { hasNumber } from './profile-data.js';

export function completeness(profile) {
  const identity = ['prenom', 'nom', 'email', 'telephone'].filter(k => profile[k].trim()).length / 4;
  const withNumber = profile.experiences.length
    ? profile.experiences.filter(hasNumber).length / profile.experiences.length
    : 0;
  const parts = [
    { section: 'identite', value: identity },
    { section: 'experiences', value: profile.experiences.length ? 1 : 0 },
    { section: 'competences', value: profile.hard.length + profile.soft.length ? 1 : 0 },
    { section: 'langues', value: profile.langues.length ? 1 : 0 },
    { section: 'experiences', value: withNumber },
  ];
  const percent = Math.round(100 * parts.reduce((sum, p) => sum + p.value, 0) / parts.length);
  const weakest = parts.reduce((low, p) => (p.value < low.value ? p : low), parts[0]);
  return { percent, weakest: weakest.value < 1 ? weakest.section : null };
}

export function ProfileGauge() {
  const wrap = el('div', { class: 'gauge' });
  function paint() {
    if (!state.profile) { fill(wrap); return; }
    const { percent, weakest } = completeness(state.profile);
    fill(wrap,
      el('p', { class: 'gauge-label', id: 'gauge-label' }, `Profil complet à ${percent} %`),
      el('meter', { class: 'gauge-meter', min: '0', max: '100', value: String(percent), 'aria-labelledby': 'gauge-label' }),
      weakest
        ? el('a', { class: 'gauge-link', href: hrefOf('profil', weakest) }, 'Compléter mon profil')
        : el('span', { class: 'gauge-link' }, 'Profil complet'));
  }
  paint();
  subscribeWhile(wrap, 'profile', paint);
  subscribeWhile(wrap, 'profile-meta', paint);
  return wrap;
}
