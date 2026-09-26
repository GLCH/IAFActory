# IAFActory

## Approche
- Lire les fichiers existants avant d'ecrire. Ne pas relire sauf changement.
- Reflexion rigoureuse, sortie concise. Pas d'ouverture flatteuse ni de conclusion de politesse.
- Pas d'emojis, pas de tirets cadratins.
- Ne pas deviner API, versions, flags, SHA de commit, noms de paquets : verifier dans le code ou la doc.
- Docs et commentaires en francais ; identifiants de code en anglais.

## Methode
- Toute nouvelle capacite commence par des epics et taches au format Definition of Ready dans `docs/epics/` : description, prerequis, acceptance criteria, contexte, exemples (regle, 1-2 exemples, questions ouvertes). Miroir dans Jira (projet IAF, types "Flux de travail" pour les epics, "Tache").
- Mettre a jour l'epic APRES mesure quand la realite contredit l'hypothese ; les chiffres mesures vont dans "Contexte".
- Les benefices annonces sont valides hors echantillon.

## Contraintes
- Les pipelines `.github/workflows/*` restent en `workflow_dispatch` seul tant que l'utilisateur n'a pas demande de les activer.
- Aucun secret dans git : `.env` est genere par `scripts/init-env.ps1`.
- Ports lies a 127.0.0.1.
