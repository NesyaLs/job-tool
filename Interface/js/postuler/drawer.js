/* Tiroir « Postuler » : s'ouvre par-dessus la liste, avec deux onglets (CV | Lettre).
   Le fermer (Échap, croix) ramène exactement où l'on était : la liste n'est pas touchée,
   le focus revient au bouton qui l'a ouvert.

   « J'ai postulé » est un clic obligatoire : aucun document généré ou téléchargé ne range
   l'offre dans Candidatures à votre place. */

import { el, fill, icon, uid } from '../core/dom.js';
import { dateLong } from '../core/format.js';
import { openDialog } from '../core/dialog.js';
import { markApplied } from '../offres/offer-actions.js';
import { registerApplyHandler, registerDetailExtra } from '../offres/offer-detail.js';
import { initBackgroundGeneration, refreshDocs } from './background.js';
import { createDocumentSession } from './document-session.js';
import { DocumentPanel } from './document-panel.js';
import { OfferDocuments } from './offer-documents.js';
import { defaultOptions, defaultTemplate, loadCatalogue, specOf } from './templates.js';

const TABS = [
  { kind: 'cv', label: 'CV' },
  { kind: 'letter', label: 'Lettre' },
];

/* Sessions de la dernière offre ouverte : rouvrir le tiroir retrouve le document affiché,
   le mode et l'onglet, sans rien relancer. */
let last = { offerId: null, sessions: null, tab: 'cv' };

function sessionsFor(offer) {
  if (last.offerId !== offer.id) {
    if (last.sessions) Object.values(last.sessions).forEach(session => session.dispose());
    last = {
      offerId: offer.id,
      tab: 'cv',
      sessions: { cv: createDocumentSession(offer, 'cv'), letter: createDocumentSession(offer, 'letter') },
    };
  }
  return last.sessions;
}

/* Contenu d'un onglet : le catalogue des templates d'abord, puis le document. */
function TabPanel(session) {
  const panel = el('div', { class: 'drawer-tab-body' },
    el('p', { class: 'field-hint', role: 'status' }, el('span', { class: 'spinner' }), ' Chargement des templates…'));
  loadCatalogue(session.state.kind)
    .then(catalogue => {
      if (!session.state.template || !specOf(catalogue, session.state.template)) {
        const name = defaultTemplate(session.state.kind, catalogue);
        session.chooseTemplate(name, session.state.kind === 'cv' ? defaultOptions(specOf(catalogue, name)) : {});
      }
      fill(panel, DocumentPanel(session, catalogue));
    })
    .catch(err => fill(panel, el('div', { class: 'state', dataset: { kind: 'error' }, role: 'alert' },
      el('p', { class: 'state-title' }, 'Templates indisponibles'),
      el('p', {}, err.message))));
  return panel;
}

function Footer(offer, close) {
  if (offer.statut === 'applied') {
    return el('p', { class: 'drawer-applied' }, icon('check'),
      offer.applied_at ? `Candidature envoyée le ${dateLong(offer.applied_at)}.` : 'Candidature envoyée.');
  }
  return el('div', { class: 'drawer-apply' },
    el('p', { class: 'field-hint' }, 'Une fois votre candidature envoyée sur le site de l’annonce, rangez l’offre dans Candidatures.'),
    el('button', {
      class: 'btn btn-primary', type: 'button',
      onClick: async () => { await markApplied(offer); close(); },
    }, icon('check'), 'J’ai postulé'));
}

function openApplyDrawer(offer) {
  const sessions = sessionsFor(offer);
  const tabIds = Object.fromEntries(TABS.map(t => [t.kind, uid('tab')]));
  const panelIds = Object.fromEntries(TABS.map(t => [t.kind, uid('panel')]));
  const panels = {};
  const tabButtons = {};

  function showTab(kind, { focus = false } = {}) {
    last.tab = kind;
    for (const t of TABS) {
      const on = t.kind === kind;
      tabButtons[t.kind].setAttribute('aria-selected', String(on));
      tabButtons[t.kind].tabIndex = on ? 0 : -1;
      if (on && !panels[t.kind]) {
        panels[t.kind] = el('div', { class: 'drawer-panel', role: 'tabpanel', id: panelIds[t.kind], 'aria-labelledby': tabIds[t.kind] },
          TabPanel(sessions[t.kind]));
        panelsWrap.appendChild(panels[t.kind]);
      }
      if (panels[t.kind]) panels[t.kind].hidden = !on;
    }
    if (focus) tabButtons[kind].focus();
  }

  const tablist = el('div', {
    class: 'drawer-tabs', role: 'tablist', 'aria-label': 'Document',
    onKeydown: (event) => {
      const index = TABS.findIndex(t => t.kind === last.tab);
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        event.preventDefault();
        const next = TABS[(index + (event.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length];
        showTab(next.kind, { focus: true });
      }
    },
  }, ...TABS.map(t => {
    tabButtons[t.kind] = el('button', {
      class: 'drawer-tab', type: 'button', role: 'tab', id: tabIds[t.kind], 'aria-controls': panelIds[t.kind],
      onClick: () => showTab(t.kind),
    }, t.label);
    return tabButtons[t.kind];
  }));
  const panelsWrap = el('div', { class: 'drawer-panels' });

  const dialog = openDialog({
    title: offer.titre,
    className: 'drawer',
    // Une génération ou une retouche faite dans le tiroir change les vignettes du détail.
    onClose: () => { Object.values(sessions).forEach(session => session.detachViews()); refreshDocs(offer.id); },
    content: [
      el('p', { class: 'drawer-sub' }, offer.entreprise || 'Entreprise non précisée'),
      tablist,
      panelsWrap,
      el('footer', { class: 'drawer-footer' }, Footer(offer, () => dialog.close())),
    ],
  });
  showTab(last.tab);
}

/* Ouvre un document déjà prêt (vignette du détail de l'offre) dans son onglet, en mode
   retouche. Rouvrir un brouillon n'appelle pas le modèle. */
export function openReadyDocument(offer, doc) {
  const session = sessionsFor(offer)[doc.kind];
  const s = session.state;
  if (!(s.status === 'ready' && s.view && s.view.doc_id === doc.doc_id)) {
    session.chooseTemplate(doc.template, doc.kind === 'cv' ? doc.options || {} : {});
    session.reopen(doc.doc_id);
  }
  if (s.mode === 'preview') session.setMode('retouche');
  last.tab = doc.kind;
  openApplyDrawer(offer);
}

export function initApplyDrawer() {
  registerApplyHandler(openApplyDrawer);
  registerDetailExtra(offer => OfferDocuments(offer, openReadyDocument));
  initBackgroundGeneration();
}
