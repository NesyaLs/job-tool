/* Un document (CV ou lettre) d'une offre, dans le tiroir Postuler : son état et les
   appels qui le font évoluer. Aucun rendu ici.

   Rien n'est généré sans un clic sur « Générer ». POST /documents rouvre le brouillon de
   l'offre s'il existe (sans rappeler le modèle), sinon génère ; « Régénérer » force une
   génération neuve. Chaque retouche fait recompiler ET recontrôler le document par le
   serveur : on n'affiche que l'état qu'il renvoie. */

import {
  editDocumentFields, fetchArtifact, getDocument, getDocumentSource, openDocument, resetDocumentFields,
  resetDocumentSource, setDocumentSource, setDocumentTemplate,
} from '../api/client.js';

export function createDocumentSession(offer, kind) {
  const listeners = new Set();
  const artifacts = new Map();   // chemin versionné -> URL blob
  let token = 0;                 // dernière ouverture demandée : une réponse périmée est ignorée

  const s = {
    kind,
    offerId: offer.id,
    status: 'idle',              // idle | loading | ready | error
    regenerating: false,
    error: '',
    view: null,                  // réponse du serveur (DocumentView)
    template: null,
    options: {},
    mode: 'preview',             // preview | retouche | advanced
    selectedField: null,
    draft: '',
    saving: false,
    source: { text: '', saved: '', status: 'stale', error: '' },
    stamp: 0,
  };

  const emit = () => listeners.forEach(fn => fn());

  function revokeArtifacts() {
    for (const url of artifacts.values()) URL.revokeObjectURL(url);
    artifacts.clear();
  }

  function applyView(view, { keepSource = false } = {}) {
    s.view = view;
    s.template = view.template;
    s.options = view.options || {};
    s.status = 'ready';
    s.error = '';
    s.saving = false;
    s.regenerating = false;
    if (!keepSource) s.source.status = 'stale';
    revokeArtifacts();
    s.stamp = Date.now();
    if (s.selectedField && !view.fields.some(f => f.id === s.selectedField)) s.selectedField = null;
    const field = view.fields.find(f => f.id === s.selectedField);
    s.draft = field ? field.text : '';
    if (view.source_edited && s.mode === 'retouche') s.mode = 'advanced';
    emit();
  }

  /* Ouvre le document : reprise du brouillon (défaut) ou génération neuve (`forceNew`). */
  async function generate({ forceNew = false } = {}) {
    const mine = ++token;
    s.status = 'loading';
    s.regenerating = forceNew;
    s.error = '';
    emit();
    const body = { offer_id: s.offerId, kind: s.kind, force_new: forceNew };
    if (s.template) body.template = s.template;
    if (s.kind === 'cv' && s.template) body.options = s.options;
    try {
      const view = await openDocument(body);
      if (mine === token) applyView(view);
    } catch (err) {
      if (mine !== token) return;
      s.status = 'error';
      s.error = err.message;
      s.regenerating = false;
      emit();
    }
  }

  /* Rouvre un document déjà prêt, par son identifiant : aucun appel au modèle. */
  async function reopen(docId) {
    const mine = ++token;
    s.status = 'loading';
    s.regenerating = false;
    s.error = '';
    emit();
    try {
      const view = await getDocument(docId);
      if (mine === token) applyView(view);
    } catch (err) {
      if (mine !== token) return;
      s.status = 'error';
      s.error = err.message;
      emit();
    }
  }

  /* Écriture qui renvoie une nouvelle vue du document. */
  async function mutate(call, failureLabel, options) {
    if (s.saving) return false;
    s.saving = true;
    emit();
    try {
      applyView(await call(), options);
      return true;
    } catch (err) {
      s.saving = false;
      s.error = `${failureLabel} : ${err.message}`;
      emit();
      return false;
    }
  }

  const docId = () => s.view.doc_id;

  return {
    state: s,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    emit,
    generate,
    reopen,

    /* Changer de template AVANT génération, ou pour un CV (autre template = autre document) :
       le document affiché est oublié, « Générer » rouvrira celui de ce template. */
    chooseTemplate(template, options = {}) {
      token += 1;
      s.template = template;
      s.options = options;
      s.view = null;
      s.status = 'idle';
      s.mode = 'preview';
      s.error = '';
      revokeArtifacts();
      emit();
    },

    /* Lettre déjà générée : même texte, recompilé dans l'autre template, sans modèle. */
    switchLetterTemplate(template) {
      return mutate(() => setDocumentTemplate(docId(), template), 'Changement de template impossible');
    },

    setMode(mode) { s.mode = mode; s.error = ''; emit(); },

    selectField(id) {
      s.mode = 'retouche';
      s.selectedField = id;
      const field = s.view.fields.find(f => f.id === id);
      s.draft = field ? field.text : '';
      emit();
    },

    saveField() {
      return mutate(() => editDocumentFields(docId(), { [s.selectedField]: s.draft }), 'Retouche non enregistrée');
    },

    resetFields(ids) {
      return mutate(() => resetDocumentFields(docId(), ids), 'Réinitialisation impossible');
    },

    async loadSource() {
      if (s.source.status === 'loading') return;
      s.source.status = 'loading';
      s.source.error = '';
      emit();
      try {
        const body = await getDocumentSource(docId());
        s.source = { text: body.source, saved: body.source, status: 'ready', error: '' };
      } catch (err) {
        s.source.status = 'stale';
        s.source.error = err.message;
      }
      emit();
    },

    /* Source refusée (JSON illisible, champ invalide, compilation) : rien n'est enregistré,
       le texte reste tel quel pour être corrigé. */
    async saveSource() {
      const text = s.source.text;
      s.source.error = '';
      const ok = await mutate(() => setDocumentSource(docId(), text), 'Données refusées', { keepSource: true });
      if (ok) s.source.saved = text;
      else { s.source.error = s.error; s.error = ''; }
      emit();
      return ok;
    },

    resetSource() {
      return mutate(() => resetDocumentSource(docId()), 'Retour aux données générées impossible');
    },

    /* URL affichable d'un artefact protégé (aperçu, PDF), mise en cache jusqu'au prochain
       rendu : le chemin porte l'horodatage du rendu. */
    async artifactUrl(path) {
      const key = `${path}?v=${s.stamp}`;
      if (!artifacts.has(key)) artifacts.set(key, URL.createObjectURL(await fetchArtifact(key)));
      return artifacts.get(key);
    },

    /* Le tiroir se ferme : ses panneaux ne doivent plus être repeints. L'état reste. */
    detachViews() { listeners.clear(); },

    dispose() {
      token += 1;
      revokeArtifacts();
      listeners.clear();
    },
  };
}
