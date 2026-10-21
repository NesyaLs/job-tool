/* Mode avancé : toutes les données que reçoit la mise en page (contact, compétences,
   formation, dates, puces…), en JSON, recompilées en quelques secondes sans modèle.
   Budget et traçabilité ne sont plus jugés ; la page et le texte extractible, si. */

import { el, uid } from '../core/dom.js';
import { confirmAction } from '../core/dialog.js';

export function AdvancedPanel(session) {
  const s = session.state;
  if (s.source.status !== 'ready') {
    if (s.source.status === 'stale' && !s.source.error) queueMicrotask(() => session.loadSource());
    return el('aside', { class: 'side-panel', 'aria-label': 'Mode avancé' },
      s.source.error
        ? el('div', { class: 'state', dataset: { kind: 'error' }, role: 'alert' },
          el('p', { class: 'state-title' }, 'Données indisponibles'),
          el('p', {}, s.source.error),
          el('button', { class: 'btn btn-sm', type: 'button', onClick: () => session.loadSource() }, 'Réessayer'))
        : el('p', { class: 'field-hint', role: 'status' }, el('span', { class: 'spinner' }), ' Chargement des données…'));
  }

  const id = uid('adv');
  const status = el('span', { class: 'field-hint', 'aria-live': 'polite' });
  const paintStatus = () => {
    status.textContent = s.source.text !== s.source.saved ? 'Modifications non recompilées' : 'À jour avec l’aperçu';
  };
  const area = el('textarea', {
    class: 'textarea source-textarea', id, spellcheck: 'false', rows: '22', value: s.source.text,
    onInput: (event) => { s.source.text = event.target.value; paintStatus(); },
    onKeydown: (event) => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); session.saveSource(); return; }
      if (event.key === 'Tab' && !event.shiftKey) {
        // On édite du JSON : Tab indente. Maj+Tab et Échap restent là pour quitter le champ.
        event.preventDefault();
        event.target.setRangeText('  ', event.target.selectionStart, event.target.selectionEnd, 'end');
        s.source.text = event.target.value;
        paintStatus();
      }
    },
  });
  paintStatus();

  return el('aside', { class: 'side-panel side-panel-wide', 'aria-label': 'Mode avancé' },
    el('label', { class: 'field-label', for: id }, 'Données du document (JSON)',
      s.view.source_edited && el('span', { class: 'badge badge-warn' }, 'données réécrites')),
    el('p', { class: 'field-hint' },
      'Tout ce que reçoit la mise en page, modifiable directement. Une clé absente reprend sa valeur par défaut. '
      + 'Budget et traçabilité ne sont plus vérifiés ; la page et le texte lisible par les ATS, si.'),
    s.source.error && el('p', { class: 'form-error', role: 'alert' }, s.source.error),
    area,
    el('div', { class: 'side-panel-row' }, status, el('span', { class: 'field-hint' }, 'Tab indente · Ctrl+Entrée recompile')),
    el('div', { class: 'side-panel-row' },
      el('button', { class: 'btn btn-primary btn-sm', type: 'button', disabled: s.saving, onClick: () => session.saveSource() },
        s.saving ? 'Recompilation…' : 'Recompiler'),
      el('button', {
        class: 'btn btn-sm', type: 'button', disabled: s.saving,
        onClick: () => { s.source.text = s.source.saved; s.source.error = ''; session.emit(); },
      }, 'Annuler les modifications'),
      s.view.source_edited && el('button', {
        class: 'btn btn-sm btn-danger', type: 'button', disabled: s.saving,
        onClick: async () => {
          const ok = await confirmAction({
            title: 'Revenir aux données générées ?',
            message: 'Les modifications faites en mode avancé seront perdues.',
            confirmLabel: 'Revenir aux données générées', danger: true,
          });
          if (ok) session.resetSource();
        },
      }, 'Revenir aux données générées')));
}
