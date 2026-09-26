# Activer les pipelines

Les workflows sont en `workflow_dispatch` seul. Rien ne tourne sur push ou PR.

## Lancer un pipeline a la main

Onglet Actions de GitHub, choisir `ci-infra`, `Run workflow`. Ou, avec `gh` authentifie :

```powershell
gh workflow run ci-infra.yml
```

## Activer le declenchement automatique

1. Dans chaque `.github/workflows/*.yml`, decommenter les lignes `push` / `pull_request` sous `on:`.
2. Renommer `.github/dependabot.yml.disabled` en `.github/dependabot.yml`.
3. Regler la protection de la branche `main` (checks requis : `lint`, `smoke`).

## Points d'attention

- `secrets` (gitleaks) : gratuit pour un compte personnel ; une organisation demande une licence.
- `cd-images` publie sur GHCR : le package est prive par defaut.
- Versions d'actions figees en majeur (`@v4`, `@v3`...) : Dependabot les met a jour une fois active.
- Non testes : aucun des workflows n'a encore tourne (voir docs/epics/EPIC-IAF-E2).
