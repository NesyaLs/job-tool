/* Profils multiples (administrateurs) : sélecteur en haut de l'espace Profil, et gestion
   (créer vide ou par copie, renommer, supprimer). Le profil actif est choisi CÔTÉ SERVEUR :
   il vaut pour toutes les routes (profil, documents, entretien). Les offres restent
   communes aux profils d'un compte. */

import {
  activateProfile, createProfile, deleteProfile, listProfiles, renameProfile,
} from '../api/client.js';
import { el, fill, uid } from '../core/dom.js';
import { confirmAction, openDialog } from '../core/dialog.js';
import { notify, state, subscribeWhile } from '../core/store.js';
import { toast, toastError } from '../core/toast.js';
import { flushSave, loadProfile } from './profile-data.js';

function apply(body) {
  state.profiles = { allowed: !!body.allowed, active: body.active || '', list: body.profiles || [] };
  notify('profiles');
}

export async function loadProfiles() {
  try {
    apply(await listProfiles());
  } catch (err) {
    /* information facultative : sans elle, pas de sélecteur */
  }
}

/* Bascule : ce qui dépend du profil est relu (base d'expériences, photo). */
async function switchProfile(key) {
  await flushSave();   // ne rien perdre de la saisie en cours sur l'ancien profil
  try {
    apply(await activateProfile(key));
  } catch (err) {
    toastError('Changement de profil impossible : ' + err.message);
    return;
  }
  await loadProfile();
  const name = (state.profiles.list.find(p => p.key === key) || {}).name || '';
  toast(`Profil actif : ${name}.`);
}

async function call(promise, message) {
  try {
    apply(await promise);
    if (message) toast(message);
    return true;
  } catch (err) {
    toastError(err.message);
    return false;
  }
}

function openManager() {
  const body = el('div', { class: 'form-stack' });
  let renaming = null;   // clé du profil en cours de renommage

  function RenameForm(profile) {
    const input = el('input', { class: 'input', value: profile.name, 'aria-label': `Nouveau nom de « ${profile.name} »`, required: true });
    queueMicrotask(() => input.focus());
    return el('form', {
      class: 'profiles-rename',
      onSubmit: async (event) => {
        event.preventDefault();
        if (await call(renameProfile(profile.key, input.value.trim()), 'Profil renommé.')) { renaming = null; paint(); }
      },
    }, input, el('button', { class: 'btn btn-sm btn-primary', type: 'submit' }, 'Enregistrer le nom'));
  }

  function paint() {
    const p = state.profiles;
    const nameId = uid('pn');
    const fromId = uid('pf');
    const name = el('input', { class: 'input', id: nameId, required: true });
    const from = el('select', { class: 'select', id: fromId },
      el('option', { value: '' }, 'Profil vide'),
      ...p.list.map(x => el('option', { value: x.key }, `Copie de « ${x.name} »`)));
    fill(body,
      el('ul', { class: 'profiles-list' },
        ...p.list.map(x => el('li', { class: 'profiles-item' }, el('div', { class: 'profiles-row' },
          el('span', { class: 'grow' }, x.name, x.key === p.active && el('span', { class: 'badge' }, 'actif')),
          x.key !== p.active && el('button', { class: 'btn btn-sm', type: 'button', onClick: () => switchProfile(x.key).then(paint) }, 'Utiliser'),
          !x.principal && el('button', {
            class: 'btn btn-sm', type: 'button', 'aria-expanded': String(renaming === x.key),
            onClick: () => { renaming = renaming === x.key ? null : x.key; paint(); },
          }, 'Renommer'),
          !x.principal && el('button', {
            class: 'btn btn-sm btn-danger', type: 'button',
            onClick: async () => {
              const ok = await confirmAction({
                title: `Supprimer « ${x.name} » ?`,
                message: 'Sa base d’expériences, sa photo, son entretien et ses documents générés seront effacés.',
                confirmLabel: 'Supprimer le profil', danger: true,
              });
              if (!ok) return;
              const wasActive = x.key === p.active;
              if (await call(deleteProfile(x.key), 'Profil supprimé.') && wasActive) await loadProfile();
              paint();
            },
          }, 'Supprimer')),
          renaming === x.key && RenameForm(x)))),
      el('form', {
        class: 'form-grid',
        onSubmit: async (event) => {
          event.preventDefault();
          if (await call(createProfile(name.value.trim(), from.value || null), 'Profil créé.')) paint();
        },
      },
        el('div', { class: 'field' }, el('label', { class: 'field-label', for: nameId }, 'Nouveau profil'), name),
        el('div', { class: 'field' }, el('label', { class: 'field-label', for: fromId }, 'À partir de'), from),
        el('div', {}, el('button', { class: 'btn btn-primary btn-sm', type: 'submit' }, 'Créer le profil'))),
      el('p', { class: 'field-hint' }, 'Le profil actif sert partout : cette page, la génération des CV et lettres, et l’entretien IA.'));
  }

  paint();
  openDialog({ title: 'Profils', content: body });
}

/* En-tête du menu dans l'espace Profil : rien pour un compte sans droits. */
export function ProfilesSwitcher() {
  const wrap = el('div', { class: 'profiles-switcher' });
  function paint() {
    const p = state.profiles;
    if (!p.allowed) { fill(wrap); return; }
    const id = uid('ps');
    const select = el('select', { class: 'select', id, onChange: (event) => switchProfile(event.target.value) },
      ...p.list.map(x => el('option', { value: x.key }, x.name)));
    select.value = p.active;
    fill(wrap,
      el('label', { class: 'field-label', for: id }, 'Profil actif'),
      select,
      el('button', { class: 'btn-link', type: 'button', onClick: openManager }, 'Gérer les profils'));
  }
  paint();
  subscribeWhile(wrap, 'profiles', paint);
  return wrap;
}
