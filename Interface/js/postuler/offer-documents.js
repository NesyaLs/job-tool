/* Encart « Documents » du détail d'une offre : générer le CV et la lettre en arrière-plan,
   puis les retrouver ici en revenant sur l'offre.

   Montré pour une offre en favori, et pour toute offre qui a déjà des documents. Chaque
   document prêt a sa vignette (première page) : un clic l'ouvre dans le tiroir, en
   retouche ; « Télécharger » récupère le PDF. */

import { fetchArtifact } from '../api/client.js';
import { confirmAction } from '../core/dialog.js';
import { el, fill, icon } from '../core/dom.js';
import { dateLong } from '../core/format.js';
import { subscribeWhile } from '../core/store.js';
import { toastError } from '../core/toast.js';
import { docsFor, jobFor, startGeneration } from './background.js';

const LABELS = { cv: 'CV', letter: 'Lettre de motivation' };
const FILES = { cv: 'cv', letter: 'lettre' };
const thumbs = new Map();   // doc_id + date -> URL blob de la première page

function thumbnail(doc, image) {
  const key = `${doc.doc_id}@${doc.updated_at}`;
  if (thumbs.has(key)) { image.src = thumbs.get(key); return; }
  fetchArtifact(`${doc.preview_url}?v=${encodeURIComponent(doc.updated_at)}`)
    .then(blob => { const url = URL.createObjectURL(blob); thumbs.set(key, url); image.src = url; })
    .catch(() => { image.alt = 'Aperçu indisponible'; });
}

async function download(offer, doc) {
  try {
    const url = URL.createObjectURL(await fetchArtifact(`${doc.pdf_url}?v=${encodeURIComponent(doc.updated_at)}`));
    const link = el('a', { href: url, download: `${FILES[doc.kind]}-${offer.id}.pdf`, hidden: true });
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) {
    toastError('Téléchargement impossible : ' + err.message);
  }
}

function DocCard(offer, doc, open) {
  const image = el('img', { class: 'offer-doc-img', alt: `Première page : ${LABELS[doc.kind]}` });
  thumbnail(doc, image);
  return el('li', { class: 'offer-doc' },
    el('button', {
      class: 'offer-doc-thumb', type: 'button', title: 'Ouvrir pour modifier',
      'aria-label': `Modifier : ${LABELS[doc.kind]}`,
      onClick: () => open(offer, doc),
    }, image),
    el('div', { class: 'offer-doc-meta' },
      el('p', { class: 'offer-doc-title' }, LABELS[doc.kind]),
      el('p', { class: 'field-hint' }, `Préparé le ${dateLong(doc.updated_at)}`),
      el('div', { class: 'offer-doc-actions' },
        el('button', { class: 'btn btn-sm', type: 'button', onClick: () => open(offer, doc) }, icon('edit'), 'Modifier'),
        el('button', { class: 'btn btn-sm', type: 'button', onClick: () => download(offer, doc) }, icon('download'), 'Télécharger'))));
}

function Progress(job) {
  const label = { pending: 'en attente', running: 'en cours…', done: 'prêt', error: 'en échec' };
  return el('div', { class: 'offer-doc-progress', role: 'status' },
    el('p', {}, el('span', { class: 'spinner' }), ' Préparation en arrière-plan. Vous pouvez continuer : un message vous préviendra.'),
    el('ul', { class: 'offer-doc-steps' },
      ...job.steps.map(step => el('li', { dataset: { status: step.status } },
        `${LABELS[step.kind]} : ${label[step.status] || step.status}`))));
}

export function OfferDocuments(offer, open) {
  const section = el('section', { class: 'detail-section offer-docs', 'aria-labelledby': 'docs-title' });

  function paint() {
    const job = jobFor(offer.id);
    const docs = docsFor(offer.id);
    const items = docs.items;
    const busy = job && job.status === 'running';
    section.hidden = !offer.favorite && !items.length && !busy;
    if (section.hidden) { section.replaceChildren(); return; }

    const failed = job && !busy ? job.steps.filter(step => step.status === 'error') : [];
    const complete = items.length === 2;
    const regenerate = async () => {
      const ok = await confirmAction({
        title: 'Régénérer le CV et la lettre ?',
        message: 'Deux nouveaux documents remplaceront ceux-ci, retouches comprises.',
        confirmLabel: 'Régénérer', danger: true,
      });
      if (ok) startGeneration(offer, { forceNew: true });
    };

    fill(section,
      el('div', { class: 'detail-section-head' },
        el('h3', { class: 'detail-h2', id: 'docs-title' }, 'Documents'),
        complete && !busy && el('div', { class: 'detail-section-tools' },
          el('button', { class: 'btn-link', type: 'button', onClick: regenerate }, icon('refresh'), 'Régénérer'))),
      busy && Progress(job),
      items.length > 0 && el('ul', { class: 'offer-doc-list' }, ...items.map(doc => DocCard(offer, doc, open))),
      docs.status === 'error' && el('p', { class: 'form-error', role: 'alert' }, docs.error),
      failed.length > 0 && el('p', { class: 'form-error', role: 'alert' },
        ...failed.map(step => `${LABELS[step.kind]} : ${step.error}. `)),
      !busy && !complete && el('div', { class: 'offer-doc-start' },
        el('button', { class: 'btn btn-primary', type: 'button', onClick: () => startGeneration(offer) },
          icon('sparkle'), items.length ? 'Générer le document manquant' : 'Générer le CV et la lettre'),
        el('p', { class: 'field-hint' }, 'Avec les templates par défaut de votre profil (Profil › Templates). '
          + 'La génération continue en arrière-plan : vous serez prévenu quand les documents seront prêts.')));
  }

  paint();
  subscribeWhile(section, 'doc-jobs', paint);
  return section;
}
