/* ESPACE OFFRES — état, chargements et compteurs. Aucun rendu ici.

   Quatre dossiers, chacun défini par l'état du compte côté serveur :
     Nouvelles     statut `new`, non favorites     tri par score
     Favoris       statut `new`, favorites         tri par publication
     Candidatures  statut `applied`                tri par date de candidature
     Masquées      statut `seen`                   tri par publication
   Une offre est donc dans UN seul dossier, et un geste (favori, masquer, postuler) la
   déplace de l'un à l'autre.

   Tout filtrage et tout tri se font par le serveur (GET /offers/ranked) : un filtre
   appliqué ici ne verrait que la page chargée. */

import { listOffers, rankOffers } from '../api/client.js';
import { readPref, writePref } from '../core/storage.js';
import { notify, state } from '../core/store.js';

const PAGE_SIZE = 30;

export const FOLDERS = {
  nouvelles: {
    label: 'Nouvelles', params: { status: 'new', favorite: false }, order: 'score',
    filtered: true,
    empty: 'Aucune nouvelle offre pour ces critères. Élargissez la recherche, ou attendez la prochaine collecte.',
    emptyCatalogue: 'Le catalogue est vide. Lancez une collecte depuis le menu de votre avatar.',
  },
  favoris: {
    label: 'Favoris', params: { status: 'new', favorite: true }, order: 'posted_date',
    empty: 'Aucun favori. Gardez une offre avec « Favori » (touche F) pour la retrouver ici.',
  },
  candidatures: {
    label: 'Candidatures', params: { status: 'applied' }, order: 'applied_at',
    empty: 'Aucune candidature. « J’ai postulé », dans le tiroir Postuler, range l’offre ici avec la date.',
  },
  masquees: {
    label: 'Masquées', params: { status: 'seen' }, order: 'posted_date',
    empty: 'Aucune offre masquée. Celles que vous écartez (touche H) arrivent ici, et se remettent d’un clic.',
  },
};

/* Critères de la recherche d'offres (pastilles de Nouvelles), retenus sur ce poste.
   `keywords` ne filtre pas : il sert au score. Les autres filtrent. */
const DEFAULT_FILTERS = { keywords: '', departments: [], contracts: [], maxAgeDays: null, includeUnknown: false };

function loadFilters() {
  const saved = readPref('offerFilters', {});
  const valid = { ...DEFAULT_FILTERS };
  if (typeof saved.keywords === 'string') valid.keywords = saved.keywords;
  if (Array.isArray(saved.departments)) valid.departments = saved.departments.filter(d => typeof d === 'string');
  if (Array.isArray(saved.contracts)) valid.contracts = saved.contracts.filter(c => typeof c === 'string');
  if (Number.isInteger(saved.maxAgeDays)) valid.maxAgeDays = saved.maxAgeDays;
  valid.includeUnknown = saved.includeUnknown === true;
  return valid;
}

state.offres = {
  folder: 'nouvelles',
  items: [],
  total: 0,
  status: 'idle',          // idle | loading | ready | error
  error: '',
  loadingMore: false,
  selectedId: null,
  filters: loadFilters(),
};

export function setFilters(patch) {
  Object.assign(state.offres.filters, patch);
  writePref('offerFilters', state.offres.filters);
  notify('filters');
  reloadFolder();
  refreshCounts();
}

/* Paramètres de filtre d'un dossier (sans pagination ni tri). */
function folderParams(folderId, { withQuery = true } = {}) {
  const folder = FOLDERS[folderId];
  const f = state.offres.filters;
  const params = { ...folder.params };
  if (withQuery && state.query) params.q = state.query;
  if (folder.filtered) {
    if (f.departments.length) params.departments = f.departments.join(',');
    else params.country = 'FR';   // « Toute la France » : les ATS publient aussi à l'étranger
    if (f.includeUnknown) params.include_unknown_location = true;
    if (f.contracts.length) params.contract = f.contracts;
    if (f.maxAgeDays != null) params.max_age_days = f.maxAgeDays;
  }
  return params;
}

/* Le score se calcule sur les mêmes critères dans tous les dossiers. */
function scoreParams() {
  const f = state.offres.filters;
  return {
    keywords: f.keywords,
    departments: f.departments.join(',') || null,
    contract: f.contracts,
  };
}

/* Numéro du dernier chargement : une réponse arrivée après un changement de dossier ou de
   filtre est ignorée, au lieu d'écraser la liste attendue. */
let loadToken = 0;

export async function reloadFolder() {
  const o = state.offres;
  const token = ++loadToken;
  o.status = 'loading';
  o.error = '';
  notify('offres');
  try {
    const page = await rankOffers({
      ...scoreParams(), ...folderParams(o.folder),
      order: FOLDERS[o.folder].order, limit: PAGE_SIZE, offset: 0,
    });
    if (token !== loadToken) return;
    o.items = page.items;
    o.total = page.total;
    o.status = 'ready';
    if (!o.items.some(item => item.id === o.selectedId)) o.selectedId = null;
    consumeReveal();
  } catch (err) {
    if (token !== loadToken) return;
    o.items = [];
    o.total = 0;
    o.status = 'error';
    o.error = err.message;
  }
  notify('offres');
  notify('selection');
}

export async function loadMore() {
  const o = state.offres;
  if (o.loadingMore || o.items.length >= o.total) return;
  const token = loadToken;
  o.loadingMore = true;
  notify('offres');
  try {
    const page = await rankOffers({
      ...scoreParams(), ...folderParams(o.folder),
      order: FOLDERS[o.folder].order, limit: PAGE_SIZE, offset: o.items.length,
    });
    if (token !== loadToken) return;
    const known = new Set(o.items.map(item => item.id));
    o.items = [...o.items, ...page.items.filter(item => !known.has(item.id))];
    o.total = page.total;
  } catch (err) {
    if (token === loadToken) o.error = err.message;
  } finally {
    if (token === loadToken) o.loadingMore = false;
    notify('offres');
  }
}

export function openFolder(folderId) {
  const o = state.offres;
  if (o.folder === folderId && o.status !== 'idle') return;
  o.folder = folderId;
  o.selectedId = null;
  o.items = [];
  reloadFolder();
}

/* Compteurs du menu : une requête légère par dossier (`limit=1`, le total est dans
   l'en-tête). La recherche de la barre du haut n'y entre pas : un compteur dit ce que
   contient le dossier, pas ce que montre la recherche en cours. */
let countsToken = 0;

export async function refreshCounts() {
  const token = ++countsToken;
  const entries = await Promise.all(Object.keys(FOLDERS).map(async (id) => {
    try {
      const page = await listOffers({ ...folderParams(id, { withQuery: false }), limit: 1 });
      return [id, page.total];
    } catch (err) {
      return [id, null];
    }
  }));
  if (token !== countsToken) return;   // une demande plus récente est partie entre-temps
  state.counts = { ...state.counts, offres: Object.fromEntries(entries) };
  notify('counts');
}

/* ---------- Offres lues (retenues sur ce poste : le serveur ne connaît pas cet état) ---------- */

const OPENED_MAX = 3000;
let opened = new Set(readPref('openedOffers', []));

export const isUnread = (offer) => !opened.has(offer.id);

function markOpened(offer) {
  if (opened.has(offer.id)) return;
  opened.add(offer.id);
  if (opened.size > OPENED_MAX) opened = new Set([...opened].slice(-OPENED_MAX));
  writePref('openedOffers', [...opened]);
}

/* ---------- Sélection ---------- */

export function selectedOffer() {
  const o = state.offres;
  return o.items.find(item => item.id === o.selectedId) || null;
}

export function select(id) {
  const o = state.offres;
  o.selectedId = id;
  const offer = selectedOffer();
  if (offer) markOpened(offer);
  notify('selection');
}

/* Sélection relative (J / K). Renvoie l'offre sélectionnée, ou null en bout de liste. */
export function selectRelative(step) {
  const o = state.offres;
  if (!o.items.length) return null;
  const index = o.items.findIndex(item => item.id === o.selectedId);
  const next = index === -1 ? (step > 0 ? 0 : o.items.length - 1) : index + step;
  if (next < 0 || next >= o.items.length) return null;
  select(o.items[next].id);
  if (next >= o.items.length - 3) loadMore();   // précharger avant d'atteindre le bas
  return o.items[next];
}

/* ---------- Mises à jour locales après un geste ---------- */

/* L'offre (mise à jour par le serveur) appartient-elle encore au dossier ouvert ? */
function belongsToFolder(offer, folderId = state.offres.folder) {
  const params = FOLDERS[folderId].params;
  if (params.status && offer.statut !== params.status) return false;
  if (params.favorite != null && offer.favorite !== params.favorite) return false;
  return true;
}

/* Remplace une offre par sa version renvoyée par le serveur (en gardant son score), ou la
   retire si elle a quitté le dossier ; la sélection passe à la suivante. Renvoie l'index
   qu'elle occupait (pour la remettre en place si le geste est annulé). */
export function applyUpdate(updated) {
  const o = state.offres;
  const index = o.items.findIndex(item => item.id === updated.id);
  if (index === -1) return -1;
  const merged = { ...o.items[index], ...updated };
  if (belongsToFolder(merged)) {
    o.items[index] = merged;
  } else {
    o.items.splice(index, 1);
    o.total = Math.max(0, o.total - 1);
    if (o.selectedId === updated.id) {
      const next = o.items[index] || o.items[index - 1] || null;
      o.selectedId = next ? next.id : null;
      if (next) markOpened(next);
    }
  }
  notify('offres');
  notify('selection');
  return index;
}

/* Remet une offre à sa place après une annulation (si elle appartient au dossier). */
export function restoreItem(offer, index) {
  const o = state.offres;
  if (!belongsToFolder(offer)) return;
  const at = o.items.findIndex(item => item.id === offer.id);
  if (at !== -1) o.items[at] = offer;
  else {
    o.items.splice(Math.max(0, Math.min(index, o.items.length)), 0, offer);
    o.total += 1;
  }
  o.selectedId = offer.id;
  notify('offres');
  notify('selection');
}

/* Offre à montrer à la fin du prochain chargement : une offre qui vient d'être créée,
   même si les filtres courants l'écartent (une offre saisie à la main a souvent un lieu
   que la normalisation ne reconnaît pas). */
let revealAfterLoad = null;
let selectAfterLoad = null;

export function revealCreatedOffer(offer) {
  revealAfterLoad = offer;
}

/* Sélectionne une offre du dossier ouvert, maintenant si elle y est déjà, sinon à la fin
   du prochain chargement (le dossier vient d'être demandé). */
export function selectWhenLoaded(id) {
  const o = state.offres;
  if (o.status === 'ready' && o.items.some(item => item.id === id)) { select(id); return; }
  selectAfterLoad = id;
}

function consumeReveal() {
  const o = state.offres;
  if (selectAfterLoad) {
    // Une seule tentative : l'offre a pu quitter le dossier entre-temps.
    if (o.items.some(item => item.id === selectAfterLoad)) {
      o.selectedId = selectAfterLoad;
      markOpened(selectedOffer());
    }
    selectAfterLoad = null;
  }
  const offer = revealAfterLoad;
  revealAfterLoad = null;
  if (!offer || o.folder !== 'nouvelles') return;
  if (!o.items.some(item => item.id === offer.id)) {
    o.items = [offer, ...o.items];
    o.total += 1;
  }
  o.selectedId = offer.id;
  markOpened(offer);
}
