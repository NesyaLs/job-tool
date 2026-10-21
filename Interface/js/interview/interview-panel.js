/* ENTRETIEN IA — panneau ouvert depuis le bouton « Entretien IA » de l'espace Profil.

   Conversation à gauche, propositions à droite. Le modèle ne touche jamais au profil : il
   PROPOSE, et chaque proposition s'accepte, se modifie puis s'accepte, ou se refuse. Toute
   la logique (validation, garde-fou sur les chiffres, application au profil) est côté
   serveur : le panneau affiche la vue que chaque route renvoie en entier.

   À la fermeture, le profil est relu : les propositions acceptées l'ont modifié. */

import {
  acceptProposals, getInterview, gotoInterviewSection, rejectProposals, restartInterview,
  sendInterviewMessage, skipInterviewSection,
} from '../api/client.js';
import { el, fill, uid } from '../core/dom.js';
import { confirmAction, openDialog } from '../core/dialog.js';
import { toast, toastError } from '../core/toast.js';
import { loadProfile } from '../profil/profile-data.js';

const KIND_LABELS = {
  identite: 'Coordonnées', formation: 'Formation', langue: 'Langue',
  experience: 'Expérience', realisation: 'Réalisation', competence: 'Compétence',
};
const IDENTITY_LABELS = { name: 'Nom', email: 'E-mail', phone: 'Téléphone', location: 'Ville', link: 'Lien' };
const EXPERIENCE_TYPES = [
  ['job', 'Emploi'], ['internship', 'Stage'], ['school_project', 'Projet (école)'], ['personal_project', 'Projet (perso)'],
];
const typeLabel = (value) => (EXPERIENCE_TYPES.find(([v]) => v === value) || [])[1] || value;
const period = (start, end) => [start, end === 'present' ? 'aujourd’hui' : end].filter(Boolean).join(' → ');

export function openInterview() {
  const s = { view: null, busy: false, pendingText: '', editing: null, drafts: {}, selected: new Set(), error: '' };

  const log = el('div', { class: 'iv-log', role: 'log', 'aria-live': 'polite', 'aria-label': 'Conversation' });
  const head = el('div', { class: 'iv-head' });
  const side = el('div', { class: 'iv-side' });
  const inputId = uid('iv');
  const input = el('textarea', {
    class: 'textarea iv-input', id: inputId, rows: '3',
    placeholder: 'Votre réponse… (Entrée pour envoyer, Maj+Entrée pour aller à la ligne)',
    onKeydown: (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(); } },
  });
  const sendButton = el('button', { class: 'btn btn-profil', type: 'button', onClick: () => send() }, 'Envoyer');

  async function call(request) {
    s.busy = true;
    paint();
    try {
      s.view = await request();
      s.error = '';
      return true;
    } catch (err) {
      s.error = err.message;
      toastError(err.message);
      return false;
    } finally {
      s.busy = false;
      paint();
    }
  }

  async function send() {
    const text = input.value.trim();
    if (!text || s.busy) return;
    s.pendingText = text;
    input.value = '';
    const ok = await call(() => sendInterviewMessage(text));
    s.pendingText = '';
    if (!ok) input.value = text;   // rien n'est perdu si le modèle échoue
    paint();
    input.focus();
  }

  async function decide(decisions) {
    const ok = await call(() => acceptProposals(decisions));
    if (ok) toast(decisions.length > 1 ? `${decisions.length} éléments ajoutés à votre profil.` : 'Ajouté à votre profil.');
    return ok;
  }

  /* ---------- Plan et progression ---------- */

  function paintHead() {
    const v = s.view;
    if (!v) { fill(head); return; }
    fill(head,
      el('ol', { class: 'iv-steps', 'aria-label': 'Plan de l’entretien' },
        ...v.sections.map(section => el('li', {},
          el('button', {
            class: 'iv-step', type: 'button', dataset: { status: section.status }, disabled: s.busy,
            'aria-current': section.status === 'current' ? 'step' : null,
            onClick: () => section.status !== 'current' && call(() => gotoInterviewSection(section.id)),
          }, section.label)))),
      el('div', { class: 'iv-progress' },
        el('meter', { class: 'iv-meter', min: '0', max: String(v.progress_total), value: String(v.progress_done), 'aria-label': 'Progression' }),
        el('span', { class: 'field-hint num' }, `${v.progress_done}/${v.progress_total} sections`),
        !v.finished && el('button', { class: 'btn-link', type: 'button', disabled: s.busy,
          onClick: () => call(() => skipInterviewSection(v.current)) }, 'Passer cette section'),
        el('button', {
          class: 'btn-link', type: 'button', disabled: s.busy,
          onClick: async () => {
            if (await confirmAction({
              title: 'Recommencer l’entretien ?',
              message: 'La conversation et les propositions en attente seront effacées ; votre profil est conservé.',
              confirmLabel: 'Recommencer',
            })) call(restartInterview);
          },
        }, 'Recommencer')));
  }

  /* ---------- Conversation ---------- */

  function paintLog() {
    const v = s.view;
    if (!v) {
      fill(log, s.error
        ? el('div', { class: 'state', dataset: { kind: 'error' } }, el('p', { class: 'state-title' }, 'Entretien indisponible'), el('p', {}, s.error),
          el('button', { class: 'btn btn-sm', type: 'button', onClick: () => call(getInterview) }, 'Réessayer'))
        : el('p', { class: 'field-hint' }, el('span', { class: 'spinner' }), ' Chargement de l’entretien…'));
      return;
    }
    const nodes = v.messages.map(m => el('div', { class: `iv-msg iv-msg-${m.role}`, id: `iv-${m.id}` },
      el('p', { class: 'visually-hidden' }, m.role === 'user' ? 'Vous :' : 'Assistant :'),
      el('div', { class: 'iv-bubble' }, m.text)));
    if (s.pendingText) nodes.push(el('div', { class: 'iv-msg iv-msg-user is-sending' }, el('div', { class: 'iv-bubble' }, s.pendingText)));
    if (s.busy && s.pendingText) nodes.push(el('p', { class: 'field-hint' }, el('span', { class: 'spinner' }), ' L’assistant écrit…'));
    if (v.turns >= v.max_turns) {
      nodes.push(el('p', { class: 'field-hint' }, 'Cette session a atteint sa limite d’échanges. Recommencez un entretien pour continuer : votre profil est conservé.'));
    }
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    fill(log, ...nodes);
    if (atBottom || s.pendingText) log.scrollTop = log.scrollHeight;
    input.disabled = s.busy;
    sendButton.disabled = s.busy;
  }

  /* ---------- Propositions ---------- */

  function describe(p) {
    const d = p.payload;
    switch (p.kind) {
      case 'identite': return [`${IDENTITY_LABELS[d.field] || d.field} : `, el('strong', {}, d.value)];
      case 'formation': return [el('strong', {}, d.degree), ` — ${d.school}`, el('div', { class: 'field-hint' }, period(d.start, d.end))];
      case 'langue': return [el('strong', {}, d.name), d.level ? ` — ${d.level}` : ''];
      case 'experience': return [el('strong', {}, d.title), ` — ${d.organization}`,
        el('div', { class: 'field-hint' }, `${typeLabel(d.experience_type)} · ${period(d.start, d.end)}`),
        d.summary && el('div', {}, d.summary)];
      case 'realisation': return [el('div', {}, d.text), d.skills && d.skills.length && el('div', { class: 'field-hint' }, d.skills.join(', '))];
      case 'competence': return [el('strong', {}, d.name), ' ', el('span', { class: 'field-hint' }, d.category === 'soft' ? 'savoir-être' : 'compétence technique'),
        d.evidence && el('div', { class: 'field-hint' }, 'Exemple : ', d.evidence)];
      default: return [JSON.stringify(d)];
    }
  }

  function EditForm(p) {
    // Brouillon gardé hors du DOM : une réponse qui repeint le panneau ne perd rien.
    const d = (s.drafts[p.id] ||= structuredClone(p.payload));
    const fields = [];
    const text = (key, label, { area = false, list = false } = {}) => {
      const id = uid('ivf');
      const value = d[key] == null ? '' : Array.isArray(d[key]) ? d[key].join(', ') : d[key];
      const node = el(area ? 'textarea' : 'input', {
        class: area ? 'textarea' : 'input', id, rows: area ? '2' : null, value,
        onInput: (event) => { d[key] = list ? event.target.value.split(',').map(x => x.trim()).filter(Boolean) : event.target.value; },
      });
      fields.push(el('div', { class: 'field' }, el('label', { class: 'field-label', for: id }, label), node));
    };
    const select = (key, label, options) => {
      const id = uid('ivf');
      const node = el('select', { class: 'select', id, onChange: (event) => { d[key] = event.target.value; } },
        ...options.map(([value, lab]) => el('option', { value }, lab)));
      node.value = d[key];
      fields.push(el('div', { class: 'field' }, el('label', { class: 'field-label', for: id }, label), node));
    };
    const dates = () => { text('start', 'Début (AAAA-MM)'); text('end', 'Fin (AAAA-MM ou present)'); };
    switch (p.kind) {
      case 'identite': select('field', 'Champ', Object.entries(IDENTITY_LABELS)); text('value', 'Valeur'); break;
      case 'formation': text('degree', 'Diplôme'); text('school', 'Établissement'); dates(); break;
      case 'langue': text('name', 'Langue'); text('level', 'Niveau'); break;
      case 'experience': select('experience_type', 'Type', EXPERIENCE_TYPES); text('title', 'Intitulé'); text('organization', 'Organisation'); dates(); text('summary', 'Résumé', { area: true }); break;
      case 'realisation': text('text', 'Réalisation', { area: true }); text('skills', 'Compétences (séparées par des virgules)', { list: true }); break;
      case 'competence': text('name', 'Compétence'); select('category', 'Catégorie', [['hard', 'Compétence technique'], ['soft', 'Savoir-être']]); break;
      default: break;
    }
    const stop = () => { s.editing = null; delete s.drafts[p.id]; paint(); };
    return el('div', { class: 'iv-edit' }, ...fields,
      el('div', { class: 'iv-actions' },
        el('button', { class: 'btn btn-profil btn-sm', type: 'button', disabled: s.busy,
          onClick: async () => { if (await decide([{ id: p.id, payload: d }])) stop(); } }, 'Accepter cette version'),
        el('button', { class: 'btn btn-sm', type: 'button', onClick: stop }, 'Annuler')));
  }

  function Proposal(p) {
    const checkId = uid('ivp');
    return el('li', { class: 'iv-prop' + (p.blocking ? ' is-blocking' : '') },
      el('div', { class: 'iv-prop-head' },
        !p.blocking && el('input', {
          type: 'checkbox', id: checkId, checked: s.selected.has(p.id), 'aria-label': 'Sélectionner cette proposition',
          onChange: (event) => { if (event.target.checked) s.selected.add(p.id); else s.selected.delete(p.id); paintSide(); },
        }),
        el('span', { class: 'badge' }, KIND_LABELS[p.kind] || p.kind)),
      s.editing === p.id
        ? EditForm(p)
        : el('div', {},
          el('div', { class: 'iv-prop-body' }, ...describe(p)),
          p.warnings.length > 0 && el('p', { class: 'iv-warn' }, 'À vérifier : ', p.warnings.join(' ; '), '. Modifiez la proposition pour l’accepter.'),
          el('div', { class: 'iv-actions' },
            !p.blocking && el('button', { class: 'btn btn-profil btn-sm', type: 'button', disabled: s.busy, onClick: () => decide([{ id: p.id }]) }, 'Accepter'),
            el('button', { class: 'btn btn-sm', type: 'button', disabled: s.busy, onClick: () => { s.editing = p.id; paint(); } }, 'Modifier'),
            el('button', { class: 'btn btn-sm btn-danger', type: 'button', disabled: s.busy, onClick: () => call(() => rejectProposals([p.id])) }, 'Refuser'))));
  }

  /* Traçabilité : un élément du profil fondé sur un message y ramène d'un clic. */
  function Traced(id, children) {
    const source = (s.view.provenance[id] || [])[0];
    if (!source) return el('span', {}, ...children);
    return el('button', {
      class: 'iv-traced', type: 'button', title: 'Voir le message d’origine',
      onClick: () => {
        const message = document.getElementById(`iv-${source}`);
        if (!message) return;
        message.scrollIntoView({ block: 'center', behavior: 'smooth' });
        message.classList.add('is-flash');
        setTimeout(() => message.classList.remove('is-flash'), 1600);
      },
    }, ...children);
  }

  /* Le profil tel qu'il est maintenant, en bref : ce que l'entretien a déjà construit. */
  function LiveProfile(v) {
    const base = v.profile;
    if (!base) return null;
    const experiences = base.experiences || [];
    return el('section', { class: 'iv-live', 'aria-labelledby': 'iv-live-title' },
      el('h3', { class: 'section-title', id: 'iv-live-title' }, 'Votre profil, en direct'),
      el('ul', { class: 'iv-live-list' },
        ...experiences.map(e => el('li', {}, Traced(e.id, [el('strong', {}, e.title), ` — ${e.organization}`]),
          el('span', { class: 'field-hint' }, ` · ${e.achievements.length} réalisation(s)`),
          el('ul', {}, ...e.achievements.map(a => el('li', {}, Traced(a.id, [a.text]))))))),
      (base.hard_skills || []).length > 0 && el('p', { class: 'field-hint' }, 'Compétences : ', base.hard_skills.join(', ')),
      v.context_notes.length > 0 && el('details', {},
        el('summary', {}, 'Ce que l’IA a retenu (non ajouté au profil)'),
        el('ul', {}, ...v.context_notes.map(n => el('li', {}, n)))));
  }

  function paintSide() {
    const v = s.view;
    if (!v) { fill(side); return; }
    const pending = v.proposals.filter(p => p.status === 'pending');
    for (const id of [...s.selected]) if (!pending.some(p => p.id === id && !p.blocking)) s.selected.delete(id);
    const selected = [...s.selected];
    fill(side,
      el('div', { class: 'iv-side-head' },
        el('h3', { class: 'section-title' }, 'Propositions', el('span', { class: 'list-sub num' }, `${pending.length} en attente`)),
        pending.length > 1 && el('div', { class: 'iv-actions' },
          el('button', { class: 'btn-link', type: 'button', onClick: () => { pending.filter(p => !p.blocking).forEach(p => s.selected.add(p.id)); paintSide(); } }, 'Tout sélectionner'),
          selected.length > 0 && el('button', { class: 'btn btn-profil btn-sm', type: 'button', disabled: s.busy,
            onClick: async () => { if (await decide(selected.map(id => ({ id })))) s.selected.clear(); paint(); } }, `Accepter (${selected.length})`),
          selected.length > 0 && el('button', { class: 'btn btn-sm btn-danger', type: 'button', disabled: s.busy,
            onClick: async () => { if (await call(() => rejectProposals(selected))) s.selected.clear(); paint(); } }, `Refuser (${selected.length})`))),
      pending.length
        ? el('ul', { class: 'iv-props' }, ...pending.map(Proposal))
        : el('p', { class: 'field-hint' }, 'Aucune proposition en attente. Répondez aux questions : l’IA proposera des éléments à ajouter.'),
      LiveProfile(v));
  }

  function paint() { paintHead(); paintLog(); paintSide(); }

  const content = el('div', { class: 'iv' },
    head,
    el('div', { class: 'iv-grid' },
      el('section', { class: 'iv-chat', 'aria-label': 'Conversation avec l’IA' },
        log,
        el('div', { class: 'iv-composer' },
          el('label', { class: 'visually-hidden', for: inputId }, 'Votre réponse'),
          input, sendButton)),
      el('section', { class: 'iv-proposals', 'aria-label': 'Propositions' }, side)));

  openDialog({
    title: 'Entretien IA',
    wide: true,
    className: 'dialog-interview',
    content: [
      el('p', { class: 'field-hint' }, 'L’IA vous pose des questions et propose des éléments. Rien n’est ajouté à votre profil sans votre accord.'),
      content,
    ],
    onClose: () => loadProfile(),
  });
  paint();
  call(getInterview).then(() => input.focus());
}
