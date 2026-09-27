# ADR 0007 : pile applicative du site

Statut : propose et mis en oeuvre (2026-09-27). Premiere brique reelle (US5.2).

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
- Connexion a la meme base Postgres que le reste de l'infra (`compose.yaml`), via les variables du `.env` racine (meme fichier, pas de duplication de secrets) : `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT`.
- Le site tourne en dehors de Docker pendant le developpement (`uvicorn` local, rechargement a chaud) ; sa dockerisation est une tache separee, une fois le schema stabilise (US5.2 et suivantes).
- `ci-app.yml` doit detecter `site/pyproject.toml` en plus des chemins deja prevus.

## Premiere brique implementee (US5.2, IAF-30)

Modele `User` (id, email, mot de passe Argon2id, role parmi viewer/creator/admin, actif, date de creation), connexion par formulaire, cookie de session signe avec expiration, dependance FastAPI qui refuse l'anonyme et qui lit le role depuis la base (jamais depuis une donnee envoyee par le client). Creation du tout premier admin par un script hors API (`site/scripts/create_admin.py`), puisque US5.1/US5.7 (decide le 2026-09-27) reservent la creation de comptes a l'admin : quelqu'un doit exister avant l'API pour en creer d'autres.

## Non fait dans cette premiere passe

- Dockerisation du site.
- SSO, verrouillage apres echecs de connexion repetes, envoi d'email d'activation (questions ouvertes de US5.1/US5.2).
- Alembic est initialise avec une seule migration (le modele `User`) ; le reste du schema (projets, agents, exclusions...) suivra epic par epic.

## Questions ouvertes

- Duree de vie de la session (valeur choisie : 12 h, a confirmer avec l'utilisateur).
- Verrouillage de compte apres echecs de connexion repetes : pas implemente.
