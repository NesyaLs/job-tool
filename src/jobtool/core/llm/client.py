"""Mécanique générique d'appel à l'API Mistral (structured output).

Aucune logique métier : création du client et un appel `chat.parse` paramétré par
le schéma Pydantic attendu. Réutilisable par tous les modules (CV, lettre, ...).
Extrait tel quel de l'ancien generate.py (_client / _call).

Couche TRANSPORT (≠ métier) : l'appel réseau est enrobé d'un retry avec back-off
exponentiel + jitter, UNIQUEMENT sur l'erreur 429 / rate_limited (tier gratuit
Mistral qui n'aime pas les bursts). Ce retry rejoue le MÊME appel (mêmes messages,
même schéma) ; il est TRANSPARENT pour la boucle de correction métier — une
tentative de correction reste UN appel logique, qui peut faire plusieurs essais
réseau en interne. Toute autre erreur est relevée immédiatement, sans retry.

Observabilité, deux chemins INDÉPENDANTS. (1) Le journal en base : chaque appel est publié
à `core/llm/capture`, où un collecteur installé par la couche service peut le conserver
(onglet d'administration) ; sans collecteur, il ne se passe rien. (2) Les fichiers
(OPT-IN, désactivée par défaut) : si JOBTOOL_LLM_LOG=1, chaque appel
logique au modèle écrit un fichier JSON horodaté dans logs/llm/ avec le system
prompt, les user messages et la réponse brute — NON tronqués — pour diagnostiquer
ce qui atteint réellement le modèle. Écriture best-effort : un échec de log ne fait
jamais échouer la génération. Sans la variable, AUCUN fichier, comportement inchangé.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from mistralai.client.sdk import Mistral
from pydantic import BaseModel

from jobtool.core.llm.capture import ExchangeRecord, publish

# Modèle de génération (CV + lettre). ministral-14b-latest depuis le 2026-09-17 : le compte
# n'a plus de quota sur la famille mistral-small/medium (limite à 0 requête/min, cf.
# check_mistral.py). Retenu contre ministral-8b et codestral sur la VRAIE génération de CV
# (2 offres de data/offres_test) : compétences les mieux alignées sur l'offre, moins de
# tentatives de correction, reformulations plus fidèles ; codestral inventait des
# compétences. Quota constaté : 30 requêtes/min, largement assez pour un CV (≤ 6 appels).
MODEL = "ministral-14b-latest"

# --- Réglages du retry réseau sur 429 (constantes nommées, modifiables ici) ---
MAX_RETRIES_429 = 5  # nb max de RE-tentatives réseau sur 429 (au-delà : on relève)
BASE_DELAY = 2.0  # délai de base (s) ; back-off = BASE_DELAY * 2**(k-1) + jitter
JITTER_MAX = 1.0  # part aléatoire ajoutée (s) pour désynchroniser les bursts

# --- Logging optionnel des échanges (opt-in via JOBTOOL_LLM_LOG=1) ------------
LOG_ENV_VAR = "JOBTOOL_LLM_LOG"
# Racine du projet : src/jobtool/core/llm/client.py -> parents[4]. Stable quelle
# que soit la CWD ; logs/ est gitignore (données perso, jamais versionnées).
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_LOG_DIR = _PROJECT_ROOT / "logs" / "llm"

T = TypeVar("T", bound=BaseModel)

#: Notification d'une RE-tentative réseau après un 429 : (n-ième retry, délai en s, plafond).
#: Le noyau ne met JAMAIS en forme lui-même — il transmet des nombres, la couche d'interface
#: décide quoi afficher (et l'API ne branche rien : stdout d'uvicorn reste propre).
RetryFn = Callable[[int, float, int], None]

#: Journal du module. Sans handler configuré par l'application, Python n'émet rien au-dessous
#: de WARNING et ne pollue pas stdout — contrairement à un print(). Sert aux diagnostics
#: internes (échec du log opt-in), jamais à parler à l'utilisateur.
_logger = logging.getLogger(__name__)


def _is_rate_limited(exc: Exception) -> bool:
    """Vrai si l'exception est un 429 / rate_limited Mistral (et rien d'autre).

    Robuste au type concret du SDK : on regarde le status HTTP (sur l'exception ou
    sa réponse brute) et, à défaut, les marqueurs textuels "rate_limited" / code 1300.
    """
    if getattr(exc, "status_code", None) == 429:
        return True
    raw_response = getattr(exc, "raw_response", None)
    if getattr(raw_response, "status_code", None) == 429:
        return True
    text = f"{getattr(exc, 'message', '')} {getattr(exc, 'body', '')} {exc}".lower()
    return "rate_limited" in text or "1300" in text


def _retry_delay(attempt: int) -> float:
    """Back-off exponentiel + jitter pour la k-ième re-tentative (attempt = k, 1-indexé)."""
    return BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, JITTER_MAX)


def _retry_on_rate_limit(
    call: Callable[[], T], on_retry: RetryFn | None = None
) -> tuple[T, int]:
    """Exécute `call` ; sur 429, attend (back-off) et rejoue le MÊME appel.

    - 429 : on patiente puis on re-tente, jusqu'à MAX_RETRIES_429 fois.
    - au-delà : on relève l'exception d'origine (échec dur, non masqué).
    - toute AUTRE erreur : relevée immédiatement, sans retry.

    `on_retry(retries, delay, maximum)` est notifié AVANT chaque attente. Absent (défaut) :
    l'attente est SILENCIEUSE — ce module est du transport, il n'écrit pas sur la sortie
    standard ; la mise en forme appartient à la couche d'interface (la CLI la rebranche).

    Renvoie (résultat, nb_retries_429_consommés) ; le compteur sert à l'observabilité.
    """
    retries = 0
    while True:
        try:
            return call(), retries
        except Exception as exc:
            if not _is_rate_limited(exc):
                raise  # pas un 429 : échec dur, on ne retente pas
            retries += 1
            if retries > MAX_RETRIES_429:
                raise  # plafond atteint : on relève l'exception d'origine
            delay = _retry_delay(retries)
            if on_retry is not None:
                on_retry(retries, delay, MAX_RETRIES_429)
            time.sleep(delay)


def _sanitize_slug(slug: str) -> str:
    """Réduit un slug à des caractères sûrs pour un nom de fichier."""
    name = Path(slug).name  # pas d'échappement de dossier
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name) or "single"


def _maybe_log_exchange(
    messages: list[dict],
    model: str,
    parsed: BaseModel | None,
    retries_429: int,
    slug: str | None,
    attempt: int | None,
) -> None:
    """Écrit (si JOBTOOL_LLM_LOG=1) le détail intégral d'un appel modèle dans logs/llm/.

    Best-effort : tout échec d'écriture est signalé sur stderr mais N'INTERROMPT PAS
    la génération. Sans la variable d'env, retour immédiat (aucun coût notable).
    """
    if os.environ.get(LOG_ENV_VAR) != "1":
        return
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now()

        # On expose à la fois la décomposition lisible (system/user) ET les messages
        # bruts tels que passés au modèle, sans rien tronquer.
        system_prompt = "\n\n".join(
            m.get("content", "") for m in messages if m.get("role") == "system"
        )
        user_messages = [
            m.get("content", "") for m in messages if m.get("role") == "user"
        ]

        if parsed is None:
            response_dump = None
        elif isinstance(parsed, BaseModel):
            response_dump = parsed.model_dump(mode="json")
        else:  # filet de sécurité : objet inattendu, on le rend sérialisable
            response_dump = str(parsed)

        record = {
            "timestamp": now.isoformat(),
            "model": model,
            "slug": slug or "single",
            "attempt": attempt,  # numéro de tentative de correction si fourni, sinon null
            "retries_429": retries_429,
            "system_prompt": system_prompt,
            "user_messages": user_messages,
            "messages": messages,  # intégral, tel qu'envoyé
            "response": response_dump,  # JSON structuré renvoyé, intégral
        }

        slug_part = _sanitize_slug(slug or "single")
        attempt_part = attempt if attempt is not None else "NA"
        ts_part = now.strftime("%Y%m%dT%H%M%S_%f")  # microsecondes -> noms uniques
        path = _LOG_DIR / f"{ts_part}__{slug_part}__attempt{attempt_part}.json"
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, on n'échoue jamais sur le log
        _logger.warning("[%s] échec d'écriture du log LLM : %s", LOG_ENV_VAR, exc)


#: Clé du COMPTE pour les appels faits dans ce contexte (cf. `using_api_key`). Les chaînes
#: de génération appellent `make_client()` très bas, sans connaître le compte : le service
#: qui les lance pose la clé ici, dans le fil qui fait le travail.
_account_api_key: ContextVar[str | None] = ContextVar("account_api_key", default=None)


@contextmanager
def using_api_key(api_key: str | None) -> Iterator[None]:
    """Les `make_client()` appelés dans ce bloc utilisent `api_key` (si elle est fournie)."""
    token = _account_api_key.set(api_key)
    try:
        yield
    finally:
        _account_api_key.reset(token)


def make_client(api_key: str | None = None) -> Mistral:
    """Crée le client Mistral, ou échoue clairement si la clé manque.

    Ordre : la clé passée en argument, puis celle posée par `using_api_key` (clé du compte),
    puis celle du serveur, décrite ci-dessous.

    La clé est cherchée dans l'ENVIRONNEMENT d'abord, puis dans le `.env` via le settings
    (pydantic-settings). Ce second chemin n'est pas un luxe : seules les commandes CLI
    appellent `load_dotenv()`, si bien qu'un `.env` parfaitement renseigné restait
    invisible depuis le serveur API — la génération échouait alors sur « clé absente »
    alors que la clé était là. Le settings, lui, lit le même `.env` dans tout processus.

    L'environnement garde la priorité : une clé exportée à la main l'emporte sur le
    fichier, ce qui permet de basculer de clé le temps d'une commande.
    """
    api_key = api_key or _account_api_key.get() or os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        from jobtool.settings import AggregatorSettings

        api_key = AggregatorSettings().mistral_api_key
    if not api_key:
        raise RuntimeError(
            "MISTRAL_API_KEY absente : renseigne ta clé dans Réglages > Clés d'API, ou "
            "copie .env.example en .env et renseigne-la "
            f"(fichier .env cherché dans le dossier de lancement : {Path.cwd()})."
        )
    return Mistral(api_key=api_key)


def call_structured(
    client: Mistral,
    messages: list[dict],
    response_format: type[T],
    model: str = MODEL,
    temperature: float = 0,
    log_slug: str | None = None,
    log_attempt: int | None = None,
    on_retry: RetryFn | None = None,
) -> T:
    """Un appel structured output ; renvoie l'instance Pydantic parsée.

    L'appel réseau est enrobé du retry 429 (transparent pour l'appelant). La
    validation du contenu (parsed None) reste hors retry : ce n'est pas un 429.

    log_slug / log_attempt : contexte OPTIONNEL pour l'observabilité — l'ÉTAPE métier
    (« routage », « redaction », « cv_a »…) et le numéro de tentative de la boucle de
    correction. Ils nomment le fichier de log et situent l'appel dans le journal. Absents,
    l'appel reste enregistré, sans étape.
    """
    response, retries_429 = _retry_on_rate_limit(
        lambda: client.chat.parse(
            model=model,
            messages=messages,
            response_format=response_format,
            temperature=temperature,
        ),
        on_retry,
    )
    message = response.choices[0].message if response.choices else None
    parsed = message.parsed if message is not None else None
    # Observabilité APRÈS réception (on capture la réponse, même si parsed est None), et
    # best-effort des deux côtés : ni la publication ni le fichier ne font échouer l'appel.
    publish(
        ExchangeRecord(
            model=model,
            step=log_slug or "",
            attempt=log_attempt or 1,
            retries_429=retries_429,
            # COPIE : la boucle de correction ajoute ses messages à CETTE liste avant
            # que le journal ne l'écrive. Sans figer l'état ici, le premier appel serait
            # enregistré avec les messages du second.
            messages=[dict(message) for message in messages],
            response=parsed.model_dump(mode="json")
            if isinstance(parsed, BaseModel)
            else None,
        )
    )
    _maybe_log_exchange(messages, model, parsed, retries_429, log_slug, log_attempt)
    if parsed is None:
        raise RuntimeError("Mistral n'a pas renvoyé de structure exploitable.")
    return parsed
