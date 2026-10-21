/* CLIENT DE L'API — le seul module qui parle au réseau.

   Le navigateur s'authentifie DIRECTEMENT auprès de Supabase et reçoit un jeton ; ce jeton
   accompagne chaque appel à notre API, qui en vérifie la signature. Notre API ne voit
   jamais de mot de passe. En mono-poste, il n'y a ni jeton ni connexion.

   Tout appel passe par `request()`, qui pose le jeton et traite le 401 comme une fin de
   session. Les écrans n'appellent jamais `fetch` : ils appellent les fonctions nommées
   en bas de ce fichier. */

// Clés historiques (texte brut) : une session ouverte avec l'ancienne interface survit.
const TOKEN_KEY = 'jobtool.token';
const EMAIL_KEY = 'jobtool.email';

export const session = { token: null, email: null, config: null };

let onSessionExpired = () => {};

/* Branché par app.js : que faire quand le serveur refuse le jeton (retour à la connexion). */
export function setSessionExpiredHandler(fn) {
  onSessionExpired = fn;
}

export function loadStoredSession() {
  try {
    session.token = localStorage.getItem(TOKEN_KEY);
    session.email = localStorage.getItem(EMAIL_KEY);
  } catch (err) {
    /* navigation privée : session vide */
  }
}

export function storeSession(token, email) {
  session.token = token;
  session.email = email;
  try {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(EMAIL_KEY, email || '');
    } else {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(EMAIL_KEY);
    }
  } catch (err) {
    /* stockage indisponible : la session durera le temps de l'onglet */
  }
}

/* Erreur d'API : garde le statut HTTP et le détail renvoyé par le serveur. */
class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function messageOf(body, status) {
  const detail = body && (body.detail ?? body.error);
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail.message === 'string') return detail.message;
  if (status === 0) return 'Serveur injoignable. Vérifiez votre connexion.';
  return `Erreur du serveur (HTTP ${status}).`;
}

const DEFAULT_TIMEOUT_MS = 30000;
/* Une génération de CV ou de lettre prend souvent plus d'une minute. */
const GENERATION_TIMEOUT_MS = 180000;

/* Appel brut : renvoie la Response. Lève ApiError si le réseau échoue ou si la session a
   expiré. Le délai est borné : aucune attente infinie sur un serveur qui ne répond plus. */
async function send(path, { method = 'GET', body, headers = {}, timeout = DEFAULT_TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const finalHeaders = { ...headers };
  if (session.token) finalHeaders.Authorization = 'Bearer ' + session.token;
  let payload = body;
  if (body !== undefined && !(body instanceof FormData)) {
    finalHeaders['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, { method, headers: finalHeaders, body: payload, signal: controller.signal });
  } catch (err) {
    if (err.name === 'AbortError') throw new ApiError('Le serveur met trop de temps à répondre.', 0);
    throw new ApiError(messageOf(null, 0), 0);
  } finally {
    clearTimeout(timer);
  }
  if (response.status === 401 && session.config && session.config.auth_required) {
    storeSession(null, null);
    onSessionExpired('Session expirée : reconnectez-vous.');
    throw new ApiError('Session expirée.', 401);
  }
  return response;
}

/* Appel JSON : renvoie le corps, et le total `X-Total-Count` quand la route le fournit. */
async function request(path, options = {}) {
  const response = await send(path, options);
  let body = null;
  try { body = await response.json(); } catch (err) { /* corps vide */ }
  if (!response.ok) throw new ApiError(messageOf(body, response.status), response.status, body && body.detail);
  return body;
}

async function requestPage(path, options = {}) {
  const response = await send(path, options);
  let body = null;
  try { body = await response.json(); } catch (err) { /* corps vide */ }
  if (!response.ok) throw new ApiError(messageOf(body, response.status), response.status, body && body.detail);
  const total = Number(response.headers.get('X-Total-Count'));
  return { items: body || [], total: Number.isFinite(total) ? total : (body || []).length };
}

/* Octets d'un artefact protégé (aperçu PNG, PDF, photo). Un `<img src>` chargé par le
   navigateur n'emporte pas le jeton : on récupère les octets ici, puis l'appelant les
   expose par une URL `blob:`. */
async function requestBlob(path) {
  const response = await send(path);
  if (!response.ok) throw new ApiError(`Fichier indisponible (HTTP ${response.status}).`, response.status);
  return response.blob();
}

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value == null || value === '' || value === false) continue;
    if (Array.isArray(value)) value.forEach(v => search.append(key, v));
    else search.set(key, String(value));
  }
  const text = search.toString();
  return text ? '?' + text : '';
}

const enc = encodeURIComponent;

/* ---------- Authentification ---------- */

/* Configuration publique : faut-il se connecter ? Aucun secret n'y figure. */
export async function loadAuthConfig() {
  try {
    session.config = await request('/auth/config');
  } catch (err) {
    session.config = { auth_required: false, supabase_url: '', publishable_key: '' };
  }
  return session.config;
}

export const fetchMe = () => request('/auth/me');

/* Le SEUL appel vers un autre hôte que le nôtre, et le seul qui voit un mot de passe. */
export async function supabaseAuth(action, email, password) {
  const cfg = session.config || {};
  if (!cfg.supabase_url || !cfg.publishable_key) {
    throw new ApiError('Authentification non configurée côté serveur.', 0);
  }
  const path = action === 'signup' ? '/auth/v1/signup' : '/auth/v1/token?grant_type=password';
  let response;
  try {
    response = await fetch(cfg.supabase_url.replace(/\/$/, '') + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', apikey: cfg.publishable_key },
      body: JSON.stringify({ email, password }),
    });
  } catch (err) {
    throw new ApiError('Service de connexion injoignable.', 0);
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(body.error_description || body.msg || body.message || 'Échec de la connexion.', response.status);
  }
  return body;
}

/* ---------- Offres ---------- */

export const listOffers = (params) => requestPage('/offers' + query(params));
export const rankOffers = (params) => requestPage('/offers/ranked' + query(params));
export const stackOffers = (params) => requestPage('/offers/stack' + query(params));
export const createOffer = (body) => request('/offers', { method: 'POST', body });
export const setOfferStatus = (id, status) =>
  request(`/offers/${enc(id)}/status`, { method: 'PATCH', body: { status } });
export const setOfferFavorite = (id, favorite) =>
  request(`/offers/${enc(id)}/favorite`, { method: 'PATCH', body: { favorite } });
export const setOfferDescription = (id, description) =>
  request(`/offers/${enc(id)}/description`, { method: 'PATCH', body: { description } });

/* ---------- Collecte ---------- */

export const startCollection = () => send('/aggregate', { method: 'POST' });
export const latestCollection = () => request('/aggregate/latest');
export const getCollection = (id) => request(`/aggregate/${enc(id)}`);

/* ---------- Documents (CV, lettre) ---------- */

export const listTemplates = (kind) => request('/documents/templates' + query({ kind }));
export const openDocument = (body) =>
  request('/documents', { method: 'POST', body, timeout: GENERATION_TIMEOUT_MS });
/* Rouvre un document existant par son identifiant : recompilé, jamais régénéré. */
export const getDocument = (id) => request(`/documents/${enc(id)}`, { timeout: 60000 });
export const editDocumentFields = (id, edits) =>
  request(`/documents/${enc(id)}/fields`, { method: 'PATCH', body: { edits }, timeout: 60000 });
export const resetDocumentFields = (id, fieldIds) =>
  request(`/documents/${enc(id)}/reset`, { method: 'POST', body: { field_ids: fieldIds }, timeout: 60000 });
export const setDocumentTemplate = (id, template) =>
  request(`/documents/${enc(id)}/template`, { method: 'PUT', body: { template }, timeout: 60000 });
export const getDocumentSource = (id) => request(`/documents/${enc(id)}/source`, { timeout: 60000 });
export const setDocumentSource = (id, source) =>
  request(`/documents/${enc(id)}/source`, { method: 'PUT', body: { source }, timeout: 60000 });
export const resetDocumentSource = (id) =>
  request(`/documents/${enc(id)}/source/reset`, { method: 'POST', timeout: 60000 });
export const fetchArtifact = (path) => requestBlob(path);
/* Génération en arrière-plan : la requête rend la main aussitôt, on suit la tâche. */
export const startDocumentJob = (offerId, forceNew = false) =>
  request('/documents/jobs', { method: 'POST', body: { offer_id: offerId, force_new: forceNew } });
export const listDocumentJobs = () => request('/documents/jobs');
export const listOfferDocuments = (offerId) => request(`/documents/offer/${enc(offerId)}`, { timeout: 60000 });

/* ---------- Profil ---------- */

export const getProfile = () => request('/profile');
export const saveProfile = (body) => request('/profile', { method: 'PUT', body });
export const fetchPhoto = (version) => requestBlob('/profile/photo?v=' + version);
export function uploadPhoto(file) {
  const body = new FormData();
  body.append('fichier', file);
  return request('/profile/photo', { method: 'PUT', body });
}
export const deletePhoto = () => request('/profile/photo', { method: 'DELETE' });
/* Puces proposées pour une expérience (telle qu'elle est à l'écran) ; rien n'est écrit. */
export const suggestBullets = (experience, instruction) =>
  request('/profile/suggestions', { method: 'POST', body: { experience, instruction }, timeout: 90000 });

export const listProfiles = () => request('/profiles');
export const createProfile = (name, copyFrom) =>
  request('/profiles', { method: 'POST', body: { name, copy_from: copyFrom } });
export const renameProfile = (key, name) =>
  request(`/profiles/${enc(key)}`, { method: 'PATCH', body: { name } });
export const deleteProfile = (key) => request(`/profiles/${enc(key)}`, { method: 'DELETE' });
export const activateProfile = (key) => request('/profiles/active', { method: 'PUT', body: { key } });

/* ---------- Entretien guidé ---------- */

const INTERVIEW_TIMEOUT_MS = GENERATION_TIMEOUT_MS;   // chaque tour attend le modèle
export const getInterview = () => request('/interview');
export const sendInterviewMessage = (text) =>
  request('/interview/messages', { method: 'POST', body: { text }, timeout: INTERVIEW_TIMEOUT_MS });
export const restartInterview = () => request('/interview/restart', { method: 'POST' });
export const gotoInterviewSection = (section) =>
  request(`/interview/sections/${enc(section)}/goto`, { method: 'POST', timeout: INTERVIEW_TIMEOUT_MS });
export const skipInterviewSection = (section) =>
  request(`/interview/sections/${enc(section)}/skip`, { method: 'POST', timeout: INTERVIEW_TIMEOUT_MS });
export const acceptProposals = (decisions) =>
  request('/interview/proposals/accept', { method: 'POST', body: { decisions } });
export const rejectProposals = (ids) =>
  request('/interview/proposals/reject', { method: 'POST', body: { ids } });

/* ---------- Administration ---------- */

export const adminOverview = () => request('/admin/overview');
export const listLlmRuns = (limit = 20) => request('/admin/llm-runs' + query({ limit }));
export const getLlmRun = (id) => request(`/admin/llm-runs/${enc(id)}`);

/* ---------- Clés d'API du compte (jamais relues : seule leur présence revient) ---------- */

export const listApiKeys = () => request('/settings/api-keys');
export const saveApiKey = (name, value) =>
  request(`/settings/api-keys/${enc(name)}`, { method: 'PUT', body: { value } });
export const deleteApiKey = (name) => request(`/settings/api-keys/${enc(name)}`, { method: 'DELETE' });
