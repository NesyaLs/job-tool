"""Teste la clé Mistral : un appel minimal (1 token) par modèle, avec les quotas renvoyés.

Usage (depuis la racine du dépôt) :
    .venv/bin/python scripts/check_mistral.py                     # modèle du projet + 2 témoins
    .venv/bin/python scripts/check_mistral.py mistral-small-latest ministral-8b-latest

La clé est lue comme le fait l'application (variable d'environnement, sinon `.env`) et
n'est jamais affichée. Chaque appel coûte quelques tokens.
"""

from __future__ import annotations

import os
import sys

import httpx

from jobtool.core.llm.client import MODEL
from jobtool.settings import AggregatorSettings

API_URL = "https://api.mistral.ai/v1"
WITNESS_MODELS = ("ministral-8b-latest", "open-mistral-nemo")


def main() -> int:
    key = os.environ.get("MISTRAL_API_KEY") or AggregatorSettings().mistral_api_key
    if not key:
        print("✗ MISTRAL_API_KEY absente (ni dans l'environnement, ni dans .env).")
        return 1
    headers = {"Authorization": f"Bearer {key}"}
    print(f"Clé trouvée (…{key[-4:]})\n")

    # 1. La clé est-elle acceptée ? (lister les modèles ne consomme pas de quota de chat)
    try:
        r = httpx.get(f"{API_URL}/models", headers=headers, timeout=30)
    except httpx.HTTPError as exc:
        print(f"✗ Réseau : {exc}")
        return 1
    if r.status_code != 200:
        print(f"✗ Clé refusée : HTTP {r.status_code} — {r.text[:200]}")
        return 1
    print(f"✓ Clé valide : {len(r.json().get('data', []))} modèles accessibles\n")

    # 2. Un appel de chat minimal par modèle.
    models = sys.argv[1:] or [MODEL, *WITNESS_MODELS]
    ok = False
    for model in models:
        try:
            r = httpx.post(
                f"{API_URL}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Réponds OK."}],
                    "max_tokens": 3,
                },
                timeout=60,
            )
        except httpx.HTTPError as exc:
            print(f"✗ {model:28} réseau : {exc}")
            continue
        limit = r.headers.get("x-ratelimit-limit-req-minute", "?")
        remaining = r.headers.get("x-ratelimit-remaining-req-minute", "?")
        tokens = r.headers.get("x-ratelimit-remaining-tokens-minute", "?")
        quota = f"req/min {remaining}/{limit}, tokens/min restants {tokens}"
        if r.status_code == 200:
            ok = True
            answer = r.json()["choices"][0]["message"]["content"].strip()
            print(f"✓ {model:28} 200  « {answer} »  ({quota})")
        else:
            try:
                message = r.json().get("message", r.text)
            except ValueError:
                message = r.text
            print(f"✗ {model:28} {r.status_code}  {str(message)[:80]}  ({quota})")
            if r.status_code == 429 and limit == "0":
                print(f"  → quota à 0 pour {model} : limite du compte, pas un pic.")

    print(f"\nModèle utilisé par l'application : {MODEL}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
