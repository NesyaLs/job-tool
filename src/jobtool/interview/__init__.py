"""Domaine « entretien » : un LLM mène un entretien et PROPOSE des éléments de profil.

Principe du projet appliqué à l'entretien : le LLM propose, le code vérifie, l'utilisateur
décide. Le LLM n'écrit jamais dans la base d'expériences : il produit des propositions
structurées (`schema.py`), que le code valide et contrôle (`checks.py`) avant de les montrer.
Seule une acceptation explicite les applique à la base (`apply.py`).

Ce domaine n'importe ni `services/` ni un autre domaine : il dépend de `core/` seulement
(modèle de la base d'expériences, client LLM, schéma de la base de données).
"""
