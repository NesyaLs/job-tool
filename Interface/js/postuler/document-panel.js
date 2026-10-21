/* Contenu d'un onglet du tiroir (CV ou Lettre) : template, génération, contrôles, aperçu,
   retouche et mode avancé. Se repeint à chaque changement de la session du document. */

import { el, fill, icon, uid } from '../core/dom.js';
import { confirmAction } from '../core/dialog.js';
import { toastError } from '../core/toast.js';
import { MenuButton } from '../shell/menu-button.js';
import { AdvancedPanel } from './advanced.js';
import { Pages } from './preview.js';
import { RetouchePanel } from './retouche.js';
import { defaultOptions, previewFor, rememberOptions, specOf } from './templates.js';

const LABELS = { cv: { name: 'CV', file: 'cv' }, letter: { name: 'lettre', file: 'lettre' } };

/* Les contrôles du serveur, en trois coches. En mode avancé, la traçabilité n'est plus
   jugée : la coche le dit au lieu de mentir. */
function Checks(view) {
  const c = view.checks;
  const items = [
    { ok: c.page_violations.length === 0, label: c.pages > 1 ? `${c.pages} pages` : '1 page' },
    view.source_edited
      ? { ok: null, label: 'Chiffres non vérifiés (mode avancé)' }
      : { ok: c.violations.length === 0, label: 'Aucun chiffre inventé' },
    { ok: c.missing_ats.length === 0, label: 'Lisible par les ATS' },
  ];
  const problems = [
    ...c.page_violations,
    ...(c.layout_warnings || []).map(w => 'Mise en page : ' + w),
    ...c.overruns,
    ...c.violations,
    ...(c.missing_ats.length ? ['Texte non relu dans le PDF : ' + c.missing_ats.map(t => `« ${t} »`).join(', ')] : []),
  ];
  return el('div', { class: 'checks' },
    el('ul', { class: 'check-list', 'aria-label': 'Contrôles du document' },
      ...items.map(item => el('li', {
        class: 'check-item', dataset: { ok: item.ok == null ? 'na' : String(item.ok) },
      }, icon(item.ok === false ? 'cross' : 'check'),
      el('span', {}, item.label),
      item.ok === false && el('span', { class: 'visually-hidden' }, ' : non respecté')))),
    problems.length > 0 && el('details', { class: 'check-details' },
      el('summary', {}, `${problems.length} point(s) à revoir`),
      el('ul', {}, ...problems.map(p => el('li', {}, p)))));
}

export function OptionControl(option, value, onChange) {
  const id = uid('opt');
  if (option.kind === 'bool') {
    return el('label', { class: 'check' },
      el('input', { type: 'checkbox', checked: value, onChange: (event) => onChange(event.target.checked) }),
      option.label);
  }
  const select = el('select', { class: 'select select-sm', id, onChange: (event) => onChange(event.target.value) },
    ...option.choices.map(choice => el('option', { value: choice.value }, choice.label)));
  select.value = value;
  return el('div', { class: 'field field-inline' }, el('label', { class: 'field-label', for: id }, option.label), select);
}

export function DocumentPanel(session, catalogue) {
  const s = session.state;
  const kind = LABELS[s.kind];
  const root = el('div', { class: 'doc-panel' });

  function chooseTemplate(name) {
    const spec = specOf(catalogue, name);
    if (s.kind === 'letter' && s.status === 'ready') {
      session.switchLetterTemplate(name);   // même lettre, autre mise en page, sans modèle
      return;
    }
    session.chooseTemplate(name, s.kind === 'cv' ? defaultOptions(spec) : {});
  }

  function setOption(name, value) {
    const options = { ...s.options, [name]: value };
    rememberOptions(s.template, options);
    session.chooseTemplate(s.template, options);
  }

  function TemplateControls() {
    const spec = specOf(catalogue, s.template);
    const id = uid('tpl');
    const select = el('select', {
      class: 'select', id, disabled: s.saving || s.status === 'loading',
      onChange: (event) => chooseTemplate(event.target.value),
    }, ...catalogue.templates.map(t => el('option', { value: t.name }, t.beta ? `${t.label} (bêta)` : t.label)));
    select.value = s.template;
    return el('div', { class: 'doc-template' },
      el('div', { class: 'field' }, el('label', { class: 'field-label', for: id }, 'Template'), select),
      s.kind === 'cv' && spec && spec.options.length > 0 && el('div', { class: 'doc-options' },
        ...spec.options.map(option => OptionControl(option, s.options[option.name], (v) => setOption(option.name, v)))));
  }

  async function regenerate() {
    const edited = s.view.edited_field_ids.length;
    if ((edited || s.view.source_edited) && !await confirmAction({
      title: 'Régénérer le document ?',
      message: `Une nouvelle génération remplace ce document : ${s.view.source_edited ? 'les données réécrites' : `vos ${edited} retouche(s)`} seront perdues.`,
      confirmLabel: 'Régénérer', danger: true,
    })) return;
    session.generate({ forceNew: true });
  }

  async function download() {
    try {
      const url = await session.artifactUrl(s.view.pdf_url);
      const link = el('a', { href: url, download: `${kind.file}-${s.offerId}.pdf`, hidden: true });
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      toastError('Téléchargement impossible : ' + err.message);
    }
  }

  function Toolbar() {
    const retouching = s.mode === 'retouche';
    const more = el('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Plus d’actions' }, icon('more'));
    return el('div', { class: 'doc-toolbar', role: 'toolbar', 'aria-label': `Actions sur le ${kind.name}` },
      el('button', {
        class: 'btn', type: 'button', 'aria-pressed': String(retouching),
        disabled: s.view.source_edited,
        title: s.view.source_edited ? 'Mode avancé actif : revenez aux données générées pour retoucher les zones.' : null,
        onClick: () => session.setMode(retouching ? 'preview' : 'retouche'),
      }, icon('edit'), 'Retoucher'),
      el('button', { class: 'btn', type: 'button', disabled: s.saving, onClick: regenerate }, icon('refresh'), 'Régénérer'),
      el('button', { class: 'btn', type: 'button', onClick: download }, icon('download'), 'Télécharger'),
      MenuButton({
        button: more, label: 'Plus d’actions', align: 'right',
        buildItems: () => [
          s.mode === 'advanced'
            ? el('button', { class: 'menu-item', type: 'button', role: 'menuitem', tabindex: '-1', onClick: () => session.setMode('preview') }, 'Quitter le mode avancé')
            : el('button', { class: 'menu-item', type: 'button', role: 'menuitem', tabindex: '-1', onClick: () => session.setMode('advanced') }, 'Mode avancé (données JSON)'),
        ],
      }));
  }

  function Idle() {
    const spec = specOf(catalogue, s.template);
    const preview = spec && previewFor(spec, s.options);
    return el('div', { class: 'doc-idle' },
      preview && el('img', { class: 'doc-thumb', src: preview.url, alt: `Exemple du template « ${spec.label} »` }),
      el('div', { class: 'doc-idle-text' },
        el('p', { class: 'state-title' }, s.kind === 'cv' ? 'CV adapté à cette offre' : 'Lettre pour cette offre'),
        el('p', {}, 'Le document est préparé à partir de votre profil, puis le code vérifie qu’il tient sur une page, '
          + 'qu’aucun chiffre n’est inventé et qu’il reste lisible par les ATS. Un document déjà préparé pour cette offre '
          + (s.kind === 'cv' ? 'avec ce template ' : '') + 'est rouvert tel quel, sans nouvelle génération.'),
        el('button', { class: 'btn btn-primary', type: 'button', onClick: () => session.generate() },
          icon('sparkle'), 'Générer')));
  }

  function Loading() {
    return el('div', { class: 'doc-idle', role: 'status' },
      el('div', { class: 'doc-skeleton', 'aria-hidden': 'true' }),
      el('div', { class: 'doc-idle-text' },
        el('p', { class: 'state-title' }, el('span', { class: 'spinner' }), s.regenerating ? ' Nouvelle génération…' : ' Préparation du document…'),
        el('p', {}, 'Le modèle rédige, Typst compile, puis le code vérifie le format. Une première génération prend environ une minute.')));
  }

  function Failure() {
    return el('div', { class: 'state', dataset: { kind: 'error' }, role: 'alert' },
      el('p', { class: 'state-title' }, 'Le document n’a pas pu être préparé'),
      el('p', {}, s.error),
      el('div', { class: 'dialog-actions' },
        el('button', { class: 'btn', type: 'button', onClick: () => session.generate() }, 'Réessayer'),
        el('button', { class: 'btn', type: 'button', onClick: () => session.generate({ forceNew: true }) }, 'Nouvelle génération')));
  }

  function Ready() {
    const side = s.mode === 'retouche' ? RetouchePanel(session) : s.mode === 'advanced' ? AdvancedPanel(session) : null;
    return el('div', { class: 'doc-ready' },
      Toolbar(),
      Checks(s.view),
      s.error && el('p', { class: 'form-error', role: 'alert' }, s.error),
      el('div', { class: 'doc-work' + (side ? ' has-side' : '') },
        el('div', { class: 'doc-canvas' }, Pages(session, { interactive: s.mode === 'retouche' })),
        side));
  }

  function paint() {
    const body = s.status === 'loading' ? Loading()
      : s.status === 'error' ? Failure()
        : s.status === 'ready' ? Ready()
          : Idle();
    fill(root, TemplateControls(), body);
  }

  paint();
  session.subscribe(paint);
  return root;
}
