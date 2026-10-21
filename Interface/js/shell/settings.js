/* Réglages : clés d'API du compte, collecte (maintenant, et automatique au démarrage) et
   aide des raccourcis clavier. Les clés sont rangées CHIFFRÉES par le serveur et ne
   reviennent jamais au navigateur : on n'affiche que leur provenance (compte, serveur,
   aucune). Le reste est retenu dans ce navigateur. */

import { deleteApiKey, listApiKeys, saveApiKey } from '../api/client.js';
import { el, fill } from '../core/dom.js';
import { openDialog } from '../core/dialog.js';
import { readPref, writePref } from '../core/storage.js';
import { state, subscribeWhile } from '../core/store.js';
import { toast } from '../core/toast.js';
import { AUTO_COLLECT_AFTER_HOURS, collectDetail, collectLabel, runCollection } from './collect.js';

const SHORTCUTS = [
  ['J', 'Offre suivante'],
  ['K', 'Offre précédente'],
  ['F', 'Mettre en favori ou retirer'],
  ['H', 'Masquer l’offre ou la remettre'],
  ['P', 'Postuler'],
  ['Échap', 'Fermer le détail, le tiroir ou le dialogue'],
  ['← →', 'Tri rapide : masquer ou garder'],
];

function ShortcutList() {
  return el('dl', { class: 'shortcut-list' },
    ...SHORTCUTS.flatMap(([key, label]) => [el('dt', {}, el('kbd', {}, key)), el('dd', {}, label)]));
}

function keyOrigin(key) {
  if (key.account) return el('span', { class: 'badge' }, 'votre clé');
  if (key.server) return el('span', { class: 'badge' }, 'clé du serveur');
  return el('span', { class: 'badge badge-warn' }, 'aucune');
}

function ApiKeyRow(key, canStore, refresh) {
  const id = `api-key-${key.name}`;
  const input = el('input', {
    class: 'input', id, type: 'password', autocomplete: 'off', spellcheck: 'false',
    disabled: !canStore,
    placeholder: key.account ? 'Saisir une nouvelle clé pour la remplacer' : 'Coller la clé',
  });
  async function run(action, done) {
    try {
      await action();
      input.value = '';
      toast(done);
      refresh();
    } catch (err) {
      toast(err.message, { kind: 'error' });
    }
  }
  const save = el('button', { class: 'btn btn-sm', type: 'button', disabled: !canStore, onClick: () => {
    const value = input.value.trim();
    if (!value) { input.focus(); return; }
    run(() => saveApiKey(key.name, value), 'Clé enregistrée.');
  } }, 'Enregistrer');
  const remove = key.account && el('button', { class: 'btn btn-sm btn-danger', type: 'button',
    onClick: () => run(() => deleteApiKey(key.name), 'Clé retirée.') }, 'Retirer');
  return el('div', { class: 'api-key-row' },
    el('div', { class: 'field' },
      el('label', { class: 'field-label', for: id }, key.label, keyOrigin(key)),
      input),
    save, remove);
}

function ApiKeysBlock() {
  const list = el('div', { class: 'api-key-list', 'aria-live': 'polite' }, el('p', { class: 'field-hint' }, 'Chargement…'));
  async function refresh() {
    try {
      const { can_store: canStore, keys } = await listApiKeys();
      fill(list,
        !canStore && el('p', { class: 'field-hint' },
          'Le serveur ne peut pas chiffrer de clé (JOBTOOL_SECRET_KEY absente) : les clés du serveur sont utilisées.'),
        keys.map((key) => ApiKeyRow(key, canStore, refresh)));
    } catch (err) {
      fill(list, el('p', { class: 'field-hint' }, err.message));
    }
  }
  refresh();
  return el('section', { class: 'settings-block' },
    el('h3', { class: 'settings-title' }, 'Clés d’API'),
    el('p', { class: 'field-hint' },
      'Vos clés servent à vous seul et remplacent celles du serveur. Elles sont chiffrées et ne sont jamais réaffichées. '
      + 'Mistral génère CV, lettres et entretien ; France Travail et Adzuna alimentent la collecte.'),
    list);
}

export function openSettings() {
  const status = el('p', { class: 'field-hint', 'aria-live': 'polite' });
  const collectButton = el('button', { class: 'btn', type: 'button', onClick: async () => {
    const message = await runCollection();
    if (message) toast(message);
  } }, 'Collecter les offres maintenant');

  function paintCollect() {
    status.textContent = [collectLabel(), collectDetail()].filter(Boolean).join(' — ');
    collectButton.disabled = state.collect.status === 'running';
  }

  const auto = el('input', { type: 'checkbox', checked: readPref('autoCollect', true),
    onChange: (event) => writePref('autoCollect', event.target.checked) });

  const content = [
    ApiKeysBlock(),
    el('section', { class: 'settings-block' },
      el('h3', { class: 'settings-title' }, 'Collecte des offres'),
      status,
      el('label', { class: 'check' }, auto,
        `Collecter au démarrage si les offres datent de plus de ${AUTO_COLLECT_AFTER_HOURS} h`),
      el('div', {}, collectButton),
    ),
    el('section', { class: 'settings-block' },
      el('h3', { class: 'settings-title' }, 'Raccourcis clavier'),
      ShortcutList(),
    ),
  ];
  const { node } = openDialog({ title: 'Réglages', content });
  paintCollect();
  subscribeWhile(node, 'collect', paintCollect);
}
