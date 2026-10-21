/* Volet de gauche de l'espace Offres : titre du dossier, pastilles de filtre, liste.

   Une ligne : titre, entreprise et lieu, score, ancienneté. Une offre jamais ouverte est
   en gras, avec un point. La ligne sélectionnée porte un trait vertical à gauche. */

import { el, fill, icon } from '../core/dom.js';
import { ageShort, plural } from '../core/format.js';
import { state, subscribeWhile } from '../core/store.js';
import { FilterChips } from './filter-chips.js';
import { openManualOffer } from './manual-offer.js';
import { ScoreBadge, placeLabel } from './offer-format.js';
import { FOLDERS, isUnread, loadMore, reloadFolder, select } from './offres-data.js';

const listActions = [];

/* Bouton ajouté à l'en-tête d'un dossier par un autre module (ex. « Tri rapide »). */
export function registerListAction(folderId, build) {
  listActions.push({ folderId, build });
}

function rowAge(offer, folderId) {
  if (folderId === 'candidatures') return ageShort(offer.applied_at);
  if (folderId === 'masquees') return ageShort(offer.hidden_at);
  return ageShort(offer.date);
}

function Row(offer, folderId, onOpen) {
  const selected = offer.id === state.offres.selectedId;
  const unread = folderId === 'nouvelles' && isUnread(offer);
  const age = rowAge(offer, folderId);
  return el('li', { class: 'offer-item' },
    el('button', {
      class: 'offer-row' + (unread ? ' is-unread' : '') + (folderId === 'masquees' ? ' is-hidden' : ''),
      type: 'button',
      dataset: { id: offer.id },
      'aria-current': selected ? 'true' : null,
      onClick: () => { select(offer.id); onOpen(); },
    },
      el('span', { class: 'offer-dot', 'aria-hidden': 'true' }),
      unread && el('span', { class: 'visually-hidden' }, 'Non lue. '),
      el('span', { class: 'offer-main' },
        el('span', { class: 'offer-title' }, offer.titre),
        el('span', { class: 'offer-sub' }, [offer.entreprise, placeLabel(offer)].filter(Boolean).join(', '))),
      el('span', { class: 'offer-side' },
        ScoreBadge(offer.score),
        age && el('span', { class: 'offer-age num' }, age)),
    ));
}

function Skeleton() {
  return el('div', { 'aria-hidden': 'true' },
    ...Array.from({ length: 6 }, () => el('div', { class: 'skeleton-row' })));
}

function Body(folderId, onOpen) {
  const o = state.offres;
  const folder = FOLDERS[folderId];
  if (o.status === 'loading' && !o.items.length) {
    return el('div', {}, el('p', { class: 'visually-hidden', role: 'status' }, 'Chargement des offres…'), Skeleton());
  }
  if (o.status === 'error') {
    return el('div', { class: 'state', dataset: { kind: 'error' }, role: 'alert' },
      el('p', { class: 'state-title' }, 'Les offres n’ont pas pu être chargées'),
      el('p', {}, o.error),
      el('button', { class: 'btn', type: 'button', onClick: reloadFolder }, 'Réessayer'));
  }
  if (!o.items.length) {
    const searching = !!state.query;
    const catalogueEmpty = folder.filtered && !searching && state.counts.offres
      && Object.values(state.counts.offres).every(n => !n);
    return el('div', { class: 'state' },
      el('p', { class: 'state-title' }, searching ? 'Aucun résultat' : 'Rien ici pour l’instant'),
      el('p', {}, searching
        ? `Aucune offre de ce dossier ne correspond à « ${state.query} ».`
        : (catalogueEmpty ? folder.emptyCatalogue : folder.empty)));
  }
  const list = el('ul', { class: 'offer-list', 'aria-label': `Offres : ${folder.label}` },
    ...o.items.map(offer => Row(offer, folderId, onOpen)));
  const more = o.items.length < o.total && el('div', { class: 'list-more' },
    el('button', { class: 'btn', type: 'button', disabled: o.loadingMore, onClick: loadMore },
      o.loadingMore ? 'Chargement…' : `Afficher plus (${o.total - o.items.length} restantes)`));
  return el('div', {}, list, more);
}

/* `onOpen` : appelé quand une ligne est choisie (sur écran étroit, le détail passe devant). */
export function OfferList(folderId, { onOpen }) {
  const folder = FOLDERS[folderId];
  const count = el('span', { class: 'list-sub num' });
  const body = el('div', { class: 'pane-scroll' });

  const actions = el('div', { class: 'list-actions' },
    ...listActions.filter(a => a.folderId === folderId).map(a => a.build()),
    el('button', { class: 'btn btn-sm', type: 'button', onClick: openManualOffer },
      icon('plus'), 'Ajouter une offre'));

  const pane = el('section', { class: 'pane-list', 'aria-labelledby': 'list-title' },
    el('div', { class: 'list-head' },
      el('h1', { class: 'list-title', id: 'list-title' }, folder.label, count),
      actions),
    folder.filtered && FilterChips(),
    body);

  function paint() {
    const o = state.offres;
    count.textContent = o.status === 'ready' ? plural(o.total, 'offre', 'offres') : '';
    const scroll = body.scrollTop;
    fill(body, Body(folderId, onOpen));
    body.scrollTop = scroll;
  }

  function paintSelection() {
    for (const row of body.querySelectorAll('.offer-row')) {
      const selected = row.dataset.id === state.offres.selectedId;
      if (selected) {
        row.setAttribute('aria-current', 'true');
        row.classList.remove('is-unread');
        row.querySelector('.visually-hidden')?.remove();
        row.scrollIntoView({ block: 'nearest' });
      } else {
        row.removeAttribute('aria-current');
      }
    }
  }

  paint();
  subscribeWhile(pane, 'offres', paint);
  subscribeWhile(pane, 'selection', paintSelection);
  return pane;
}
