/* Profil › Identité : nom, prénom, e-mail, téléphone, et la photo.

   La photo ne passe pas par le profil : elle a sa propre route (envoi, lecture, suppression),
   et l'aperçu est récupéré avec le jeton (un `<img src>` chargé par le navigateur
   n'emporterait pas l'en-tête d'authentification). */

import { deletePhoto, fetchPhoto, uploadPhoto } from '../api/client.js';
import { el, fill, uid } from '../core/dom.js';
import { state } from '../core/store.js';
import { toast, toastError } from '../core/toast.js';
import { editProfile } from './profile-data.js';

const FIELDS = [
  { key: 'prenom', label: 'Prénom', autocomplete: 'given-name' },
  { key: 'nom', label: 'Nom', autocomplete: 'family-name' },
  { key: 'email', label: 'E-mail', type: 'email', autocomplete: 'email' },
  { key: 'telephone', label: 'Téléphone', type: 'tel', autocomplete: 'tel' },
];

let photoVersion = Date.now();

function Photo() {
  const wrap = el('div', { class: 'photo' });
  let busy = false;
  let objectUrl = null;

  const picker = el('input', {
    class: 'visually-hidden', type: 'file', accept: 'image/jpeg,image/png,image/webp', tabindex: '-1',
    'aria-hidden': 'true',
    onChange: async (event) => {
      const file = event.target.files[0];
      event.target.value = '';
      if (!file) return;
      busy = true; paint();
      try {
        const dto = await uploadPhoto(file);
        state.profile.a_photo = !!dto.a_photo;
        photoVersion = Date.now();
        toast('Photo enregistrée.');
      } catch (err) {
        toastError('Photo refusée : ' + err.message);
      }
      busy = false; paint();
    },
  });

  async function remove() {
    busy = true; paint();
    try {
      await deletePhoto();
      state.profile.a_photo = false;
      toast('Photo supprimée.');
    } catch (err) {
      toastError('Suppression impossible : ' + err.message);
    }
    busy = false; paint();
  }

  function paint() {
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
    const hasPhoto = state.profile.a_photo;
    const preview = hasPhoto
      ? el('img', { class: 'photo-preview', alt: 'Votre photo de profil' })
      : el('div', { class: 'photo-preview photo-empty', 'aria-hidden': 'true' }, 'Aucune photo');
    if (hasPhoto) {
      fetchPhoto(photoVersion)
        .then(blob => { objectUrl = URL.createObjectURL(blob); preview.src = objectUrl; })
        .catch(() => { preview.alt = 'Photo indisponible'; });
    }
    fill(wrap, preview,
      el('div', { class: 'photo-actions' },
        el('button', { class: 'btn btn-sm', type: 'button', disabled: busy, onClick: () => picker.click() },
          busy ? 'Envoi…' : hasPhoto ? 'Remplacer la photo' : 'Ajouter une photo'),
        picker,
        hasPhoto && !busy && el('button', { class: 'btn btn-sm btn-danger', type: 'button', onClick: remove }, 'Supprimer'),
        el('p', { class: 'field-hint' }, 'JPEG, PNG ou WebP, recadrée en carré. Utilisée par les templates qui affichent une photo.')));
  }

  paint();
  return wrap;
}

export function IdentitySection() {
  const p = state.profile;
  return el('div', { class: 'profile-form' },
    el('div', { class: 'form-grid' },
      ...FIELDS.map(field => {
        const id = uid('id');
        return el('div', { class: 'field' },
          el('label', { class: 'field-label', for: id }, field.label),
          el('input', {
            class: 'input', id, type: field.type || 'text', autocomplete: field.autocomplete, value: p[field.key],
            onInput: (event) => editProfile(profile => { profile[field.key] = event.target.value; }, { repaint: false }),
          }));
      })),
    el('div', { class: 'field' }, el('span', { class: 'field-label' }, 'Photo'), Photo()));
}

