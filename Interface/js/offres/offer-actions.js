/* Gestes sur une offre : favori, masquer, postulée, description.

   Chaque geste écrit une valeur FORCÉE (jamais une bascule côté serveur) : l'annuler
   revient à écrire la valeur inverse, sans dépendre de l'état du moment. Le message
   « Annuler » reste quelques secondes. */

import { setOfferDescription, setOfferFavorite, setOfferStatus } from '../api/client.js';
import { toast, toastError, toastUndo } from '../core/toast.js';
import { applyUpdate, refreshCounts, restoreItem } from './offres-data.js';

const pending = new Set();

/* Exécute l'écriture, applique la réponse, propose d'annuler. `undoWrite` rejoue l'état
   d'origine ; en cas d'échec, rien n'a bougé localement. */
async function perform(offer, { write, undoWrite, message, undoneMessage, failure }) {
  if (pending.has(offer.id)) return;
  pending.add(offer.id);
  const before = { ...offer };
  try {
    const updated = await write();
    const index = applyUpdate(updated);
    refreshCounts();
    toastUndo(message, async () => {
      try {
        const restored = await undoWrite();
        restoreItem({ ...before, ...restored }, index);
        refreshCounts();
        toast(undoneMessage);
      } catch (err) {
        toastError('Impossible d’annuler : ' + err.message);
      }
    });
  } catch (err) {
    toastError(`${failure} ${err.message}`);
  } finally {
    pending.delete(offer.id);
  }
}

export function toggleFavorite(offer) {
  const target = !offer.favorite;
  return perform(offer, {
    write: () => setOfferFavorite(offer.id, target),
    undoWrite: () => setOfferFavorite(offer.id, !target),
    message: target ? 'Ajoutée aux favoris.' : 'Retirée des favoris.',
    undoneMessage: 'Favori rétabli comme avant.',
    failure: 'Favori non enregistré :',
  });
}

/* Masquer une offre nouvelle, ou remettre une offre masquée. Une candidature ne se masque
   pas : changer son statut effacerait sa date de candidature côté serveur. */
export function canToggleHide(offer) {
  return offer.statut === 'new' || offer.statut === 'seen';
}

export function toggleHide(offer) {
  if (!canToggleHide(offer)) return Promise.resolve();
  const hiding = offer.statut !== 'seen';
  return perform(offer, {
    write: () => setOfferStatus(offer.id, hiding ? 'seen' : 'new'),
    undoWrite: () => setOfferStatus(offer.id, hiding ? 'new' : 'seen'),
    message: hiding ? 'Offre masquée.' : 'Offre remise dans les nouvelles.',
    undoneMessage: hiding ? 'Offre de retour.' : 'Offre masquée à nouveau.',
    failure: hiding ? 'Offre non masquée :' : 'Offre non remise :',
  });
}

/* « J'ai postulé » : l'offre part dans Candidatures, datée par le serveur. L'annulation la
   rend à son statut d'avant (la date de candidature est alors effacée). */
export function markApplied(offer) {
  const previous = offer.statut;
  return perform(offer, {
    write: () => setOfferStatus(offer.id, 'applied'),
    undoWrite: () => setOfferStatus(offer.id, previous),
    message: 'Candidature enregistrée, rangée dans Candidatures.',
    undoneMessage: 'Candidature annulée.',
    failure: 'Candidature non enregistrée :',
  });
}

/* Description corrigée à la main (source partielle, complétée depuis l'annonce). */
export async function saveDescription(offer, text) {
  if (text === offer.description) return true;
  const previous = offer.description;
  try {
    applyUpdate(await setOfferDescription(offer.id, text));
    toastUndo('Description enregistrée.', async () => {
      try {
        applyUpdate(await setOfferDescription(offer.id, previous));
      } catch (err) {
        toastError('Impossible d’annuler : ' + err.message);
      }
    });
    return true;
  } catch (err) {
    toastError('Description non enregistrée : ' + err.message);
    return false;
  }
}
