/* DÉCOUVRIR — les nouvelles offres une par une, de la plus compatible à la moins
   compatible (GET /offers/stack, noté par le serveur), en pile de cartes à glisser.

   → ou glisser à droite : garder (favori). ← ou glisser à gauche : masquer.
   Retour arrière (ou Z) : annuler le dernier geste. Les gestes réutilisent l'état du
   compte (favori, statut `seen`) : la liste Nouvelles voit la même chose. Les critères
   sont ceux de Nouvelles (mêmes pastilles, retenues sur ce poste).

   Les gestes sont OPTIMISTES : la carte part tout de suite, et revient si l'écriture
   échoue. Seules `transform` et `opacity` bougent ; le mouvement réduit est respecté par
   la feuille de style. */

import { setOfferFavorite, setOfferStatus, stackOffers } from '../api/client.js';
import { el, fill, icon } from '../core/dom.js';
import { ageLong } from '../core/format.js';
import { bindKey } from '../core/keys.js';
import { state, subscribe } from '../core/store.js';
import { toastError } from '../core/toast.js';
import { onCollectionDone } from '../shell/collect.js';
import { FilterChips } from './filter-chips.js';
import { ExternalLink, ScoreBadge, contractLabel, offerLinks, placeLabel, sourceLabel, whyLine } from './offer-format.js';
import { refreshCounts } from './offres-data.js';

const BATCH = 10;
const LOW_WATER = 4;
const BEHIND = 2;                 // cartes visibles derrière celle du dessus
const LEAVE_MS = 220;
const MATCH_CONTRACTS = ['cdi', 'cdd', 'interim', 'stage', 'alternance', 'freelance'];

function criteria() {
  const f = state.offres.filters;
  return {
    keyword: f.keywords,
    departments: f.departments.join(',') || null,
    contract: f.contracts.filter(c => MATCH_CONTRACTS.includes(c)),
    max_age_days: f.maxAgeDays,
  };
}

function excerpt(text, max = 480) {
  const flat = (text || '').replace(/\s+/g, ' ').trim();
  if (flat.length <= max) return flat;
  const cut = flat.slice(0, max);
  return cut.slice(0, cut.lastIndexOf(' ') > max * 0.6 ? cut.lastIndexOf(' ') : max) + '…';
}

/* Écriture d'un geste, ou de son annulation : toujours une valeur FORCÉE. */
function write(card, action, undo) {
  if (action === 'keep') return setOfferFavorite(card.id, !undo);
  return setOfferStatus(card.id, undo ? 'new' : 'seen');
}

export function mountDiscover(main) {
  const t = { cards: [], remaining: 0, status: 'loading', error: '', history: [], loadingMore: false };
  const pending = new Set();
  let request = 0;                // un chargement parti avant un changement de critères est ignoré
  const stage = el('div', { class: 'discover-stage', 'aria-live': 'polite' });
  const counter = el('span', { class: 'list-sub num' });
  const undoButton = el('button', { class: 'btn', type: 'button', onClick: () => undo() }, icon('undo'), 'Annuler', el('kbd', { 'aria-hidden': 'true' }, '⌫'));
  const hideButton = el('button', { class: 'btn', type: 'button', onClick: () => act('hide') }, icon('hide'), 'Masquer', el('kbd', { 'aria-hidden': 'true' }, '←'));
  const keepButton = el('button', { class: 'btn btn-primary', type: 'button', onClick: () => act('keep') }, icon('heart'), 'Garder', el('kbd', { class: 'kbd-on-accent', 'aria-hidden': 'true' }, '→'));
  let leaving = false;

  async function load({ reset = false } = {}) {
    if (!reset && (t.loadingMore || t.status !== 'ready')) return;
    const token = reset ? ++request : request;
    t.loadingMore = !reset;
    if (reset) { t.cards = []; t.remaining = 0; t.status = 'loading'; paint(); }
    try {
      const page = await stackOffers({
        ...criteria(), limit: BATCH,
        exclude: [...t.cards.map(c => c.id), ...pending],
      });
      if (token !== request) return;
      const known = new Set(t.cards.map(c => c.id));
      t.cards = [...t.cards, ...page.items.filter(c => !known.has(c.id))];
      t.remaining = Math.max(0, page.total - page.items.length);
      t.status = 'ready';
    } catch (err) {
      if (token !== request) return;
      if (reset || !t.cards.length) { t.status = 'error'; t.error = err.message; }
    } finally {
      if (token === request) { t.loadingMore = false; paint(); }
    }
  }

  function topUp() {
    if (t.cards.length <= LOW_WATER && t.remaining > 0) load();
  }

  function act(action) {
    const card = t.cards[0];
    if (!card || leaving) return;
    const node = stage.querySelector('.discover-card.is-top');
    t.cards = t.cards.slice(1);
    const entry = { card, action, settled: null };
    t.history = [...t.history, entry].slice(-30);
    pending.add(card.id);
    if (node) {
      leaving = true;
      node.classList.remove('is-dragging');
      node.dataset.lean = action;
      node.classList.add(action === 'keep' ? 'is-leaving-right' : 'is-leaving-left');
      // La carte suivante monte pendant le vol.
      stage.querySelector('.discover-card[data-depth="1"]')?.setAttribute('data-depth', '0');
      setTimeout(() => { leaving = false; paint(); }, LEAVE_MS);
    } else {
      paint();
    }
    topUp();
    entry.settled = write(card, action, false)
      .then(() => refreshCounts())
      .catch((err) => {
        t.history = t.history.filter(h => h !== entry);
        t.cards = [card, ...t.cards.filter(c => c.id !== card.id)];
        toastError((action === 'keep' ? 'Offre non gardée : ' : 'Offre non masquée : ') + err.message);
        paint();
      })
      .finally(() => pending.delete(card.id));
  }

  async function undo() {
    const last = t.history[t.history.length - 1];
    if (!last) return;
    await last.settled;
    if (!t.history.includes(last)) return;   // l'écriture a échoué : la carte est déjà revenue
    t.history = t.history.filter(h => h !== last);
    t.cards = [last.card, ...t.cards.filter(c => c.id !== last.card.id)];
    if (t.status !== 'ready') t.status = 'ready';
    paint({ returning: last.action });
    try {
      await write(last.card, last.action, true);
      refreshCounts();
    } catch (err) {
      t.cards = t.cards.filter(c => c.id !== last.card.id);
      t.history = [...t.history, last];
      toastError('Impossible d’annuler : ' + err.message);
      paint();
    }
  }

  /* Glisser la carte du dessus (souris ou doigt). Le décalage passe par une propriété CSS,
     en px mais SANS unité (le CSS en tire aussi l'angle) ; un lancer rapide suffit, même en
     deçà du seuil. */
  function attachDrag(node) {
    let start = null;
    let dx = 0;
    let samples = [];
    node.addEventListener('pointerdown', (event) => {
      if (event.button !== 0 || (event.target instanceof Element && event.target.closest('a, button'))) return;
      start = event.clientX;
      dx = 0;
      samples = [{ x: event.clientX, t: event.timeStamp }];
      node.setPointerCapture(event.pointerId);
      node.classList.add('is-dragging');
    });
    node.addEventListener('pointermove', (event) => {
      if (start == null) return;
      dx = event.clientX - start;
      samples = [...samples, { x: event.clientX, t: event.timeStamp }].slice(-6);
      node.style.setProperty('--dx', String(dx));
      node.dataset.lean = dx > 40 ? 'keep' : dx < -40 ? 'hide' : '';
    });
    const end = (event) => {
      if (start == null) return;
      start = null;
      node.classList.remove('is-dragging');
      const a = samples[0];
      const b = samples[samples.length - 1];
      const speed = (b.x - a.x) / Math.max(1, b.t - a.t);   // px/ms
      const threshold = Math.min(130, node.offsetWidth * 0.3);
      const flung = Math.abs(dx) > 40 && Math.abs(speed) > 0.6 && Math.sign(speed) === Math.sign(dx);
      if (event.type !== 'pointercancel' && (Math.abs(dx) > threshold || flung)) act(dx > 0 ? 'keep' : 'hide');
      else { node.style.setProperty('--dx', '0'); node.dataset.lean = ''; }
    };
    node.addEventListener('pointerup', end);
    node.addEventListener('pointercancel', end);
  }

  function Card(card, depth) {
    const links = offerLinks(card);
    const node = el('article', {
      class: 'discover-card' + (depth === 0 ? ' is-top' : ''),
      dataset: { depth: String(depth) },
      'aria-label': `${card.titre}, ${card.entreprise}`,
      'aria-hidden': depth === 0 ? null : 'true',
      inert: depth === 0 ? null : '',
    },
      el('span', { class: 'discover-stamp keep', 'aria-hidden': 'true' }, 'Garder'),
      el('span', { class: 'discover-stamp hide', 'aria-hidden': 'true' }, 'Masquer'),
      el('div', { class: 'discover-card-head' },
        el('div', {},
          el('h2', { class: 'discover-title' }, card.titre),
          el('p', { class: 'detail-company' }, card.entreprise || 'Entreprise non précisée')),
        ScoreBadge(card.score, { big: true })),
      el('div', { class: 'tags' },
        ...[placeLabel(card), contractLabel(card), card.date ? `Publiée ${ageLong(card.date)}` : '']
          .filter(Boolean).map(tag => el('span', { class: 'tag' }, tag))),
      el('p', { class: 'detail-why' }, whyLine(card)),
      el('p', { class: 'discover-excerpt' }, excerpt(card.description) || 'Aucune description fournie par la source.'),
      links.length > 0 && ExternalLink(links[0].url, `Voir l’annonce sur ${sourceLabel(links[0].source)}`));
    if (depth === 0) attachDrag(node);
    return node;
  }

  function paint({ returning = null } = {}) {
    if (leaving) return;
    const top = t.cards[0];
    const left = t.cards.length + t.remaining;
    counter.textContent = t.status === 'ready' && left ? `${left} à trier` : '';
    hideButton.disabled = !top;
    keepButton.disabled = !top;
    undoButton.disabled = t.history.length === 0;
    if (t.status === 'loading') {
      fill(stage, el('p', { class: 'field-hint', role: 'status' }, el('span', { class: 'spinner' }), ' Recherche des offres les plus compatibles…'));
    } else if (t.status === 'error') {
      fill(stage, el('div', { class: 'state', dataset: { kind: 'error' }, role: 'alert' },
        el('p', { class: 'state-title' }, 'Les offres n’ont pas pu être chargées'),
        el('p', {}, t.error),
        el('button', { class: 'btn', type: 'button', onClick: () => load({ reset: true }) }, 'Réessayer')));
    } else if (!top) {
      fill(stage, el('div', { class: 'state state-center' },
        el('p', { class: 'state-title' }, 'Tout est trié'),
        el('p', {}, 'Plus d’offre à proposer pour ces critères. Seules les offres compatibles à 25 % au moins sont proposées ; élargissez les critères pour en voir d’autres.')));
    } else {
      // Du fond vers le dessus : la carte du dessus est la dernière du DOM.
      const cards = t.cards.slice(0, BEHIND + 1).map((card, depth) => Card(card, depth)).reverse();
      fill(stage, ...cards);
      if (returning) {
        // « Annuler » : la carte revient du côté où elle était partie.
        const node = stage.querySelector('.discover-card.is-top');
        node.classList.add(returning === 'keep' ? 'is-leaving-right' : 'is-leaving-left');
        requestAnimationFrame(() => requestAnimationFrame(() =>
          node.classList.remove('is-leaving-right', 'is-leaving-left')));
      }
    }
  }

  const onPage = () => stage.isConnected;
  const unbind = [
    bindKey('arrowright', () => act('keep'), { when: onPage }),
    bindKey('arrowleft', () => act('hide'), { when: onPage }),
    bindKey(['backspace', 'z'], () => undo(), { when: onPage }),
    // Nouveaux critères, ou nouvelle collecte : la pile repart de zéro.
    subscribe('filters', () => { if (onPage()) load({ reset: true }); }),
    onCollectionDone(() => { if (onPage()) load({ reset: true }); }),
  ];

  main.classList.add('is-single');
  fill(main, el('div', { class: 'pane-page discover-page' },
    el('div', { class: 'discover-head' },
      el('h1', { class: 'page-title' }, 'Découvrir ', counter),
      el('p', { class: 'section-intro' },
        'Les nouvelles offres, de la plus compatible à la moins compatible. Glissez à droite pour les garder en favori, à gauche pour les masquer.')),
    FilterChips(),
    stage,
    el('div', { class: 'discover-controls' }, hideButton, undoButton, keepButton)));
  load({ reset: true });
  return () => unbind.forEach(off => off());
}
