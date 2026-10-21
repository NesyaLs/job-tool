/* Volet de droite de l'espace Offres : le détail de l'offre sélectionnée.

   En tête, trois actions toujours au même endroit : Masquer, Favori, Postuler. Puis le
   titre, l'entreprise, le lieu, le contrat, l'ancienneté, le score et son « pourquoi »,
   la description (modifiable) et les sources. */

import { el, fill, icon } from '../core/dom.js';
import { ageLong, dateLong } from '../core/format.js';
import { subscribeWhile } from '../core/store.js';
import { toast } from '../core/toast.js';
import { canToggleHide, saveDescription, toggleFavorite, toggleHide } from './offer-actions.js';
import {
  ExternalLink, ScoreBadge, alsoPublishedOn, contractLabel, offerLinks, placeLabel,
  sourceLabel, whyLine,
} from './offer-format.js';
import { selectedOffer } from './offres-data.js';

let applyHandler = null;
const extras = [];

/* Le tiroir Postuler s'inscrit ici (le détail ne l'importe pas : pas de dépendance croisée). */
export function registerApplyHandler(fn) { applyHandler = fn; }

/* Encart ajouté sous la description (ex. « Ajouter au profil »). `build(offer)` renvoie
   un nœud ou null. */
export function registerDetailExtra(build) { extras.push(build); }

export function applyTo(offer) {
  if (applyHandler && offer) applyHandler(offer);
}

function Actions(offer) {
  const hideAllowed = canToggleHide(offer);
  const hidden = offer.statut === 'seen';
  const applied = offer.statut === 'applied';
  return el('div', { class: 'detail-actions', role: 'toolbar', 'aria-label': 'Actions sur l’offre' },
    el('button', {
      class: 'btn', type: 'button', disabled: !hideAllowed,
      title: hideAllowed ? null : 'Une candidature ne se masque pas.',
      'aria-keyshortcuts': 'H',
      onClick: () => toggleHide(offer),
    }, icon(hidden ? 'show' : 'hide'), hidden ? 'Remettre' : 'Masquer', el('kbd', { 'aria-hidden': 'true' }, 'H')),
    el('button', {
      class: 'btn', type: 'button', 'aria-pressed': String(offer.favorite),
      'aria-keyshortcuts': 'F',
      onClick: () => toggleFavorite(offer),
    }, icon('heart'), 'Favori', el('kbd', { 'aria-hidden': 'true' }, 'F')),
    el('div', { class: 'grow' }),
    applyHandler && el('button', {
      class: 'btn btn-primary', type: 'button', 'aria-keyshortcuts': 'P',
      onClick: () => applyTo(offer),
    }, applied ? 'Documents' : 'Postuler', el('kbd', { class: 'kbd-on-accent', 'aria-hidden': 'true' }, 'P'), icon('arrow')),
  );
}

function StatusLine(offer) {
  if (offer.statut === 'applied') {
    return el('p', { class: 'detail-status' },
      offer.applied_at ? `Candidature envoyée le ${dateLong(offer.applied_at)}.` : 'Candidature envoyée (date inconnue).');
  }
  if (offer.statut === 'seen') {
    return el('p', { class: 'detail-status' },
      `Masquée ${offer.hidden_at ? ageLong(offer.hidden_at) : ''}. Une offre masquée quitte le catalogue `
      + 'quand plus aucune source ne la publie depuis 30 jours ; « Remettre » la ramène dans les nouvelles.');
  }
  if (offer.expired) {
    return el('p', { class: 'detail-status' }, 'Cette annonce n’est plus publiée par ses sources ; elle reste ici parce que vous la suivez.');
  }
  return null;
}

/* Description : lue telle quelle, modifiable en place (source partielle à compléter). */
function Description(offer) {
  const wrap = el('section', { class: 'detail-section', 'aria-labelledby': 'desc-title' });
  let editing = false;

  function paint() {
    const head = el('div', { class: 'detail-section-head' },
      el('h3', { class: 'detail-h2', id: 'desc-title' }, 'Le poste'),
      el('div', { class: 'detail-section-tools' },
        el('button', {
          class: 'btn-link', type: 'button',
          onClick: async () => {
            try {
              await navigator.clipboard.writeText(offer.description || '');
              toast('Description copiée.');
            } catch (err) {
              toast('Copie impossible dans ce navigateur.');
            }
          },
        }, icon('copy'), 'Copier'),
        !editing && el('button', {
          class: 'btn-link', type: 'button', onClick: () => { editing = true; paint(); wrap.querySelector('textarea').focus(); },
        }, icon('edit'), 'Modifier')));

    if (editing) {
      const area = el('textarea', {
        class: 'textarea detail-textarea', rows: '14', value: offer.description || '',
        'aria-label': 'Description de l’offre',
      });
      const done = async () => {
        const ok = await saveDescription(offer, area.value);
        if (ok) { editing = false; paint(); }
      };
      fill(wrap, head, area,
        el('div', { class: 'detail-edit-actions' },
          el('button', { class: 'btn btn-primary btn-sm', type: 'button', onClick: done }, 'Enregistrer'),
          el('button', { class: 'btn btn-sm', type: 'button', onClick: () => { editing = false; paint(); } }, 'Annuler'),
          el('span', { class: 'field-hint' }, 'La description corrigée sert à la génération du CV et de la lettre.')));
      area.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') { event.stopPropagation(); editing = false; paint(); }
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) done();
      });
      return;
    }
    fill(wrap, head, offer.description
      ? el('div', { class: 'detail-description' }, offer.description)
      : el('p', { class: 'field-hint' }, 'La source n’a fourni aucune description. « Modifier » permet de la coller depuis l’annonce.'));
  }

  paint();
  return wrap;
}

function Sources(offer) {
  const links = offerLinks(offer);
  const also = alsoPublishedOn(offer);
  return el('section', { class: 'detail-sources', 'aria-label': 'Sources de l’annonce' },
    also && el('p', { class: 'detail-also' }, also),
    links.length
      ? el('div', { class: 'detail-links' },
        ...links.map(link => ExternalLink(link.url, `Voir l’annonce sur ${sourceLabel(link.source)}`)))
      : el('p', { class: 'field-hint' }, 'Aucun lien fourni par la source.'));
}

function Empty() {
  return el('div', { class: 'state state-center' },
    el('p', { class: 'state-title' }, 'Choisissez une offre'),
    el('p', {}, 'Son détail s’affiche ici. Au clavier : J et K pour parcourir, F pour garder, H pour masquer, P pour postuler.'));
}

export function OfferDetail({ onBack }) {
  const pane = el('section', { class: 'pane-detail offer-detail', 'aria-label': 'Détail de l’offre', tabindex: '-1' });
  let paintedId = null;

  function paint() {
    const offer = selectedOffer();
    const scroll = offer && offer.id === paintedId ? pane.scrollTop : 0;
    paintedId = offer ? offer.id : null;
    if (!offer) { fill(pane, Empty()); return; }
    const tags = [
      placeLabel(offer),
      contractLabel(offer),
      offer.date ? `Publiée ${ageLong(offer.date)}` : 'Date de publication inconnue',
    ].filter(Boolean);
    fill(pane,
      el('button', { class: 'btn btn-sm back-to-list', type: 'button', onClick: onBack }, icon('back'), 'Retour à la liste'),
      Actions(offer),
      el('header', { class: 'detail-head' },
        el('h2', { class: 'detail-title' }, offer.titre),
        el('p', { class: 'detail-company' }, offer.entreprise || 'Entreprise non précisée')),
      el('div', { class: 'tags' },
        ...tags.map(tag => el('span', { class: 'tag' }, tag)),
        offer.private && el('span', { class: 'badge' }, 'Saisie à la main')),
      offer.score != null && el('div', { class: 'detail-match' },
        ScoreBadge(offer.score, { big: true, suffix: ' compatible' }),
        el('p', { class: 'detail-why' }, whyLine(offer))),
      StatusLine(offer),
      Description(offer),
      ...extras.map(build => build(offer)),
      Sources(offer));
    pane.scrollTop = scroll;
  }

  paint();
  subscribeWhile(pane, 'selection', paint);
  // Un geste sur l'offre ouverte (favori, description) change son contenu, pas la sélection.
  subscribeWhile(pane, 'offres', () => {
    const offer = selectedOffer();
    if (offer && offer.id === paintedId && !pane.querySelector('textarea')) paint();
  });
  return pane;
}
