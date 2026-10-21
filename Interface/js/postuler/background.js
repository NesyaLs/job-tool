/* Génération du CV et de la lettre EN ARRIÈRE-PLAN, et les documents prêts d'une offre.
   Aucun rendu ici.

   « Générer le CV et la lettre » lance une tâche sur le serveur et rend la main : on peut
   continuer à parcourir les offres. Tant qu'une tâche tourne, on interroge le serveur toutes
   les quelques secondes ; quand elle finit, un message le dit (avec « Voir »), et une
   notification du navigateur aussi si l'onglet n'est pas au premier plan et que
   l'utilisateur l'a permise.

   Les documents eux-mêmes ne vivent pas dans la tâche : ils sont rangés sur le serveur,
   et relus par offre (`docsFor`) quand on revient sur elle. */

import { listDocumentJobs, listOfferDocuments, startDocumentJob } from '../api/client.js';
import { navigate } from '../core/router.js';
import { notify } from '../core/store.js';
import { toast, toastError } from '../core/toast.js';
import { selectWhenLoaded } from '../offres/offres-data.js';

const POLL_MS = 4000;

const jobs = new Map();       // id -> tâche (réponse du serveur)
const docs = new Map();       // id d'offre -> { status: loading|ready|error, items, error }
let pollTimer = null;

const running = (job) => job.status === 'running';

/* Dernière tâche connue pour une offre (null s'il n'y en a pas). */
export function jobFor(offerId) {
  let latest = null;
  for (const job of jobs.values()) {
    if (job.offer_id === offerId && (!latest || job.started_at > latest.started_at)) latest = job;
  }
  return latest;
}

/* Documents prêts de l'offre ; lancés au premier appel, puis gardés jusqu'à la fin d'une
   tâche qui la concerne. Renvoie l'entrée courante (peut-être encore en chargement). */
export function docsFor(offerId) {
  if (!docs.has(offerId)) loadDocs(offerId);
  return docs.get(offerId);
}

/* Relit les documents d'une offre déjà affichée (après un passage dans le tiroir). */
export function refreshDocs(offerId) {
  if (docs.has(offerId)) loadDocs(offerId).then(() => {}, () => {});
}

async function loadDocs(offerId) {
  // Pas de notification ici : `docsFor` est appelé pendant la construction de l'encart,
  // qui n'est pas encore dans la page (son abonnement se déferait).
  const entry = { status: 'loading', items: docs.get(offerId)?.items || [], error: '' };
  docs.set(offerId, entry);
  try {
    entry.items = await listOfferDocuments(offerId);
    entry.status = 'ready';
  } catch (err) {
    entry.status = 'error';
    entry.error = err.message;
  }
  notify('doc-jobs');
}

export async function startGeneration(offer, { forceNew = false } = {}) {
  // Demandée sur un clic (les navigateurs l'exigent) : c'est le moment où l'on sait que
  // l'utilisateur attend un signal plus tard.
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {});
  }
  try {
    const job = await startDocumentJob(offer.id, forceNew);
    jobs.set(job.id, job);
    notify('doc-jobs');
    toast('CV et lettre en préparation. Vous pouvez continuer : vous serez prévenu à la fin.');
    schedulePoll();
  } catch (err) {
    toastError('Génération impossible : ' + err.message);
  }
}

function schedulePoll() {
  if (pollTimer == null) pollTimer = setTimeout(poll, POLL_MS);
}

async function poll() {
  pollTimer = null;
  let fresh;
  try {
    fresh = await listDocumentJobs();
  } catch (err) {
    schedulePoll();   // serveur momentanément injoignable : on réessaie
    return;
  }
  for (const job of fresh) {
    const before = jobs.get(job.id);
    jobs.set(job.id, job);
    if (before && running(before) && !running(job)) finished(job);
  }
  notify('doc-jobs');
  if (fresh.some(running)) schedulePoll();
}

function finished(job) {
  loadDocs(job.offer_id);
  const what = job.offer_title || 'l’offre';
  const failed = job.steps.filter(step => step.status === 'error');
  const message = job.status === 'done'
    ? `CV et lettre prêts : ${what}.`
    : job.status === 'partial'
      ? `${failed.map(s => (s.kind === 'cv' ? 'CV' : 'Lettre')).join(' et ')} en échec pour ${what} ; l’autre document est prêt.`
      : `La génération a échoué pour ${what}.`;
  const show = () => { navigate('offres', 'favoris'); selectWhenLoaded(job.offer_id); };
  toast(message, { kind: job.status === 'done' ? 'info' : 'error', duration: 10000, action: { label: 'Voir', run: show } });
  if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
    try {
      const note = new Notification('jobtool', { body: message, tag: `documents-${job.id}` });
      note.onclick = () => { window.focus(); show(); note.close(); };
    } catch (err) {
      /* notifications indisponibles (contexte non sécurisé) : le message suffit */
    }
  }
}

/* Au démarrage : une génération lancée avant un rechargement de la page est reprise. */
export async function initBackgroundGeneration() {
  try {
    for (const job of await listDocumentJobs()) jobs.set(job.id, job);
  } catch (err) {
    return;
  }
  notify('doc-jobs');
  if ([...jobs.values()].some(running)) schedulePoll();
}
