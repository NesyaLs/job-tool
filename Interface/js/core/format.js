/* Dates et nombres, pour l'affichage seulement. Aucune règle métier.

   Les dates de l'API sont en ISO-8601, parfois nulles (certaines sources ne datent pas
   leurs annonces). Une date absente ou illisible ne donne jamais « il y a NaN jours ». */

function parseDate(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/* JJ/MM/AAAA, ou « date inconnue ». */
export function dateFr(iso) {
  const date = parseDate(iso);
  if (!date) return 'date inconnue';
  return date.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

/* « 3 mars 2026 » : pour les phrases (« Postulé le … »). */
export function dateLong(iso) {
  const date = parseDate(iso);
  if (!date) return 'à une date inconnue';
  return date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
}

const MINUTE = 60;
const HOUR = 3600;
const DAY = 86400;

function secondsSince(iso) {
  const date = parseDate(iso);
  return date ? Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000)) : null;
}

/* Ancienneté courte, pour une ligne de liste : « 2 h », « hier », « 3 j », « 2 sem. ». */
export function ageShort(iso) {
  const s = secondsSince(iso);
  if (s == null) return '';
  if (s < HOUR) return `${Math.max(1, Math.floor(s / MINUTE))} min`;
  if (s < DAY) return `${Math.floor(s / HOUR)} h`;
  if (s < 2 * DAY) return 'hier';
  if (s < 14 * DAY) return `${Math.floor(s / DAY)} j`;
  if (s < 60 * DAY) return `${Math.floor(s / (7 * DAY))} sem.`;
  return `${Math.floor(s / (30 * DAY))} mois`;
}

/* Ancienneté en toutes lettres : « il y a 2 heures », « il y a 3 jours ». */
export function ageLong(iso) {
  const s = secondsSince(iso);
  if (s == null) return '';
  if (s < MINUTE) return 'à l’instant';
  const units = [
    [HOUR, MINUTE, 'minute'],
    [DAY, HOUR, 'heure'],
    [7 * DAY, DAY, 'jour'],
    [30 * DAY, 7 * DAY, 'semaine'],
    [365 * DAY, 30 * DAY, 'mois'],
    [Infinity, 365 * DAY, 'an'],
  ];
  for (const [limit, size, unit] of units) {
    if (s < limit) {
      const n = Math.floor(s / size);
      const plural = n > 1 && unit !== 'mois' ? 's' : '';
      return `il y a ${n} ${unit}${plural}`;
    }
  }
  return '';
}

/* Nombre d'heures écoulées depuis une date (null si inconnue). */
export function hoursSince(iso) {
  const s = secondsSince(iso);
  return s == null ? null : s / HOUR;
}

export function plural(n, one, many) {
  return `${n} ${n > 1 ? many : one}`;
}
