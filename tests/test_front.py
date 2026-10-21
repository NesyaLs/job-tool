"""Câblage du front (Interface/js/) — lu À TRAVERS L'API, sans navigateur.

Pas de moteur JavaScript dans la suite : ces tests prouvent des CÂBLAGES (« tout appel
passe par le client qui pose le jeton », « aucun style en ligne ») sur le code réellement
servi. Le rendu, lui, se vérifie à l'œil (captures comparées à la maquette,
docs/design/maquette_reference_carnet.html).

Les modules sont DÉCOUVERTS, pas énumérés : un module ajouté demain est couvert sans que
personne ait à y penser. Les lire par l'API vérifie du même coup qu'ils sont servis.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from jobtool.api.main import FRONT_DIR, app

JS_DIR = FRONT_DIR / "js"
CSS_DIR = FRONT_DIR / "css"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _served(client: TestClient, directory, pattern: str) -> dict[str, str]:
    files = {}
    for path in sorted(directory.rglob(pattern)):
        url = "/" + path.relative_to(FRONT_DIR).as_posix()
        response = client.get(url)
        assert response.status_code == 200, f"fichier du front non servi : {url}"
        files[url] = response.text
    assert files, f"aucun fichier {pattern} sous {directory}"
    return files


@pytest.fixture(scope="module")
def modules(client) -> dict[str, str]:
    return _served(client, JS_DIR, "*.js")


@pytest.fixture(scope="module")
def styles(client) -> dict[str, str]:
    return _served(client, CSS_DIR, "*.css")


# --- Point d'entrée ---------------------------------------------------------


def test_index_loads_the_module_entry_point(client):
    html = client.get("/").text
    assert '<script type="module" src="js/app.js">' in html
    assert '<div id="app">' in html


def test_every_import_resolves_to_a_served_module(client, modules):
    """Un import cassé ne se voit que dans le navigateur : on le vérifie ici."""
    for url, source in modules.items():
        base = url.rsplit("/", 1)[0]
        for target in re.findall(r"from '(\.{1,2}/[^']+)'", source):
            parts = (base + "/" + target).split("/")
            resolved: list[str] = []
            for part in parts:
                if part == "..":
                    resolved.pop()
                elif part not in (".", ""):
                    resolved.append(part)
            path = "/" + "/".join(resolved)
            assert path in modules, f"{url} importe {target}, introuvable ({path})"


# --- Réseau : un seul client, qui pose le jeton ------------------------------


def test_only_the_api_client_calls_fetch(modules):
    """Un `fetch` hors du client partirait sans jeton, donc en 401 en multi-comptes."""
    for url, source in modules.items():
        if url == "/js/api/client.js":
            continue
        assert "fetch(" not in source.replace("Fetch(", ""), f"fetch direct dans {url}"


def test_api_client_sends_the_token_and_handles_expiry(modules):
    source = modules["/js/api/client.js"]
    assert "finalHeaders.Authorization = 'Bearer ' + session.token" in source
    assert "response.status === 401" in source, "un jeton expiré ramène à la connexion"
    direct = [
        line.strip() for line in source.splitlines() if re.search(r"\bfetch\(", line)
    ]
    # Notre API (un seul point d'envoi) et Supabase (autre hôte, pour la connexion).
    assert len(direct) == 2, direct


# --- Règles de code ---------------------------------------------------------


def test_no_inline_style_and_no_inner_html(modules):
    for url, source in modules.items():
        assert not re.search(r"\.innerHTML\b", source), f"innerHTML dans {url}"
        assert "style:" not in source and ".style.cssText" not in source, (
            f"style en ligne dans {url}"
        )


def test_colors_and_fonts_live_in_the_tokens_file(styles):
    """Un seul fichier de variables : aucune couleur ni police écrite ailleurs."""
    assert "--score-bon" in styles["/css/tokens.css"]
    assert "--accent" in styles["/css/tokens.css"]
    for url, css in styles.items():
        if url == "/css/tokens.css":
            continue
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), f"couleur en dur dans {url}"
        assert "rgb(" not in css and "rgba(" not in css, f"couleur en dur dans {url}"
        assert "font-family:" not in css.replace("font-family: var(", ""), (
            f"police en dur dans {url}"
        )


def test_fonts_are_self_hosted(client, styles):
    """Mono-poste hors ligne : les polices sont servies par l'application elle-même."""
    tokens = styles["/css/tokens.css"]
    for font in ("fraunces-latin-wght.woff2", "inter-latin-wght.woff2"):
        assert font in tokens
        assert client.get("/fonts/" + font).status_code == 200
    assert "fonts.googleapis.com" not in client.get("/").text


def test_logo_and_favicons_are_served(client):
    html = client.get("/").text
    for path in (
        "assets/logo/favicon.svg",
        "assets/logo/favicon.ico",
        "assets/logo/apple-touch-icon.png",
    ):
        assert path in html
        assert client.get("/" + path).status_code == 200
    # L'interface ne montre que le symbole, sans le nom « jobtool ».
    assert client.get("/assets/logo/jobtool-symbole-couleur.svg").status_code == 200


# --- Espace Offres ----------------------------------------------------------


def test_folders_are_read_from_the_server_ranked_list(modules):
    """Filtres et tri partent au serveur : aucun tri ni filtrage de la liste en JS."""
    data = modules["/js/offres/offres-data.js"]
    assert "rankOffers(" in data and "'/offers/ranked'" in modules["/js/api/client.js"]
    for folder, status in (
        ("nouvelles", "new"),
        ("favoris", "new"),
        ("candidatures", "applied"),
        ("masquees", "seen"),
    ):
        assert folder in data and f"status: '{status}'" in data
    assert "X-Total-Count" in modules["/js/api/client.js"], "total lu dans l'en-tête"
    for url, source in modules.items():
        assert ".sort(" not in source, f"tri côté client dans {url}"


def test_every_gesture_can_be_undone_with_a_forced_value(modules):
    """Favori, masquer, postulée : valeur FORCÉE, et l'inverse proposé par « Annuler »."""
    actions = modules["/js/offres/offer-actions.js"]
    assert "setOfferFavorite(offer.id, target)" in actions
    assert "setOfferFavorite(offer.id, !target)" in actions
    assert "setOfferStatus(offer.id, hiding ? 'seen' : 'new')" in actions
    assert "setOfferStatus(offer.id, 'applied')" in actions
    assert "toastUndo(" in actions
    client = modules["/js/api/client.js"]
    assert "body: { favorite }" in client, "le favori est forcé, jamais basculé"


def test_keyboard_shortcuts_are_wired(modules):
    view = modules["/js/offres/offres-view.js"]
    for key in ("'j'", "'k'", "'f'", "'h'", "'p'", "'escape'"):
        assert f"bindKey({key}" in view, key
    keys = modules["/js/core/keys.js"]
    assert "isTyping" in keys, "une lettre tapée dans un champ n'est pas un raccourci"


def test_external_links_are_safe_and_made_in_one_place(modules):
    joined = "\n".join(modules.values())
    assert joined.count("rel: 'noopener noreferrer'") == 1
    assert (
        "target: '_blank', rel: 'noopener noreferrer'"
        in modules["/js/offres/offer-format.js"]
    )


def test_score_colours_follow_the_design_thresholds(modules, styles):
    fmt = modules["/js/offres/offer-format.js"]
    assert "score >= 75" in fmt and "score >= 60" in fmt
    css = styles["/css/components.css"]
    for level in ("bon", "moyen", "faible"):
        assert f'[data-level="{level}"] {{ color: var(--score-{level}); }}' in css


# --- Tiroir Postuler --------------------------------------------------------


def test_generation_happens_only_on_click(modules):
    """Ouvrir le tiroir ne génère rien : seul le bouton « Générer » appelle POST /documents."""
    session = modules["/js/postuler/document-session.js"]
    assert "openDocument(body)" in session
    assert "force_new: forceNew" in session, "reprise du brouillon par défaut"
    panel = modules["/js/postuler/document-panel.js"]
    assert "onClick: () => session.generate()" in panel
    drawer = modules["/js/postuler/drawer.js"]
    assert "generate(" not in drawer, (
        "le tiroir ne lance aucune génération à l'ouverture"
    )


def test_regeneration_is_confirmed_and_stale_responses_are_ignored(modules):
    panel = modules["/js/postuler/document-panel.js"]
    assert "confirmAction(" in panel and "generate({ forceNew: true })" in panel
    session = modules["/js/postuler/document-session.js"]
    assert "if (mine === token) applyView(view)" in session


def test_preview_is_png_and_pdf_is_downloaded_with_the_token(modules):
    preview = modules["/js/postuler/preview.js"]
    assert (
        "view.preview_urls[pageNumber - 1]" in preview and "class: 'pdf-img'" in preview
    )
    session = modules["/js/postuler/document-session.js"]
    assert "fetchArtifact(" in session and "URL.createObjectURL" in session
    assert "URL.revokeObjectURL" in session, "les aperçus périmés sont libérés"
    panel = modules["/js/postuler/document-panel.js"]
    assert "session.artifactUrl(s.view.pdf_url)" in panel and "download:" in panel


def test_retouche_advanced_mode_and_letter_template_switch(modules):
    session = modules["/js/postuler/document-session.js"]
    for call in (
        "editDocumentFields(",
        "resetDocumentFields(",
        "getDocumentSource(",
        "setDocumentSource(",
        "resetDocumentSource(",
        "setDocumentTemplate(",
    ):
        assert call in session, call
    assert "interactive ? view.fields" in modules["/js/postuler/preview.js"]


def test_templates_and_options_come_from_the_server(modules):
    templates = modules["/js/postuler/templates.js"]
    assert "listTemplates(kind)" in templates and "resolveOptions" in templates
    joined = "\n".join(modules.values())
    for name in ("'basic'", "'compact'", "'metronic'", "'moderne'", "'sobre'"):
        assert name not in joined, f"template codé en dur : {name}"


def test_applying_is_an_explicit_click(modules):
    drawer = modules["/js/postuler/drawer.js"]
    assert "markApplied(offer)" in drawer and "J’ai postulé" in drawer
    for url, source in modules.items():
        if url in ("/js/postuler/drawer.js", "/js/offres/offer-actions.js"):
            continue
        assert "markApplied(" not in source, f"candidature enregistrée ailleurs : {url}"


# --- Espace Profil ----------------------------------------------------------


def test_profile_is_saved_automatically_with_undo(modules):
    data = modules["/js/profil/profile-data.js"]
    assert "saveProfile(toDto(state.profile))" in data
    assert "SAVE_DELAY_MS" in data and "undoLastSave" in data
    assert "'/profile', { method: 'PUT', body }" in modules["/js/api/client.js"]
    status = modules["/js/profil/save-status.js"]
    assert "role: 'status'" in status and "Annuler" in status


def test_photo_is_sent_as_form_data_and_read_with_the_token(modules):
    client = modules["/js/api/client.js"]
    assert "new FormData()" in client and "body.append('fichier', file)" in client
    assert "requestBlob('/profile/photo?v=' + version)" in client, (
        "aperçu versionné, récupéré avec le jeton (un <img src> n'emporte pas l'en-tête)"
    )
    identity = modules["/js/profil/identity.js"]
    assert "state.profile.a_photo = !!dto.a_photo" in identity
    assert "deletePhoto()" in identity


def test_interview_proposals_are_accepted_edited_or_rejected(modules):
    panel = modules["/js/interview/interview-panel.js"]
    for call in (
        "getInterview",
        "sendInterviewMessage(text)",
        "acceptProposals(decisions)",
        "rejectProposals(",
        "restartInterview",
        "gotoInterviewSection(",
        "skipInterviewSection(",
    ):
        assert call in panel, call
    assert "{ id: p.id, payload: d }" in panel, (
        "une proposition modifiée part avec sa version"
    )
    assert "onClose: () => loadProfile()" in panel, (
        "le profil est relu après l'entretien"
    )


def test_admin_profile_switch_is_wired(modules):
    switcher = modules["/js/profil/profiles-switcher.js"]
    for call in (
        "listProfiles()",
        "activateProfile(key)",
        "createProfile(",
        "renameProfile(",
        "deleteProfile(",
    ):
        assert call in switcher, call
    assert "if (!p.allowed)" in switcher, "rien pour un compte sans droits"


def test_profile_export_downloads_server_json(modules):
    data = modules["/js/profil/profile-data.js"]
    assert "await flushSave();\n  const dto = await getProfile();" in data, (
        "l'export enregistre la saisie en cours puis relit la version du serveur"
    )
    assert "type: 'application/json'" in data
    view = modules["/js/profil/profil-view.js"]
    assert (
        "onClick: onExport" in view
        and "registerSidebarFooter('profil', SidebarFooter)" in view
    )


# --- Découvrir, jauge, pont vers le profil ------------------------------------


def test_discover_page_reuses_the_account_state(modules, styles):
    """Garder = favori forcé, masquer = statut `seen` ; gestes souris et tactile, flèches."""
    triage = modules["/js/offres/discover.js"]
    assert (
        "stackOffers(" in triage and "'/offers/stack'" in modules["/js/api/client.js"]
    )
    assert "setOfferFavorite(card.id, !undo)" in triage
    assert "setOfferStatus(card.id, undo ? 'new' : 'seen')" in triage
    for event in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
        assert event in triage, f"geste souris ET tactile : {event}"
    assert "bindKey('arrowright'" in triage and "bindKey('arrowleft'" in triage
    assert "'decouvrir'" in modules["/js/core/router.js"]
    assert "id: 'decouvrir'" in modules["/js/shell/sidebar.js"]
    assert "mountDiscover(main)" in modules["/js/offres/offres-view.js"]
    assert "prefers-reduced-motion" in styles["/css/offres.css"]


def test_profile_gauge_counts_sections_and_numbers(modules):
    gauge = modules["/js/profil/completeness.js"]
    for section in ("'identite'", "'experiences'", "'competences'", "'langues'"):
        assert section in gauge
    assert "filter(hasNumber)" in gauge, "part des expériences avec un chiffre"
    assert "hrefOf('profil', weakest)" in gauge, (
        "le lien ouvre la section la moins remplie"
    )


def test_skill_bridge_opens_the_skills_section(modules):
    bridge = modules["/js/profil/skill-bridge.js"]
    assert "navigate('profil', 'competences')" in bridge
    assert "takeSkillPrefill()" in modules["/js/profil/skills.js"]
    assert "registerDetailExtra(SkillBridge)" in modules["/js/profil/profil-view.js"]


# --- Menu de l'avatar, administration, connexion -----------------------------


def test_admin_entries_are_shown_to_admins_only(modules):
    menu = modules["/js/shell/avatar-menu.js"]
    assert "state.me.isAdmin && Item('Administration'" in menu
    assert "state.me.isAdmin && Item('Journal LLM'" in menu
    assert "session.token && Item('Se déconnecter'" in menu, (
        "déconnexion en multi-comptes"
    )
    assert "fetchMe()" in modules["/js/app.js"]
    views = modules["/js/admin/admin-views.js"]
    assert (
        "adminOverview()" in views and "listLlmRuns(" in views and "getLlmRun(" in views
    )
    assert "if (!state.me.isAdmin)" in views


def test_collection_runs_in_the_background_without_a_prominent_button(modules):
    collect = modules["/js/shell/collect.js"]
    assert "AUTO_COLLECT_AFTER_HOURS = 6" in collect
    assert "response.status === 409" in collect and "response.status === 429" in collect
    assert "getCollection(runId)" in collect, "suivi de la collecte en cours"
    assert "Collecter les offres maintenant" in modules["/js/shell/avatar-menu.js"]


def test_login_page_talks_to_supabase_only(modules):
    login = modules["/js/auth/login.js"]
    assert "supabaseAuth(mode," in login and "storeSession(body.access_token" in login
    assert "type: 'password'" in login and "autocomplete" in login


# --- Reprises des garanties de l'ancien front ---------------------------------


def test_dates_are_french_and_never_nan(modules):
    fmt = modules["/js/core/format.js"]
    assert "Number.isNaN(" in fmt, "une date illisible est détectée avant tout calcul"
    assert "'date inconnue'" in fmt
    assert "toLocaleDateString('fr-FR'" in fmt


def test_search_and_filters_go_to_the_server(modules):
    data = modules["/js/offres/offres-data.js"]
    assert "params.q = state.query" in data, "la recherche filtre côté serveur"
    assert "params.max_age_days = f.maxAgeDays" in data, "fraîcheur envoyée à l'API"
    assert "params.departments = f.departments.join(',')" in data
    assert "params.contract = f.contracts" in data
    assert "limit: PAGE_SIZE, offset:" in data, "pagination demandée au serveur"


def test_manual_offer_and_description_are_wired(modules):
    manual = modules["/js/offres/manual-offer.js"]
    assert "createOffer({" in manual and "err.status === 409" in manual
    assert "'/offers', { method: 'POST', body }" in modules["/js/api/client.js"]
    actions = modules["/js/offres/offer-actions.js"]
    assert "setOfferDescription(offer.id, text)" in actions
    assert "setOfferDescription(offer.id, previous)" in actions, (
        "la correction s'annule"
    )


# --- Itération 2 ---------------------------------------------------------------


def test_search_bar_only_in_offres_space(modules, styles):
    """Hors d'Offres, la recherche disparaît et le même créneau porte le nom de l'espace."""
    topbar = modules["/js/shell/topbar.js"]
    assert "class: 'topbar-slot'" in topbar and "class: 'topbar-title'" in topbar
    assert "subscribe('route', paintTitle)" in topbar
    layout = styles["/css/layout.css"]
    assert '.app:not([data-space="offres"]) .search { display: none; }' in layout


def test_offer_detail_blocks_share_one_reading_measure(modules, styles):
    """Les blocs du détail prennent la largeur du panneau jusqu'à une mesure commune."""
    assert "class: 'pane-detail offer-detail'" in modules["/js/offres/offer-detail.js"]
    offres = styles["/css/offres.css"]
    assert (
        ".offer-detail > * { width: 100%; max-width: var(--detail-measure); }" in offres
    )
    assert "64ch" not in offres, "plus de mesure étroite propre à un bloc"
    assert "--detail-measure:" in styles["/css/tokens.css"]


def test_logo_is_centred_over_the_sidebar(styles):
    layout = styles["/css/layout.css"]
    logo = layout.split(".logo {", 1)[1].split("}", 1)[0]
    assert "width: var(--sidebar-w);" in logo and "justify-content: center;" in logo
    assert "margin-left: calc(-1 * var(--topbar-pad));" in logo


def test_bullet_suggestions_come_without_a_conversation(modules):
    """« Proposer avec l'IA » appelle la route des propositions, pas l'entretien."""
    box = modules["/js/profil/bullet-suggestions.js"]
    assert "suggestBullets(" in box and "openInterview" not in box
    assert "'/profile/suggestions'" in modules["/js/api/client.js"]
    editor = modules["/js/profil/experiences.js"]
    assert "SuggestButton(" in editor and "SuggestionsBox(" in editor
    # L'éditeur ne montre plus les compétences de chaque puce.
    assert "Compétences liées" not in editor


def test_default_templates_live_in_the_profile(modules):
    """Le template par défaut est lu dans le profil (le serveur en a besoin pour générer
    en arrière-plan), plus dans le stockage du navigateur."""
    templates = modules["/js/postuler/templates.js"]
    assert "state.profile" in templates and "template_cv" in templates
    gallery = modules["/js/profil/template-gallery.js"]
    assert "editProfile(" in gallery and "setDefaultTemplate" not in gallery
    data = modules["/js/profil/profile-data.js"]
    for key in ("template_cv", "options_cv", "template_lettre", "lettre_exemple"):
        assert key in data, f"{key} perdu à la normalisation du profil"


def test_documents_are_generated_in_the_background(modules):
    """« Générer le CV et la lettre » lance une tâche serveur, suivie par sondage, avec un
    message à la fin ; les documents prêts s'affichent dans le détail de l'offre."""
    client = modules["/js/api/client.js"]
    assert "'/documents/jobs'" in client and "/documents/offer/" in client
    background = modules["/js/postuler/background.js"]
    assert "startDocumentJob(" in background and "listDocumentJobs()" in background
    assert "setTimeout(poll" in background and "Notification" in background
    card = modules["/js/postuler/offer-documents.js"]
    assert "Générer le CV et la lettre" in card and "offer.favorite" in card
    assert "Télécharger" in card and "Modifier" in card
    drawer = modules["/js/postuler/drawer.js"]
    assert "openReadyDocument" in drawer and "registerDetailExtra(" in drawer


def test_letter_example_is_edited_in_the_profile(modules):
    gallery = modules["/js/profil/template-gallery.js"]
    assert "lettre_exemple" in gallery and "Votre lettre type" in gallery


def test_api_keys_are_typed_masked_and_never_read_back(modules):
    """Réglages > Clés d'API : saisie masquée, et le front ne reçoit que la provenance."""
    settings = modules["/js/shell/settings.js"]
    client = modules["/js/api/client.js"]
    assert "listApiKeys" in settings and "saveApiKey" in settings and "deleteApiKey" in settings
    assert "type: 'password'" in settings and "autocomplete: 'off'" in settings
    assert "/settings/api-keys" in client
