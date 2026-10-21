/* Profil › Expériences : les cartes à gauche, l'éditeur à droite.

   L'éditeur n'est reconstruit que si l'on change d'expérience ou de structure (ajout,
   suppression d'une réalisation) : une frappe ne repeint que la liste des cartes, pour que
   le curseur reste où il est. */

import { el, fill, icon, uid } from '../core/dom.js';
import { plural } from '../core/format.js';
import { state, subscribeWhile } from '../core/store.js';
import { openInterview } from '../interview/interview-panel.js';
import { SuggestButton, SuggestionsBox } from './bullet-suggestions.js';
import { EXPERIENCE_TYPES, editProfile, hasNumber, newExperience, newRealisation } from './profile-data.js';
import { SaveStatus } from './save-status.js';

let selectedId = null;
const dismissedHints = new Set();

const typeLabel = (value) => (EXPERIENCE_TYPES.find(t => t.value === value) || {}).label || value;

function periodOf(exp) {
  const format = (value) => {
    if (!value) return '';
    if (value === 'present') return 'aujourd’hui';
    const [year, month] = value.split('-');
    if (!month) return year;
    const date = new Date(Number(year), Number(month) - 1, 1);
    return date.toLocaleDateString('fr-FR', { month: 'short', year: 'numeric' });
  };
  const start = format(exp.debut);
  const end = format(exp.fin);
  return start && end ? `${start} à ${end}` : start || end;
}

function Card(exp, onSelect) {
  const count = exp.realisations.filter(r => r.text.trim()).length;
  return el('li', {},
    el('button', {
      class: 'xcard', type: 'button', 'aria-current': exp.id === selectedId ? 'true' : null,
      onClick: () => onSelect(exp.id),
    },
      el('span', { class: 'x-title' }, exp.titre || 'Expérience sans intitulé'),
      el('span', { class: 'x-sub' }, [exp.org, periodOf(exp)].filter(Boolean).join(' · ') || typeLabel(exp.type)),
      el('span', { class: 'x-meta' },
        plural(count, 'réalisation', 'réalisations'),
        count > 0 && !hasNumber(exp) && el('span', { class: 'x-warn' }, ' · aucun chiffre'))));
}

function Field(label, input, { full = false, hint = '' } = {}) {
  const id = uid('xf');
  input.id = id;
  return el('div', { class: 'field' + (full ? ' field-full' : '') },
    el('label', { class: 'field-label', for: id }, label), input,
    hint && el('p', { class: 'field-hint' }, hint));
}

function Editor(exp, { repaintEditor, onDeleted }) {
  const update = (mutate) => editProfile(profile => mutate(profile.experiences.find(e => e.id === exp.id)), { repaint: false });
  // Changement de structure : l'abonnement au profil repeint la liste et l'éditeur.
  const structural = (mutate) => editProfile(profile => mutate(profile.experiences.find(e => e.id === exp.id)));

  const title = el('h2', { class: 'editor-title' }, exp.titre || 'Nouvelle expérience');
  const input = (key, attrs = {}) => el('input', {
    class: 'input', value: exp[key], ...attrs,
    onInput: (event) => {
      update(e => { e[key] = event.target.value; });
      if (key === 'titre') title.textContent = event.target.value || 'Nouvelle expérience';
    },
  });

  const ongoing = exp.fin === 'present';
  const end = el('input', {
    class: 'input', type: 'month', value: ongoing ? '' : exp.fin, disabled: ongoing,
    onInput: (event) => update(e => { e.fin = event.target.value; }),
  });
  const ongoingBox = el('input', {
    type: 'checkbox', checked: ongoing,
    onChange: (event) => {
      end.disabled = event.target.checked;
      if (event.target.checked) end.value = '';
      update(e => { e.fin = event.target.checked ? 'present' : end.value; });
    },
  });

  const type = el('select', { class: 'select', onChange: (event) => update(e => { e.type = event.target.value; }) },
    ...EXPERIENCE_TYPES.map(t => el('option', { value: t.value }, t.label)));
  type.value = exp.type;

  const summary = el('textarea', {
    class: 'textarea', rows: '3', value: exp.resume,
    onInput: (event) => update(e => { e.resume = event.target.value; }),
  });

  /* Une réalisation par ligne. Entrée ajoute une ligne en dessous ; Retour arrière sur une
     ligne vide la supprime. Seul le texte est montré : les compétences liées à une
     réalisation restent dans le profil (le CV s'en sert), mais surchargeaient l'éditeur. */
  function Realisation(rea, index) {
    const text = el('input', {
      class: 'input', value: rea.text, 'aria-label': `Réalisation ${index + 1}`,
      placeholder: 'Ce que vous avez fait, avec un chiffre si possible',
      onInput: (event) => update(e => { e.realisations.find(r => r.id === rea.id).text = event.target.value; }),
      onKeydown: (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          const fresh = newRealisation();
          structural(e => { e.realisations.splice(index + 1, 0, fresh); });
          focusRealisation(fresh.id);
        } else if (event.key === 'Backspace' && !event.target.value && exp.realisations.length > 0) {
          event.preventDefault();
          const previous = exp.realisations[index - 1];
          structural(e => { e.realisations = e.realisations.filter(r => r.id !== rea.id); });
          if (previous) focusRealisation(previous.id);
        }
      },
    });
    return el('li', { class: 'real-row', dataset: { id: rea.id } },
      el('div', { class: 'real-inputs' }, text),
      el('button', {
        class: 'icon-btn', type: 'button', 'aria-label': `Supprimer la réalisation ${index + 1}`,
        onClick: () => structural(e => { e.realisations = e.realisations.filter(r => r.id !== rea.id); }),
      }, icon('close')));
  }

  const hint = exp.realisations.some(r => r.text.trim()) && !hasNumber(exp) && !dismissedHints.has(exp.id)
    && el('div', { class: 'aside-note' },
      icon('sparkle'),
      el('p', { class: 'grow' }, 'Aucune de ces réalisations n’a de chiffre. L’entretien IA peut vous poser quelques questions pour en trouver.'),
      el('button', { class: 'btn btn-profil btn-sm', type: 'button', onClick: openInterview }, 'Commencer'),
      el('button', { class: 'btn-link', type: 'button', onClick: () => { dismissedHints.add(exp.id); repaintEditor(); } }, 'Plus tard'));

  return el('section', { class: 'pane-detail editor', 'aria-labelledby': 'editor-title' },
    el('p', { class: 'crumb' }, 'Expériences › ', exp.titre || 'Nouvelle expérience'),
    (title.id = 'editor-title', title),
    el('div', { class: 'form-grid profile-grid' },
      Field('Intitulé', input('titre')),
      Field('Entreprise ou organisation', input('org')),
      Field('Type', type),
      el('div', {}),
      Field('Début', input('debut', { type: 'month' })),
      el('div', { class: 'field' },
        el('label', { class: 'field-label', for: (end.id = uid('xf'), end.id) }, 'Fin'),
        end,
        el('label', { class: 'check' }, ongoingBox, 'En cours')),
      Field('Résumé', summary, { full: true, hint: 'Une ou deux phrases de contexte (facultatif).' })),
    el('div', { class: 'field field-full' },
      el('span', { class: 'field-label', id: 'real-label' }, 'Réalisations'),
      el('ul', { class: 'real-list', 'aria-labelledby': 'real-label' }, ...exp.realisations.map(Realisation)),
      el('div', { class: 'add-lines' },
        el('button', {
          class: 'btn-link add-line', type: 'button',
          onClick: () => { const fresh = newRealisation(); structural(e => { e.realisations.push(fresh); }); focusRealisation(fresh.id); },
        }, icon('plus'), 'Ajouter une réalisation'),
        SuggestButton(exp, repaintEditor)),
      SuggestionsBox(exp, {
        repaint: repaintEditor,
        // Les lignes vides (ajoutées puis laissées en blanc) cèdent la place aux puces retenues.
        onAdd: (texts) => structural(e => {
          e.realisations = [
            ...e.realisations.filter(r => r.text.trim()),
            ...texts.map(text => ({ ...newRealisation(), text })),
          ];
        }),
      })),
    hint,
    el('div', { class: 'editor-foot' },
      SaveStatus(),
      el('button', {
        class: 'btn btn-sm btn-danger', type: 'button',
        onClick: () => { onDeleted(); editProfile(profile => { profile.experiences = profile.experiences.filter(e => e.id !== exp.id); }); },
      }, 'Supprimer l’expérience')));
}

function focusRealisation(id) {
  requestAnimationFrame(() => document.querySelector(`.real-row[data-id="${CSS.escape(id)}"] input`)?.focus());
}

export function mountExperiences(main) {
  const list = el('ul', { class: 'xcard-list', 'aria-label': 'Expériences' });
  const editorSlot = el('div', { class: 'editor-slot' });
  const count = el('span', { class: 'list-sub num' });

  function select(id) {
    selectedId = id;
    paintList();
    paintEditor();
    main.classList.add('has-detail');
  }

  function paintList() {
    const experiences = state.profile.experiences;
    count.textContent = plural(experiences.length, 'expérience', 'expériences');
    fill(list, ...experiences.map(exp => Card(exp, select)));
  }

  function paintEditor() {
    const exp = state.profile.experiences.find(e => e.id === selectedId);
    if (!exp) {
      fill(editorSlot, el('section', { class: 'pane-detail' },
        el('div', { class: 'state state-center' },
          el('p', { class: 'state-title' }, state.profile.experiences.length ? 'Choisissez une expérience' : 'Aucune expérience'),
          el('p', {}, state.profile.experiences.length
            ? 'Ses détails et ses réalisations s’éditent ici.'
            : 'Ajoutez votre première expérience, ou laissez l’entretien IA vous guider.'))));
      return;
    }
    fill(editorSlot, Editor(exp, {
      repaintEditor: () => { paintList(); paintEditor(); },
      onDeleted: () => { selectedId = null; main.classList.remove('has-detail'); },
    }));
  }

  if (!state.profile.experiences.some(e => e.id === selectedId)) selectedId = state.profile.experiences[0]?.id || null;

  const pane = el('section', { class: 'pane-list', 'aria-labelledby': 'xlist-title' },
    el('div', { class: 'list-head' }, el('h1', { class: 'list-title', id: 'xlist-title' }, 'Expériences', count)),
    el('div', { class: 'pane-scroll' },
      list,
      el('button', {
        class: 'add-row', type: 'button',
        onClick: () => {
          const fresh = newExperience();
          selectedId = fresh.id;
          main.classList.add('has-detail');
          editProfile(profile => { profile.experiences.unshift(fresh); });
          requestAnimationFrame(() => editorSlot.querySelector('input')?.focus());
        },
      }, icon('plus'), 'Ajouter une expérience')));

  main.replaceChildren(pane, editorSlot);
  paintList();
  paintEditor();
  subscribeWhile(pane, 'profile-meta', paintList);
  // Le profil a été remplacé (annulation, entretien, changement de profil) : tout repeindre.
  subscribeWhile(pane, 'profile', () => {
    if (!state.profile.experiences.some(e => e.id === selectedId)) selectedId = state.profile.experiences[0]?.id || null;
    paintList();
    paintEditor();
  });
}
