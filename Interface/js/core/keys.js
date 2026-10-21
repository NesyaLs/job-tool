/* Raccourcis clavier : un seul écouteur pour toute l'application.

   Un raccourci se tait quand on écrit dans un champ (la lettre F doit s'écrire, pas mettre
   une offre en favori) et quand un dialogue modal est ouvert (il a ses propres touches).
   Chaque raccourci déclare dans quel contexte il vaut (`when`). */

const bindings = [];

function isTyping(target) {
  return target instanceof Element
    && !!target.closest('input, textarea, select, [contenteditable="true"]');
}

function modalOpen() {
  return !!document.querySelector('dialog[open]');
}

/* `keys` : une touche ou une liste (valeur de `event.key`, insensible à la casse pour les
   lettres). `when()` : vrai si le raccourci s'applique maintenant. Renvoie une fonction de
   désinscription. */
export function bindKey(keys, handler, { when = () => true, allowInModal = false } = {}) {
  const binding = { keys: [].concat(keys).map(k => k.toLowerCase()), handler, when, allowInModal };
  bindings.push(binding);
  return () => bindings.splice(bindings.indexOf(binding), 1);
}

document.addEventListener('keydown', (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key !== 'Escape' && isTyping(event.target)) return;
  const key = event.key.toLowerCase();
  // Les derniers inscrits d'abord : un écran monté par-dessus prend la main.
  for (let i = bindings.length - 1; i >= 0; i--) {
    const binding = bindings[i];
    if (!binding.keys.includes(key)) continue;
    if (modalOpen() && !binding.allowInModal) continue;
    if (!binding.when()) continue;
    event.preventDefault();
    binding.handler(event);
    return;
  }
});
