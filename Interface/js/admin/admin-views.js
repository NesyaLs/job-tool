/* ADMINISTRATION et JOURNAL LLM — contenu inchangé par rapport à l'ancienne interface,
   accessibles depuis le menu de l'avatar pour les administrateurs.

   La garde est au serveur (403 pour un compte non administrateur, voir
   api/routers/admin.py) : ici, on affiche le refus lisiblement, rien de plus.

   Administration : des COMPTEURS, jamais de contenu. Le Journal fait exception, assumée :
   il montre les prompts et les réponses des dernières générations (le serveur ne garde
   que les 10 dernières de chaque type). */

import { adminOverview, getLlmRun, listLlmRuns } from '../api/client.js';
import { el, fill } from '../core/dom.js';
import { dateFr } from '../core/format.js';
import { state } from '../core/store.js';
import { registerSpace } from '../shell/views.js';

const KINDS = { cv: 'CV', letter: 'Lettre' };

/* « 18/09 14:32 » à partir d'un horodatage ISO-8601. */
function shortDate(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const two = (n) => String(n).padStart(2, '0');
  return `${two(date.getDate())}/${two(date.getMonth() + 1)} ${two(date.getHours())}:${two(date.getMinutes())}`;
}

function Page(title, intro, ...children) {
  return el('div', { class: 'pane-page admin-page' },
    el('h1', { class: 'page-title' }, title),
    el('p', { class: 'section-intro' }, intro),
    ...children);
}

const Loading = () => el('p', { class: 'field-hint', role: 'status' }, el('span', { class: 'spinner' }), ' Chargement…');

function Failure(message, retry) {
  return el('div', { class: 'state', dataset: { kind: 'error' }, role: 'alert' },
    el('p', { class: 'state-title' }, 'Chargement impossible'),
    el('p', {}, message),
    retry && el('button', { class: 'btn', type: 'button', onClick: retry }, 'Réessayer'));
}

function Stat(label, value, hint) {
  return el('div', { class: 'stat' },
    el('div', { class: 'stat-value num' }, String(value)),
    el('div', { class: 'stat-label' }, label),
    hint && el('div', { class: 'field-hint' }, hint));
}

function Overview(data) {
  return el('div', { class: 'admin-body' },
    el('div', { class: 'stats' },
      Stat('Comptes', data.accounts.length),
      Stat('Offres au catalogue', data.catalogue_offers, 'mutualisées entre comptes'),
      Stat('Documents', data.total_documents),
      Stat('Appels au modèle', data.total_llm_calls, 'base des quotas')),
    el('p', { class: 'field-hint' }, 'Base : ' + data.database),
    el('h2', { class: 'section-title' }, 'Comptes'),
    el('div', { class: 'table-wrap' },
      el('table', { class: 'table' },
        el('thead', {}, el('tr', {},
          ...['Compte', 'Offres', 'Favoris', 'Postulées', 'Documents', 'Appels LLM', 'Créé le']
            .map(h => el('th', { scope: 'col' }, h)))),
        el('tbody', {}, ...data.accounts.map(a => el('tr', {},
          el('td', {},
            el('div', {}, a.display_name || '—', a.is_admin && el('span', { class: 'badge' }, 'admin')),
            el('div', { class: 'field-hint' }, a.email)),
          ...[a.offers, a.favorites, a.applied, a.documents, a.llm_calls].map(n => el('td', { class: 'num' }, String(n))),
          el('td', {}, dateFr(a.created_at))))))));
}

function mountOverview(main) {
  main.classList.add('is-single');
  const body = el('div', {}, Loading());
  fill(main, Page('Administration', 'Comptes, volumes et consommation du modèle.', body));
  const load = () => {
    fill(body, Loading());
    adminOverview()
      .then(data => fill(body, Overview(data)))
      .catch(err => fill(body, Failure(err.message, load)));
  };
  load();
}

/* ---------- Journal LLM ---------- */

function Exchange(x) {
  return el('details', { class: 'journal-call' },
    el('summary', {},
      el('span', { class: 'journal-step' }, x.step || 'appel'),
      el('span', { class: 'field-hint' }, ` tentative ${x.attempt}`),
      x.retries_429 > 0 && el('span', { class: 'field-hint' }, ` · ${x.retries_429} × 429`)),
    el('p', { class: 'field-label' }, 'Envoyé au modèle'),
    ...x.messages.map(m => el('div', { class: 'journal-msg' },
      el('p', { class: 'field-hint' }, m.role || 'message'),
      el('pre', { class: 'journal-pre' }, String(m.content == null ? '' : m.content)))),
    el('p', { class: 'field-label' }, 'Réponse'),
    el('pre', { class: 'journal-pre' }, x.response == null ? '(aucune structure renvoyée)' : JSON.stringify(x.response, null, 2)));
}

function mountJournal(main) {
  main.classList.add('is-single');
  const j = { runs: [], selected: null, detail: null };
  const body = el('div', {}, Loading());
  fill(main, Page('Journal LLM',
    'Les dernières générations de CV et de lettres : ce qui est parti au modèle, et ce qu’il a répondu. Le serveur ne conserve que les 10 dernières de chaque type.',
    el('p', { class: 'aside-note journal-warn' }, 'Cet écran affiche le contenu réel des comptes (base d’expériences, offres, textes générés).'),
    body));

  async function open(runId) {
    if (j.selected === runId) { j.selected = null; j.detail = null; paint(); return; }
    j.selected = runId;
    j.detail = null;
    paint();
    try {
      const detail = await getLlmRun(runId);
      if (j.selected === runId) { j.detail = detail; paint(); }
    } catch (err) {
      if (j.selected === runId) { j.detail = { error: err.message }; paint(); }
    }
  }

  function Detail() {
    if (!j.selected) return null;
    if (!j.detail) return el('div', { class: 'journal-detail' }, Loading());
    if (j.detail.error) return el('div', { class: 'journal-detail' }, Failure(j.detail.error));
    const { run, exchanges } = j.detail;
    return el('section', { class: 'journal-detail', 'aria-label': 'Détail de la génération' },
      el('p', { class: 'field-label' }, `${KINDS[run.kind] || run.kind} · ${run.account} · ${exchanges.length} appel(s) · ${run.model || 'modèle inconnu'}`),
      run.error && el('p', { class: 'form-error' }, run.error),
      ...exchanges.map(Exchange));
  }

  function paint() {
    if (!j.runs.length) {
      fill(body, el('div', { class: 'state' },
        el('p', { class: 'state-title' }, 'Aucune génération enregistrée'),
        el('p', {}, 'Générez un CV ou une lettre depuis le tiroir Postuler : l’échange apparaîtra ici.')));
      return;
    }
    fill(body,
      el('div', { class: 'table-wrap' },
        el('table', { class: 'table' },
          el('thead', {}, el('tr', {},
            ...['Date', 'Compte', 'Type', 'Offre', 'Mise en page', 'Appels', 'État'].map(h => el('th', { scope: 'col' }, h)))),
          el('tbody', {}, ...j.runs.map(run => el('tr', { class: run.run_id === j.selected ? 'is-open' : null },
            el('td', {}, el('button', {
              class: 'btn-link', type: 'button', 'aria-expanded': String(run.run_id === j.selected),
              onClick: () => open(run.run_id),
            }, shortDate(run.started_at))),
            el('td', {}, run.account),
            el('td', {}, KINDS[run.kind] || run.kind),
            el('td', {}, run.offer_title || run.offer_id || '—'),
            el('td', {}, run.template || '—'),
            el('td', { class: 'num' }, String(run.calls)),
            el('td', {}, run.status === 'ok' ? 'réussie' : run.status === 'error' ? 'échec' : 'en cours')))))),
      Detail());
  }

  const load = () => {
    fill(body, Loading());
    listLlmRuns(20)
      .then(runs => { j.runs = runs; paint(); })
      .catch(err => fill(body, Failure(err.message, load)));
  };
  load();
}

function mountAdmin(main, section) {
  if (!state.me.isAdmin) {
    main.classList.add('is-single');
    fill(main, el('div', { class: 'pane-page' },
      el('div', { class: 'state state-center', dataset: { kind: 'error' } },
        el('p', { class: 'state-title' }, 'Accès réservé à l’administration'),
        el('a', { class: 'btn', href: '#/offres/nouvelles' }, 'Retour aux offres'))));
    return;
  }
  if (section === 'journal') mountJournal(main);
  else mountOverview(main);
}

export function initAdmin() {
  registerSpace('admin', mountAdmin);
}
