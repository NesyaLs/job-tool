/* Retouche : cliquer une zone de l'aperçu, réécrire son texte, enregistrer. Le serveur
   recompile et recontrôle le document à chaque enregistrement. Sans modèle. */

import { el, uid } from '../core/dom.js';

/* Les zones réécrites à la main sortent de la garantie anti-fabrication (le code vérifie
   qu'une puce pointe vers un fait réel, pas que le texte saisi en soit une reformulation
   fidèle) : c'est dit, et elles restent listées. */
function EditedList(session) {
  const s = session.state;
  const ids = s.view.edited_field_ids;
  if (!ids.length) return null;
  return el('div', { class: 'retouche-edited' },
    el('p', { class: 'field-label' }, `${ids.length} zone(s) retouchée(s) à la main`),
    el('p', { class: 'field-hint' }, 'Ces textes ne sont plus des reformulations vérifiées de votre profil : relisez-les.'),
    el('div', { class: 'retouche-chips' },
      ...ids.map(id => el('button', {
        class: 'chip', type: 'button', 'aria-pressed': String(id === s.selectedField),
        onClick: () => session.selectField(id),
      }, id))),
    el('button', { class: 'btn btn-sm btn-danger', type: 'button', disabled: s.saving, onClick: () => session.resetFields([]) },
      'Tout remettre comme généré'));
}

export function RetouchePanel(session) {
  const s = session.state;
  const field = s.view.fields.find(f => f.id === s.selectedField);
  if (!field) {
    return el('aside', { class: 'side-panel', 'aria-label': 'Retouche' },
      el('p', { class: 'state-title' }, 'Retoucher'),
      el('p', { class: 'field-hint' }, 'Cliquez une zone de l’aperçu pour en réécrire le texte. Chaque enregistrement recompile le document et revérifie le format.'),
      EditedList(session));
  }
  const id = uid('rt');
  const count = el('span', { class: 'field-hint num', 'aria-live': 'polite' });
  const area = el('textarea', {
    class: 'textarea', id, rows: '7', value: s.draft,
    onInput: (event) => { s.draft = event.target.value; paintCount(); save.disabled = s.saving || s.draft === field.text; },
    onKeydown: (event) => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); session.saveField(); } },
  });
  const save = el('button', {
    class: 'btn btn-primary btn-sm', type: 'button', disabled: s.saving || s.draft === field.text,
    onClick: () => session.saveField(),
  }, s.saving ? 'Recompilation…' : 'Enregistrer');
  function paintCount() { count.textContent = `${area.value.length} caractères`; }
  paintCount();
  queueMicrotask(() => area.isConnected && area.focus());

  return el('aside', { class: 'side-panel', 'aria-label': 'Retouche' },
    el('label', { class: 'field-label', for: id }, 'Texte de la zone', field.edited && el('span', { class: 'badge badge-warn' }, 'retouché')),
    area,
    el('div', { class: 'side-panel-row' }, count, el('span', { class: 'field-hint' }, 'Ctrl+Entrée pour enregistrer')),
    el('div', { class: 'side-panel-row' },
      save,
      field.edited && el('button', { class: 'btn btn-sm', type: 'button', disabled: s.saving, onClick: () => session.resetFields([field.id]) },
        'Revenir au texte généré')),
    field.edited && el('div', { class: 'retouche-original' },
      el('p', { class: 'field-label' }, 'Texte généré d’origine'),
      el('p', {}, field.original)),
    EditedList(session));
}
