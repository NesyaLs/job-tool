/* Espace Offres : la liste à gauche, le détail à droite, comme une boîte de réception.
   Exception : Découvrir, une pile de cartes à glisser (voir discover.js).

   Raccourcis : J / K pour parcourir, F favori, H masquer, P postuler, Échap pour fermer
   le détail. Sur écran étroit, le détail passe par-dessus la liste. */

import { bindKey } from '../core/keys.js';
import { state, subscribe } from '../core/store.js';
import { onCollectionDone } from '../shell/collect.js';
import { registerSpace } from '../shell/views.js';
import { toggleFavorite, toggleHide } from './offer-actions.js';
import { mountDiscover } from './discover.js';
import { OfferDetail, applyTo } from './offer-detail.js';
import { OfferList } from './offer-list.js';
import { openFolder, refreshCounts, reloadFolder, select, selectRelative, selectedOffer } from './offres-data.js';

function mountOffres(main, folderId) {
  if (folderId === 'decouvrir') return mountDiscover(main);
  const detail = OfferDetail({ onBack: closeDetail });
  const list = OfferList(folderId, { onOpen: openDetail });

  function openDetail() {
    main.classList.add('has-detail');
    // Sur écran étroit, le détail recouvre la liste : le focus doit l'y suivre.
    if (window.matchMedia('(max-width: 899px)').matches) detail.focus();
  }

  function closeDetail() {
    const id = state.offres.selectedId;
    main.classList.remove('has-detail');
    if (window.matchMedia('(max-width: 899px)').matches || !id) select(null);
    list.querySelector(`.offer-row[data-id="${CSS.escape(id || '')}"]`)?.focus();
  }

  main.replaceChildren(list, detail);
  openFolder(folderId);

  const inOffres = () => state.route.space === 'offres';
  const withOffer = (action) => () => {
    const offer = selectedOffer();
    if (offer) action(offer);
  };
  const unbind = [
    bindKey('j', () => { if (selectRelative(1)) openDetail(); }, { when: inOffres }),
    bindKey('k', () => { if (selectRelative(-1)) openDetail(); }, { when: inOffres }),
    bindKey('f', withOffer(toggleFavorite), { when: inOffres }),
    bindKey('h', withOffer(toggleHide), { when: inOffres }),
    bindKey('p', withOffer(applyTo), { when: inOffres }),
    bindKey('escape', closeDetail, { when: () => inOffres() && !!state.offres.selectedId }),
  ];
  return () => unbind.forEach(off => off());
}

/* Inscription de l'espace et de ce qui doit vivre au-delà d'une vue : la recherche de la
   barre du haut et la fin d'une collecte relisent le dossier ouvert et les compteurs. */
export function initOffres() {
  registerSpace('offres', mountOffres);
  subscribe('query', () => { if (state.route.space === 'offres') reloadFolder(); });
  onCollectionDone(() => { reloadFolder(); refreshCounts(); });
  refreshCounts();
}
