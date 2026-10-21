/* Templates (mises en page) du CV et de la lettre : catalogue du serveur, template par
   défaut du profil, options retenues.

   Le catalogue vient de GET /documents/templates : aucun template ni aucune option n'est
   connu du front par son nom. Le template par défaut se choisit dans Profil › Templates
   et vit dans le profil (le serveur s'en sert pour générer en arrière-plan). */

import { listTemplates } from '../api/client.js';
import { readPref, writePref } from '../core/storage.js';
import { state } from '../core/store.js';

const PROFILE_KEYS = { cv: 'template_cv', letter: 'template_lettre' };

const catalogues = {};
const pending = {};

/* Catalogue d'un type (cv | letter), chargé une fois. Un échec est oublié : le prochain
   appel réessaie. */
export function loadCatalogue(kind) {
  if (catalogues[kind]) return Promise.resolve(catalogues[kind]);
  if (!pending[kind]) {
    pending[kind] = listTemplates(kind)
      .then(catalogue => { catalogues[kind] = catalogue; return catalogue; })
      .finally(() => { delete pending[kind]; });
  }
  return pending[kind];
}

export function specOf(catalogue, name) {
  return catalogue && catalogue.templates.find(t => t.name === name);
}

/* Template par défaut : celui du profil s'il est toujours proposé, sinon celui du serveur. */
export function defaultTemplate(kind, catalogue) {
  const chosen = state.profile && state.profile[PROFILE_KEYS[kind]];
  return specOf(catalogue, chosen) ? chosen : catalogue.default;
}

/* Choix fait sur ce poste avant que le profil ne le range (ancienne version) : repris une
   fois, puis oublié. Renvoie { template_cv?, template_lettre? } à écrire, ou null. */
export function takeLegacyDefaults() {
  const legacy = readPref('defaultTemplates', null);
  if (!legacy) return null;
  writePref('defaultTemplates', null);
  const found = {};
  for (const [kind, key] of Object.entries(PROFILE_KEYS)) {
    if (legacy[kind] && !state.profile[key]) found[key] = legacy[kind];
  }
  return Object.keys(found).length ? found : null;
}

/* Options d'un template : valeurs complètes et VALIDES d'après sa déclaration serveur.
   Une valeur absente, d'un mauvais type ou hors liste reprend sa valeur par défaut. */
function resolveOptions(spec, values) {
  const given = values && typeof values === 'object' ? values : {};
  return Object.fromEntries(spec.options.map(option => {
    const value = given[option.name];
    const valid = option.kind === 'bool'
      ? typeof value === 'boolean'
      : option.choices.some(choice => choice.value === value);
    return [option.name, valid ? value : option.default];
  }));
}

export function rememberedOptions(spec) {
  return resolveOptions(spec, readPref('templateOptions', {})[spec.name]);
}

/* Options d'un template de CV : celles du profil pour le template par défaut, sinon les
   dernières utilisées sur ce poste. */
export function defaultOptions(spec) {
  const p = state.profile;
  if (p && p.template_cv === spec.name && Object.keys(p.options_cv).length) return resolveOptions(spec, p.options_cv);
  return rememberedOptions(spec);
}

export function rememberOptions(name, options) {
  writePref('templateOptions', { ...readPref('templateOptions', {}), [name]: options });
}

/* Miniature correspondant aux options VISUELLES choisies (la première sinon). */
export function previewFor(spec, values = {}) {
  const match = spec.previews.find(preview =>
    Object.entries(preview.options).every(([key, value]) => values[key] === value));
  return match || spec.previews[0] || null;
}
