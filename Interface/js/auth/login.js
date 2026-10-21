/* Page de connexion (mode multi-comptes seulement). Seule à l'écran : tant qu'on n'est pas
   connecté, il n'y a rien d'autre à faire. Le mot de passe part directement à Supabase ;
   notre API ne le voit jamais. */

import { session, storeSession, supabaseAuth } from '../api/client.js';
import { el, uid } from '../core/dom.js';

export function mountLogin(root, { message = '', onSignedIn }) {
  let mode = 'signin';

  function field(label, input) {
    const id = uid('f');
    input.id = id;
    return el('div', { class: 'field' }, el('label', { class: 'field-label', for: id }, label), input);
  }

  function paint(note = '', noteKind = 'info', email = session.email || '') {
    const mail = el('input', { class: 'input', type: 'email', required: true, autocomplete: 'email', value: email });
    const pass = el('input', {
      class: 'input', type: 'password', required: true, minlength: '6',
      autocomplete: mode === 'signup' ? 'new-password' : 'current-password',
    });
    const submit = el('button', { class: 'btn btn-primary login-submit', type: 'submit' },
      mode === 'signup' ? 'Créer le compte' : 'Se connecter');

    const form = el('form', {
      class: 'login-card', 'aria-labelledby': 'login-title',
      onSubmit: async (event) => {
        event.preventDefault();
        submit.disabled = true;
        submit.textContent = mode === 'signup' ? 'Création…' : 'Connexion…';
        try {
          const body = await supabaseAuth(mode, mail.value.trim(), pass.value);
          if (!body.access_token) {
            // Inscription avec confirmation d'e-mail : pas de jeton tout de suite.
            mode = 'signin';
            paint('Compte créé. Confirmez votre adresse e-mail, puis connectez-vous.', 'info', mail.value);
            return;
          }
          storeSession(body.access_token, mail.value.trim());
          onSignedIn();
        } catch (err) {
          paint(err.message || 'Connexion impossible.', 'error', mail.value);
        }
      },
    },
      el('img', { class: 'login-logo', src: 'assets/logo/jobtool-symbole-couleur.svg', alt: '', width: '40', height: '40' }),
      el('h1', { class: 'login-title', id: 'login-title' }, mode === 'signup' ? 'Créer un compte' : 'Connexion'),
      el('p', { class: 'login-sub' }, 'Vos offres, vos candidatures et vos documents, au même endroit.'),
      note && el('p', { class: 'login-note', role: noteKind === 'error' ? 'alert' : 'status', dataset: { kind: noteKind } }, note),
      field('Adresse e-mail', mail),
      field('Mot de passe', pass),
      submit,
      el('button', {
        class: 'btn-link login-switch', type: 'button',
        onClick: () => { mode = mode === 'signup' ? 'signin' : 'signup'; paint('', 'info', mail.value); },
      }, mode === 'signup' ? 'J’ai déjà un compte' : 'Créer un compte'),
    );
    root.replaceChildren(el('main', { class: 'login-page' }, form));
    (email ? pass : mail).focus();
  }

  root.className = 'login-root';
  paint(message, message ? 'error' : 'info');
}
