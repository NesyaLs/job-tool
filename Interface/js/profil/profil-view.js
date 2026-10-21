/* Espace Profil : un espace calme, sans offres. Cinq sections, un enregistrement
   automatique, et l'entretien IA toujours à portée en bas du menu. */

import { el, fill, icon } from '../core/dom.js';
import { navigate } from '../core/router.js';
import { state, subscribe } from '../core/store.js';
import { openInterview } from '../interview/interview-panel.js';
import { registerDetailExtra } from '../offres/offer-detail.js';
import { registerSidebarFooter, registerSidebarHeader } from '../shell/sidebar.js';
import { registerSpace } from '../shell/views.js';
import { ProfileGauge } from './completeness.js';
import { mountExperiences } from './experiences.js';
import { IdentitySection } from './identity.js';
import { LanguagesSection } from './languages.js';
import { toastError } from '../core/toast.js';
import { exportProfile, isProfileEmpty, loadProfile, profileState } from './profile-data.js';
import { ProfilesSwitcher, loadProfiles } from './profiles-switcher.js';
import { SaveStatus } from './save-status.js';
import { SkillBridge } from './skill-bridge.js';
import { SkillsSection } from './skills.js';
import { TemplatesSection } from './template-gallery.js';

const PAGES = {
  identite: { title: 'Identité', build: IdentitySection, intro: 'Vos coordonnées, reprises en tête du CV et de la lettre.' },
  competences: { title: 'Compétences', build: SkillsSection, intro: 'Les compétences que le CV peut mettre en avant, par catégorie.' },
  langues: { title: 'Langues', build: LanguagesSection, intro: 'Chaque langue avec son niveau.' },
  templates: { title: 'Templates', build: TemplatesSection, intro: '' },
};

function EmptyProfileNote() {
  if (!isProfileEmpty()) return null;
  return el('div', { class: 'aside-note empty-note' },
    icon('sparkle'),
    el('p', { class: 'grow' }, 'Votre profil est vide. L’entretien IA vous pose quelques questions et vous propose expériences, réalisations et compétences ; vous gardez la main sur chaque élément.'),
    el('button', { class: 'btn btn-profil btn-sm', type: 'button', onClick: openInterview }, 'Commencer l’entretien'));
}

function Loading() {
  return el('div', { class: 'pane-page' },
    el('p', { class: 'field-hint', role: 'status' }, el('span', { class: 'spinner' }), ' Chargement du profil…'));
}

function LoadError() {
  return el('div', { class: 'pane-page' },
    el('div', { class: 'state', dataset: { kind: 'error' }, role: 'alert' },
      el('p', { class: 'state-title' }, 'Le profil n’a pas pu être chargé'),
      el('p', {}, profileState.error),
      el('button', { class: 'btn', type: 'button', onClick: loadProfile }, 'Réessayer')));
}

function mountProfil(main, section) {
  let unsubscribe = null;

  function render() {
    main.className = 'main';
    if (profileState.status !== 'ready') {
      main.classList.add('is-single');
      fill(main, profileState.status === 'error' ? LoadError() : Loading());
      return;
    }
    if (section === 'experiences') {
      mountExperiences(main);
      const note = EmptyProfileNote();
      if (note) main.querySelector('.pane-list .list-head')?.after(note);
      return;
    }
    const page = PAGES[section];
    main.classList.add('is-single');
    fill(main, el('div', { class: 'pane-page profile-page' },
      el('h1', { class: 'page-title' }, page.title),
      page.intro && el('p', { class: 'section-intro' }, page.intro),
      EmptyProfileNote(),
      page.build(),
      page.autosave !== false && el('div', { class: 'page-foot' }, SaveStatus())));
  }

  render();
  // Les Expériences gèrent leurs propres repeints ; les autres pages se reconstruisent quand
  // le profil change de structure (ajout, retrait, annulation, autre profil).
  unsubscribe = subscribe('profile', () => {
    if (section !== 'experiences' || profileState.status !== 'ready' || !main.querySelector('.xcard-list')) render();
  });
  return () => unsubscribe();
}

async function onExport() {
  try {
    await exportProfile();
  } catch (err) {
    toastError('Export impossible : ' + err.message);
  }
}

function SidebarFooter() {
  return el('div', { class: 'sidebar-profil-actions' },
    el('button', { class: 'btn btn-profil sidebar-ia', type: 'button', onClick: openInterview },
      icon('sparkle'), 'Entretien IA'),
    el('button', { class: 'btn btn-sm sidebar-export', type: 'button', onClick: onExport },
      icon('download'), 'Exporter le profil (JSON)'));
}

/* `openIfEmpty` : au premier lancement (aucune adresse demandée), un profil vide ouvre
   l'espace Profil, où l'entretien IA est proposé. */
export function initProfil({ openIfEmpty }) {
  registerSpace('profil', mountProfil);
  registerSidebarHeader('profil', ProfilesSwitcher);
  registerSidebarFooter('profil', SidebarFooter);
  // Côté Offres : la jauge du profil, et le pont d'une offre vers Profil › Compétences.
  registerSidebarFooter('offres', ProfileGauge);
  registerDetailExtra(SkillBridge);
  loadProfiles();
  loadProfile().then(() => {
    if (openIfEmpty && isProfileEmpty() && state.route.space === 'offres') navigate('profil', 'experiences');
  });
}
