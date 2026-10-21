/* ESPACE PROFIL — le profil (base d'expériences), son enregistrement automatique et
   l'annulation. Aucun rendu ici.

   Chaque modification change le profil localement, puis un enregistrement part après un
   court délai (PUT /profile, le profil complet). « Annuler » renvoie le profil tel qu'il
   était avant la dernière série de modifications. Le serveur valide (dates, identifiants)
   et renvoie le profil relu : c'est lui qui fait foi. */

import { getProfile, listTemplates, saveProfile } from '../api/client.js';
import { uid } from '../core/dom.js';
import { notify, state } from '../core/store.js';
import { takeLegacyDefaults } from '../postuler/templates.js';

const SAVE_DELAY_MS = 800;
const UNDO_WINDOW_MS = 6000;

export const EXPERIENCE_TYPES = [
  { value: 'job', label: 'Emploi' },
  { value: 'internship', label: 'Stage' },
  { value: 'school_project', label: 'Projet (école)' },
  { value: 'personal_project', label: 'Projet (perso)' },
];

// Séparateur « Langue — Niveau » : c'est ainsi que le serveur transporte une langue.
const LANGUAGE_SEPARATOR = ' — ';

/* `save` : idle | pending | saving | saved | error. `undo` : profil d'avant la dernière
   série de modifications, proposé quelques secondes après l'enregistrement. */
export const profileState = {
  status: 'idle',     // idle | loading | ready | error (chargement)
  error: '',
  save: 'idle',
  saveError: '',
  undo: null,
  savedAt: 0,
};

let saveTimer = null;
let snapshot = null;      // profil d'avant la série de modifications en cours
let undoTimer = null;

const clone = (value) => structuredClone(value);

function normalize(dto) {
  return {
    nom: dto.nom || '', prenom: dto.prenom || '', telephone: dto.telephone || '', email: dto.email || '',
    soft: dto.soft || [], hard: dto.hard || [], langues: dto.langues || [],
    experiences: (dto.experiences || []).map(e => ({
      id: e.id, titre: e.titre || '', org: e.org || '', type: e.type || 'job',
      debut: e.debut || '', fin: e.fin || '', resume: e.resume || '',
      realisations: (e.realisations || []).map(r => ({ id: r.id, text: r.text || '', skills: r.skills || [] })),
    })),
    a_photo: !!dto.a_photo,
    template_cv: dto.template_cv || '', options_cv: dto.options_cv || {},
    template_lettre: dto.template_lettre || '', lettre_exemple: dto.lettre_exemple || '',
  };
}

export async function loadProfile() {
  profileState.status = 'loading';
  notify('profile');
  try {
    state.profile = normalize(await getProfile());
    profileState.status = 'ready';
    profileState.error = '';
  } catch (err) {
    profileState.status = 'error';
    profileState.error = err.status === 404
      ? 'Aucune base d’expériences n’existe encore pour ce profil. L’entretien IA peut la créer avec vous.'
      : err.message;
    if (err.status === 404) {
      state.profile = normalize({});
      profileState.status = 'ready';
    }
  }
  refreshProfileCounts();
  notify('profile');
  if (profileState.status === 'ready') {
    const legacy = takeLegacyDefaults();
    if (legacy) editProfile(profile => Object.assign(profile, legacy));
  }
}

/* Un profil vide : ni expérience ni compétence. L'application ouvre alors le Profil et
   propose l'entretien IA. */
export function isProfileEmpty() {
  const p = state.profile;
  return !!p && p.experiences.length === 0 && p.hard.length === 0 && p.soft.length === 0;
}

/* Modifie le profil (`mutate(profile)`), puis planifie l'enregistrement. `repaint` : faux
   pour une frappe dans un champ (le champ est déjà à jour, repeindre volerait le focus). */
export function editProfile(mutate, { repaint = true } = {}) {
  if (!snapshot) snapshot = clone(state.profile);
  mutate(state.profile);
  profileState.save = 'pending';
  profileState.undo = null;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushSave, SAVE_DELAY_MS);
  refreshProfileCounts();
  notify(repaint ? 'profile' : 'profile-save');
  if (!repaint) notify('profile-meta');
}

/* Ce qui part au serveur. Les éléments encore vides (ligne qu'on vient d'ajouter, pas
   encore remplie) restent à l'écran mais ne sont pas envoyés : le serveur les refuserait. */
function toDto(profile) {
  const { a_photo: _photo, ...body } = profile;
  const filled = (text) => text.trim() !== '';
  return {
    ...body,
    soft: body.soft.filter(filled),
    hard: body.hard.filter(filled),
    langues: body.langues.filter(filled),
    experiences: body.experiences
      .filter(e => filled(e.titre))
      .map(e => ({ ...e, realisations: e.realisations.filter(r => filled(r.text)) })),
  };
}

export async function flushSave() {
  clearTimeout(saveTimer);
  if (profileState.save !== 'pending') return;
  const before = snapshot;
  snapshot = null;
  profileState.save = 'saving';
  notify('profile-save');
  try {
    // La version locale est gardée telle quelle : elle peut contenir des lignes encore
    // vides que le serveur n'a pas reçues, et l'utilisateur est peut-être en train d'écrire.
    await saveProfile(toDto(state.profile));
    profileState.save = snapshot ? 'pending' : 'saved';
    profileState.saveError = '';
    profileState.savedAt = Date.now();
    offerUndo(before);
  } catch (err) {
    snapshot = snapshot || before;
    profileState.save = 'error';
    profileState.saveError = err.message;
  }
  refreshProfileCounts();
  notify('profile-save');
}

function offerUndo(before) {
  clearTimeout(undoTimer);
  profileState.undo = before;
  undoTimer = setTimeout(() => { profileState.undo = null; notify('profile-save'); }, UNDO_WINDOW_MS);
}

/* « Annuler » : le profil d'avant la dernière série de modifications est renvoyé tel quel. */
export async function undoLastSave() {
  const before = profileState.undo;
  if (!before) return;
  clearTimeout(undoTimer);
  profileState.undo = null;
  state.profile = { ...clone(before), a_photo: state.profile.a_photo };
  snapshot = null;
  profileState.save = 'saving';
  notify('profile');
  try {
    state.profile = { ...normalize(await saveProfile(toDto(state.profile))), a_photo: state.profile.a_photo };
    profileState.save = 'saved';
    profileState.savedAt = Date.now();
  } catch (err) {
    profileState.save = 'error';
    profileState.saveError = err.message;
  }
  refreshProfileCounts();
  notify('profile');
  notify('profile-save');
}

/* Réessayer après un refus du serveur (la saisie locale est conservée). */
export function retrySave() {
  profileState.save = 'pending';
  flushSave();
}

/* Export : la version du serveur (celle qui fait foi), après enregistrement de la saisie en
   cours, téléchargée en fichier JSON. */
export async function exportProfile() {
  await flushSave();
  const dto = await getProfile();
  const who = [dto.prenom, dto.nom].filter(Boolean).join('-').toLowerCase().replace(/[^a-z0-9-]+/g, '') || 'profil';
  const day = new Date().toISOString().slice(0, 10);
  const blob = new Blob([JSON.stringify(dto, null, 2) + '\n'], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `profil-${who}-${day}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ---------- Petites constructions ---------- */

export function newExperience() {
  return { id: uid('exp_'), titre: '', org: '', type: 'job', debut: '', fin: '', resume: '', realisations: [] };
}

export function newRealisation() {
  return { id: uid('ach_'), text: '', skills: [] };
}

export function splitLanguage(value) {
  for (const separator of [LANGUAGE_SEPARATOR, '—', ' - ', '–']) {
    const at = value.indexOf(separator);
    if (at !== -1) return { name: value.slice(0, at).trim(), level: value.slice(at + separator.length).trim() };
  }
  return { name: value.trim(), level: '' };
}

export function joinLanguage({ name, level }) {
  return level.trim() ? `${name.trim()}${LANGUAGE_SEPARATOR}${level.trim()}` : name.trim();
}

/* Une expérience « chiffrée » : au moins une réalisation contient un nombre. */
export const hasNumber = (experience) => experience.realisations.some(r => /\d/.test(r.text));

/* ---------- Compteurs du menu ---------- */

let templateCount = null;

async function refreshProfileCounts() {
  const p = state.profile;
  if (templateCount == null) {
    templateCount = 0;
    listTemplates('cv').then(c => { templateCount = c.templates.length; refreshProfileCounts(); })
      .catch(() => { templateCount = null; });
  }
  state.counts = {
    ...state.counts,
    profil: p ? {
      experiences: p.experiences.length,
      competences: p.hard.length + p.soft.length,
      langues: p.langues.length,
      templates: templateCount || null,
    } : {},
  };
  notify('counts');
}
