/* Présentation d'une offre : lieu, contrat, sources, score, lien vers l'annonce.
   Aucune règle métier : le score et ses critères viennent du serveur. */

import { el, icon } from '../core/dom.js';

export const CONTRACT_LABELS = {
  cdi: 'CDI', cdd: 'CDD', stage: 'Stage', alternance: 'Alternance',
  interim: 'Intérim', freelance: 'Freelance',
};

const SOURCE_LABELS = {
  france_travail: 'France Travail', adzuna: 'Adzuna', smartrecruiters: 'SmartRecruiters',
  workday: 'Workday', manual: 'saisie manuelle',
};

export const sourceLabel = (source) => SOURCE_LABELS[source] || source;

/* Lieu normalisé quand il est connu (« Nanterre (92) »), sinon le texte de la source. */
export function placeLabel(offer) {
  if (offer.city) return offer.department ? `${offer.city} (${offer.department})` : offer.city;
  return offer.lieu || 'Lieu non précisé';
}

export const contractLabel = (offer) => CONTRACT_LABELS[offer.contract_kind] || '';

/* Paliers de couleur du score (docs/design : 75 % et plus, 60 à 74 %, moins de 60 %). */
function scoreLevel(score) {
  if (score == null) return null;
  if (score >= 75) return 'bon';
  if (score >= 60) return 'moyen';
  return 'faible';
}

export function ScoreBadge(score, { big = false, suffix = '' } = {}) {
  if (score == null) return null;
  return el('span', {
    class: 'score num' + (big ? ' score-big' : ''),
    dataset: { level: scoreLevel(score) },
  }, `${score} %${suffix}`);
}

/* Ligne « pourquoi » : le détail par critère que le serveur a calculé, en une phrase. */
export function whyLine(offer) {
  const criteria = offer.criteres || [];
  if (criteria.some(c => c.key === 'neutral')) {
    return 'Aucun poste recherché : indiquez-en un dans les filtres pour classer les offres.';
  }
  return criteria
    .filter(c => c.detail)
    .map(c => (c.key === 'freshness' ? c.detail : `${c.label} : ${c.detail}`))
    .join(' · ');
}

/* Tous les liens de l'offre, le principal d'abord (une offre dédoublonnée en a plusieurs). */
export function offerLinks(offer) {
  const links = (offer.sources || []).filter(link => link.url);
  if (links.length) return links;
  return offer.url ? [{ source: offer.source, url: offer.url }] : [];
}

/* « Aussi publiée sur France Travail et Adzuna » (les sources autres que la principale). */
export function alsoPublishedOn(offer) {
  const others = [...new Set(offerLinks(offer).slice(1).map(link => sourceLabel(link.source)))];
  if (!others.length) return '';
  const list = others.length === 1 ? others[0] : `${others.slice(0, -1).join(', ')} et ${others[others.length - 1]}`;
  return `Aussi publiée sur ${list}`;
}

/* Lien vers une annonce externe : nouvel onglet, SANS accès à `window.opener`. Seul
   endroit qui fabrique un lien externe, pour que l'attribut de sécurité ne dépende de la
   vigilance de personne. */
export function ExternalLink(url, label, className = 'btn btn-sm') {
  return el('a', { class: className, href: url, target: '_blank', rel: 'noopener noreferrer' },
    label, icon('external'), el('span', { class: 'visually-hidden' }, ' (nouvel onglet)'));
}
