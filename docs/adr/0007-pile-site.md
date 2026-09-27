# ADR 0007 : pile applicative du site

Statut : propose et mis en oeuvre (2026-09-27). Dockerise et pages admin (US5.1, US5.7, US5.8) le meme jour.

## Decision

Python cote serveur, rendu cote serveur (pas de SPA pour cette premiere version) :

| Brique | Choix | Version verifiee (PyPI, 2026-09-27) |
|---|---|---|
| Langage | Python 3.13 | deja utilise par poc/ et la CI |
| Framework web | FastAPI | 0.141.1 |
| Serveur ASGI | uvicorn[standard] | 0.54.0 |
| ORM | SQLAlchemy (style 2.0) | 2.1.1 |
| Migrations | Alembic | 1.20.0 |
| Pilote Postgres | psycopg (v3) | 3.3.6 |
| Configuration | pydantic-settings | 2.15.0 |
| Hachage de mot de passe | argon2-cffi (Argon2id) | 25.1.0 |
| Cookies de session signes | itsdangerous | 2.2.0 |
| Gabarits HTML | Jinja2 | 3.1.6 |
| Interactivite | HTMX (CDN, pas de paquet pip) | - |
| Televersement de fichiers | python-multipart | 0.0.32 |

## Pourquoi

- **Coherence** : tout l'ecosysteme deja mobilise (poc/, rdflib en CI, futur Docling) est Python ; un seul langage pour l'API et les scripts d'ingestion evite une double implementation des memes regles (droits, format des identifiants).
- **Simple d'abord** : rendu serveur + Jinja2 + HTMX evite un pipeline de build JavaScript separe, coherent avec l'ambition « IA factory simple » du besoin initial. Une SPA reste possible plus tard sans changer l'API si le besoin s'en fait sentir (alternative ecartee pour l'instant, pas fermee).
- **Argon2id** : recommandation OWASP courante (Password Storage Cheat Sheet), verifiee par recherche le 2026-09-27, avant `bcrypt` et `PBKDF2`. Bibliotheque `argon2-cffi` (implementation native, evite les implementations pures Python 100x plus lentes).
- **Session par cookie signe** (`itsdangerous`), pas de JWT : plus simple pour un site rendu cote serveur ; revocation immediate possible (on stocke l'id de session cote serveur si besoin plus tard) contrairement a un JWT auto-suffisant.
- **psycopg 3** plutot que psycopg2 : pilote actif, supporte l'usage asynchrone si le besoin apparait.

## Consequences

- Emplacement : `site/` a la racine du depot, distinct de `poc/` (jetable) et de `infra/`.
- Connexion a la meme base Postgres que le reste de l'infra (`compose.yaml`). En local hors Docker : `POSTGRES_HOST=127.0.0.1` (defaut) et `POSTGRES_PORT` du `.env` racine (port hote, peut etre decale). Dans le conteneur `site` : `POSTGRES_HOST=postgres` et `POSTGRES_PORT=5432` fixes par `compose.yaml` (reseau interne Docker, jamais le port hote remappe) - voir `site/app/config.py`.
- `ci-app.yml` detecte `site/pyproject.toml` (job `site` dedie, plus generique node/api).

## Premiere brique implementee (US5.2, IAF-30)

Modele `User` (id, email, mot de passe Argon2id, role parmi viewer/creator/admin, actif, date de creation), connexion par formulaire, cookie de session signe avec expiration, dependance FastAPI qui refuse l'anonyme et qui lit le role depuis la base (jamais depuis une donnee envoyee par le client). Creation du tout premier admin par un script hors API (`site/scripts/create_admin.py`), puisque US5.1/US5.7 (decide le 2026-09-27) reservent la creation de comptes a l'admin : quelqu'un doit exister avant l'API pour en creer d'autres.

## Dockerisation (2026-09-27)

`site/Dockerfile` (python:3.13-slim, utilisateur non root) + `site/entrypoint.sh` qui applique `alembic upgrade head` (idempotent) puis lance `uvicorn`. Service `site` dans `compose.yaml`, port hote par defaut 8010 (`SITE_PORT`), sonde `/healthz` sans authentification. Ajoute a `compose.secure.yaml` sur le seul reseau `data` (pas de sortie Internet necessaire : le site n'appelle aucun fournisseur externe lui-meme, tout appel LLM passe par `llm-gateway`).

**Verifie reellement** (build, demarrage, migrations, navigateur puis HTTP direct) : image construite et demarree saine ; `alembic upgrade head` s'execute a chaque demarrage sans erreur ; connexion admin, creation d'un creator et d'un viewer via `/admin/users/new`, suppression reelle d'un viewer via `/admin/users` (disparait de la liste et de la base) ; cas limites verifies par HTTP direct : email deja utilise (409), mot de passe trop court (422), tentative de forger `role=admin` a la creation (422), acces de la page admin par un creator (403), auto-suppression bloquee.

## Pages admin implementees (US5.1, US5.7, US5.8)

- `GET/POST /admin/users/new` : formulaire commun de creation (email, mot de passe initial, role viewer ou creator par bouton radio). La creation d'un admin n'est PAS exposee ici, volontairement : seul `scripts/create_admin.py` (hors API) le permet, pour qu'une faille dans cette page ne puisse pas fabriquer un admin.
- `GET /admin/users` : liste de tous les comptes (courriel, role, actif, date de creation) avec un bouton de suppression pour chaque compte non-admin et non soi-meme.
- `POST /admin/users/{id}/delete` : suppression definitive (pas de corbeille) ; bloque sur soi-meme et sur toute cible admin.
- Les deux routes sont protegees par `require_role(Role.admin)` (403 pour les autres roles).

## Non fait

- SSO, verrouillage apres echecs de connexion repetes, envoi d'email d'activation (questions ouvertes de US5.1/US5.2).
- Alembic est initialise avec une seule migration (le modele `User`) ; le reste du schema (projets, agents, exclusions...) suivra epic par epic.
- Aucune protection CSRF explicite sur les formulaires POST (`admin_new_user.html`, `admin_users.html`) : le cookie de session est `samesite=lax`, ce qui reduit le risque sans l'annuler. A traiter avant tout usage hors machine de developpement.
- La confirmation `confirm()` JavaScript avant suppression est une mesure d'ergonomie cote client, jamais une garantie cote serveur (le serveur re-verifie tout : cible non-soi, cible non-admin).
- Journal d'activite (US12.1) non branche : la creation et la suppression de compte ne sont pas encore des evenements journalises, seulement des lignes en base.

## Questions ouvertes

- Duree de vie de la session (valeur choisie : 12 h, a confirmer avec l'utilisateur).
- Verrouillage de compte apres echecs de connexion repetes : pas implemente.
- Suppression definitive ou desactivation (`is_active=false`) une fois que des projets/agents seront lies a un utilisateur ?
- Comment un viewer ou creator change-t-il son mot de passe initial (impose par l'admin a la creation) ? Pas encore d'ecran pour ca.
