# EPIC IAF-E2 : pipelines CI/CD (ecrits, non utilises)

Objectif : pipelines GitHub Actions prets, mais declenchables seulement a la main jusqu'a decision explicite.

Jira : IAF-2 (taches IAF-14 a IAF-19, T1 a T6 dans l'ordre).

Statut : T1 a T5 ecrites, aucune executee. T6 (premier run) attend la demande de l'utilisateur.

## T1 `ci-infra`
- Description : lint (compose config, hadolint, shellcheck), gitleaks, smoke (up des 3 services, n10s, SPARQL), scan Trivy de l'image Fuseki.
- Prerequis : IAF-E1 T4, T5, T6.
- Acceptance criteria : `workflow_dispatch` seul ; echec si compose invalide, secret detecte, service non sain ou vulnerabilite HIGH/CRITICAL corrigeable ; `down -v` toujours execute.
- Contexte : `smoke` genere un `.env` de CI avec `openssl rand`. Le smoke telecharge plugins Neo4j et archive Jena : duree a mesurer (timeout 20 min pose a l'aveugle).
- Exemples : mot de passe Fuseki vide en CI : `up --wait` echoue, logs affiches par l'etape d'echec.
- Questions ouvertes : `hadolint-action@v3.1.0` et `gitleaks-action@v2` a confirmer au premier run.

## T2 `ci-app`
- Description : squelette Node et Python qui se saute si aucun manifeste n'existe.
- Acceptance criteria : sans code applicatif, les jobs `node` et `python` sont sautes ; a raffiner apres choix de la pile (IAF-E4/E5).
- Questions ouvertes : pile de l'API et du site.

## T3 `cd-images`
- Description : publication de l'image Fuseki sur GHCR, tag fourni a la main.
- Acceptance criteria : `packages: write` limite a ce workflow ; aucune etape de deploiement.
- Questions ouvertes : cible de deploiement (VM, Kubernetes, compose distant) non decidee.

## T4 Dependabot et protection de branche
- Description : `.github/dependabot.yml.disabled` (actions, docker) et regle de protection de `main`.
- Acceptance criteria : desactive par defaut ; activation decrite dans le runbook.

## T5 Runbook d'activation
- Description : `docs/runbooks/activer-les-pipelines.md`.
- Acceptance criteria : dit comment lancer a la main et comment activer les declencheurs.

## T6 Premier run a blanc
- Prerequis : accord de l'utilisateur, IAF-E1 T6 termine, depot pousse.
- Acceptance criteria : `ci-infra` vert ; durees et corrections reportees dans cet epic.
