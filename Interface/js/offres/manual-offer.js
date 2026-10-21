/* « Ajouter une offre » : une annonce que les sources n'ont pas ramenée. Titre et lien
   obligatoires ; un lien déjà connu est refusé par le serveur (409), sans rien écraser.
   L'offre créée est privée au compte et arrive dans Nouvelles. */

import { createOffer } from '../api/client.js';
import { el, uid } from '../core/dom.js';
import { openDialog } from '../core/dialog.js';
import { navigate } from '../core/router.js';
import { toast } from '../core/toast.js';
import { state } from '../core/store.js';
import { refreshCounts, reloadFolder, revealCreatedOffer } from './offres-data.js';

function Field(label, input, { required = false, hint = '' } = {}) {
  const id = uid('mo');
  input.id = id;
  if (required) input.required = true;
  return el('div', { class: 'field' },
    el('label', { class: 'field-label', for: id }, label, required ? ' (obligatoire)' : ''),
    input,
    hint && el('p', { class: 'field-hint' }, hint));
}

export function openManualOffer() {
  const title = el('input', { class: 'input', autocomplete: 'off' });
  const url = el('input', { class: 'input', type: 'url', placeholder: 'https://…', autocomplete: 'off' });
  const company = el('input', { class: 'input', autocomplete: 'off' });
  const place = el('input', { class: 'input', autocomplete: 'off' });
  const description = el('textarea', { class: 'textarea', rows: '6' });
  const error = el('p', { class: 'form-error', role: 'alert', hidden: true });
  const submit = el('button', { class: 'btn btn-primary', type: 'submit' }, 'Enregistrer l’offre');

  const form = el('form', {
    class: 'form-stack',
    onSubmit: async (event) => {
      event.preventDefault();
      submit.disabled = true;
      error.hidden = true;
      try {
        const offer = await createOffer({
          titre: title.value.trim(), url: url.value.trim(),
          entreprise: company.value.trim(), lieu: place.value.trim(),
          description: description.value,
        });
        dialog.close();
        toast('Offre ajoutée dans Nouvelles.');
        revealCreatedOffer(offer);
        // Déjà dans Nouvelles : on relit la liste ; sinon, y aller la relit.
        if (state.route.space === 'offres' && state.route.section === 'nouvelles') reloadFolder();
        else navigate('offres', 'nouvelles');
        refreshCounts();
      } catch (err) {
        error.textContent = err.status === 409
          ? 'Cette annonce est déjà enregistrée (même lien).'
          : err.message;
        error.hidden = false;
        submit.disabled = false;
      }
    },
  },
    Field('Intitulé du poste', title, { required: true }),
    Field('Lien vers l’annonce', url, { required: true }),
    el('div', { class: 'form-grid' }, Field('Entreprise', company), Field('Lieu', place)),
    Field('Description', description, { hint: 'Collez le texte de l’annonce : il sert à générer le CV et la lettre.' }),
    error,
    el('div', { class: 'dialog-actions' },
      el('button', { class: 'btn', type: 'button', onClick: () => dialog.close() }, 'Annuler'),
      submit));

  const dialog = openDialog({ title: 'Ajouter une offre', content: form });
  title.focus();
}
