/* COLLECTE des offres : état, lancement automatique, suivi.

   Il n'y a plus de bouton « Actualiser » en évidence : au démarrage, si la dernière
   collecte date de plus de AUTO_COLLECT_AFTER_HOURS, on en lance une. Le serveur refuse
   déjà les collectes simultanées (409) et trop rapprochées (429) : demander est sans
   risque. Un bouton discret reste dans le menu de l'avatar. */

import { getCollection, latestCollection, startCollection as postCollection } from '../api/client.js';
import { ageLong, hoursSince } from '../core/format.js';
import { readPref } from '../core/storage.js';
import { notify, state } from '../core/store.js';

export const AUTO_COLLECT_AFTER_HOURS = 6;
const POLL_MS = 2000;

const SOURCE_LABELS = {
  france_travail: 'France Travail', adzuna: 'Adzuna',
  smartrecruiters: 'SmartRecruiters', workday: 'Workday',
};

let pollTimer = null;
const doneListeners = new Set();

/* Appelé à la fin d'une collecte réussie (les listes relisent le catalogue). */
export function onCollectionDone(fn) {
  doneListeners.add(fn);
  return () => doneListeners.delete(fn);
}

function set(status, run, message) {
  state.collect = { status, run: run || state.collect.run, message: message || '' };
  notify('collect');
}

/* Libellé court pour la barre du haut. */
export function collectLabel() {
  const { status, run, message } = state.collect;
  if (status === 'running' && run) {
    const active = run.sources.filter(s => s.status !== 'skipped');
    const finished = active.filter(s => s.status === 'done' || s.status === 'failed');
    return `Collecte en cours (${finished.length}/${active.length} sources)`;
  }
  if (status === 'running') return 'Collecte en cours…';
  if (status === 'error') return message || 'La collecte a échoué';
  if (run && run.status === 'done') return `Offres à jour ${ageLong(run.finished_at || run.started_at)}`;
  if (run && run.status === 'failed') return 'La dernière collecte a échoué';
  return message || 'Aucune collecte pour l’instant';
}

/* Détail complet (infobulle et réglages) : sources, nouvelles offres, erreurs. */
export function collectDetail() {
  const run = state.collect.run;
  if (!run) return '';
  const sources = run.sources
    .filter(s => s.status !== 'skipped')
    .map(s => `${SOURCE_LABELS[s.source] || s.source} : ${s.status === 'running' || s.status === 'pending' ? '…' : s.fetched}`)
    .join(' · ');
  if (run.status === 'running') return sources;
  if (run.status === 'failed') return 'Échec : ' + (run.errors[0] || 'erreur inconnue');
  const summary = run.summary || {};
  let text = `${summary.new_offers || 0} nouvelle(s) sur ${summary.fetched || 0} récupérée(s). ${sources}`;
  if (run.error_count) text += ` · ${run.error_count} erreur(s), les autres sources ont fonctionné`;
  return text;
}

function follow(runId) {
  clearTimeout(pollTimer);
  const tick = async () => {
    try {
      const run = await getCollection(runId);
      if (run.status === 'running') {
        set('running', run);
        pollTimer = setTimeout(tick, POLL_MS);
        return;
      }
      set(run.status === 'failed' ? 'error' : 'idle', run,
        run.status === 'failed' ? 'La collecte a échoué' : '');
      if (run.status === 'done') doneListeners.forEach(fn => fn());
    } catch (err) {
      // Coupure passagère : on ne lâche pas le suivi.
      pollTimer = setTimeout(tick, POLL_MS * 3);
    }
  };
  tick();
}

/* Lance une collecte. Renvoie un message à afficher (ou '' si elle a démarré). */
export async function runCollection() {
  if (state.collect.status === 'running') return 'Une collecte est déjà en cours.';
  set('running', null, 'Lancement de la collecte…');
  try {
    const response = await postCollection();
    const body = await response.json().catch(() => null);
    const detail = body && body.detail;
    if (response.status === 409 && detail && detail.run_id) {
      follow(detail.run_id);
      return 'Une collecte est déjà en cours : suivi repris.';
    }
    if (response.status === 429) {
      set('idle', null);
      return (detail && detail.message) || 'Les offres viennent d’être collectées.';
    }
    if (!response.ok) {
      const message = (detail && (detail.message || detail)) || `HTTP ${response.status}`;
      throw new Error(typeof message === 'string' ? message : `HTTP ${response.status}`);
    }
    set('running', body);
    follow(body.id);
    return '';
  } catch (err) {
    set('error', null, 'La collecte n’a pas pu démarrer');
    return 'La collecte n’a pas pu démarrer : ' + (err.message || 'erreur inconnue');
  }
}

/* Au démarrage : reprendre une collecte en cours, ou en lancer une si les offres sont
   anciennes (et que le poste n'a pas désactivé la collecte automatique). */
export async function initCollection() {
  let run = null;
  try {
    run = await latestCollection();
  } catch (err) {
    set('error', null, 'État de la collecte indisponible');
    return;
  }
  if (run && run.status === 'running') { set('running', run); follow(run.id); return; }
  set('idle', run);
  const age = run ? hoursSince(run.finished_at || run.started_at) : null;
  const stale = age == null || age >= AUTO_COLLECT_AFTER_HOURS;
  if (stale && readPref('autoCollect', true)) runCollection();
}
