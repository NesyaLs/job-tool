/* Profil › Templates : la galerie des mises en page du CV et de la lettre, et le template
   par défaut de chacun. Le choix est rangé dans le profil (enregistrement automatique) :
   le tiroir Postuler le propose d'abord, et la génération en arrière-plan l'utilise. */

import { el, fill, uid } from '../core/dom.js';
import { openDialog } from '../core/dialog.js';
import { OptionControl } from '../postuler/document-panel.js';
import { defaultOptions, defaultTemplate, loadCatalogue, previewFor, rememberOptions, specOf } from '../postuler/templates.js';
import { state } from '../core/store.js';
import { editProfile } from './profile-data.js';

const KINDS = [
  { kind: 'cv', label: 'CV' },
  { kind: 'letter', label: 'Lettre de motivation' },
];

function zoom(url, label) {
  openDialog({ title: label, wide: true, content: el('img', { class: 'template-zoom', src: url, alt: `Exemple du template « ${label} »` }) });
}

function choose(kind, spec) {
  editProfile(profile => {
    if (kind === 'cv') {
      profile.template_cv = spec.name;
      profile.options_cv = defaultOptions(spec);
    } else {
      profile.template_lettre = spec.name;
    }
  });
}

/* Options du template de CV par défaut (couleur…), sous la galerie. */
function DefaultOptions(catalogue) {
  const spec = specOf(catalogue, defaultTemplate('cv', catalogue));
  if (!spec || !spec.options.length) return null;
  const values = defaultOptions(spec);
  return el('div', { class: 'doc-options template-default-options' },
    el('p', { class: 'field-label' }, `Options de « ${spec.label} »`),
    ...spec.options.map(option => OptionControl(option, values[option.name], (value) => {
      const options = { ...values, [option.name]: value };
      rememberOptions(spec.name, options);
      editProfile(profile => { profile.template_cv = spec.name; profile.options_cv = options; });
    })));
}

function Gallery(kind, label, catalogue) {
  const name = uid('tpl');
  const current = defaultTemplate(kind, catalogue);
  return el('fieldset', { class: 'template-gallery' },
    el('legend', { class: 'section-title' }, label),
    el('ul', { class: 'template-grid' },
      ...catalogue.templates.map(spec => {
        const preview = previewFor(spec, kind === 'cv' ? defaultOptions(spec) : {});
        const id = uid('tplc');
        return el('li', { class: 'template-card' },
          preview && el('button', {
            class: 'template-thumb', type: 'button', 'aria-label': `Agrandir l’exemple « ${spec.label} »`,
            onClick: () => zoom(preview.url, spec.label),
          }, el('img', { src: preview.url, alt: '', loading: 'lazy' })),
          el('label', { class: 'check template-choice', for: id },
            el('input', { type: 'radio', name, id, checked: spec.name === current, onChange: () => choose(kind, spec) }),
            el('span', {}, spec.label, spec.beta && el('span', { class: 'badge' }, 'bêta'))),
          spec.description && el('p', { class: 'field-hint' }, spec.description));
      })),
    kind === 'cv' && DefaultOptions(catalogue));
}

/* Lettre type : écrite une fois, elle devient la base de chaque lettre générée (le modèle
   en garde la structure et les phrases, et adapte ce qui dépend de l'offre). */
function LetterExample() {
  const id = uid('lx');
  return el('div', { class: 'field letter-example' },
    el('label', { class: 'section-title', for: id }, 'Votre lettre type (facultatif)'),
    el('p', { class: 'field-hint' },
      'Collez une lettre de motivation que vous avez écrite. Chaque lettre générée partira d’elle : '
      + 'même structure, mêmes tournures, avec l’entreprise, le poste et les arguments adaptés à l’offre. '
      + 'Sans lettre type, la lettre est rédigée à partir de votre profil.'),
    el('textarea', {
      class: 'textarea', id, rows: '14', value: state.profile.lettre_exemple,
      placeholder: 'Madame, Monsieur,\n\n…',
      onInput: (event) => editProfile(profile => { profile.lettre_exemple = event.target.value; }, { repaint: false }),
    }));
}

export function TemplatesSection() {
  const wrap = el('div', { class: 'profile-form' },
    el('p', { class: 'field-hint', role: 'status' }, el('span', { class: 'spinner' }), ' Chargement des templates…'));
  Promise.all(KINDS.map(k => loadCatalogue(k.kind)))
    .then(catalogues => {
      fill(wrap,
        el('p', { class: 'section-intro' }, 'Le template coché sert à « Générer le CV et la lettre » depuis une offre, et est proposé en premier dans le tiroir Postuler, où il reste modifiable pour chaque offre.'),
        ...KINDS.map((k, i) => Gallery(k.kind, k.label, catalogues[i])),
        LetterExample());
    })
    .catch(err => fill(wrap, el('div', { class: 'state', dataset: { kind: 'error' }, role: 'alert' },
      el('p', { class: 'state-title' }, 'Templates indisponibles'), el('p', {}, err.message))));
  return wrap;
}
