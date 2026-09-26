# ADR 0006 : orchestration du pipeline documentaire

Statut : propose (2026-09-26). Rien n'est implémenté.

## Besoin

Enchaîner par document les étapes Ingestion, Reconnaissance, Création de classe, Structuration (conception section 13), avec état persistant, reprise, idempotence, retraitement en cascade, concurrence bornée et suivi.

## Options

| Option | Pour | Contre |
|---|---|---|
| **A. File de tâches dans Postgres** (table `stage_runs`, workers qui réservent une ligne) | Aucun service de plus ; l'état est déjà dans la base ; transactionnelle avec les données applicatives ; simple à comprendre | À écrire : réservation, reprise, planification ; débit limité (suffisant pour des milliers de documents par jour, à mesurer) |
| **B. Moteur de workflow** (Temporal, Prefect, Airflow, Dagster...) | Reprise, planification et interface fournies | Un service de plus à héberger, sécuriser et superviser ; concepts propres à apprendre ; non évalué ici (versions et licences à vérifier) |
| **C. Courtier de messages** (Redis, RabbitMQ, NATS) | Débit élevé, découplage | Il faut quand même stocker l'état ailleurs ; un service de plus |

## Décision proposée

**Option A pour démarrer**, derrière une interface `StageRunner` (une étape = une fonction avec entrée, sortie, empreinte d'entrée et version de configuration). Migrer vers B ou C reste possible sans réécrire les étapes. Critères de sortie de A : débit mesuré insuffisant, ou besoin de workflows longs avec attentes humaines complexes.

## Règles quelle que soit l'option

- Une étape est **idempotente** : clé = (document, étape, empreinte des entrées, version de configuration).
- L'état d'un document est dans Postgres ; les artefacts volumineux sur le stockage de documents.
- **Concurrence bornée par étape** (l'ingestion est limitée par le CPU de l'analyse, R et S par le budget LLM).
- **Isolation** : l'analyse de fichier (étape I) dans un conteneur sans réseau.
- **Appels LLM** par la passerelle uniquement, un alias par étape (`iaf-extraction` pour R et S, `iaf-embedding` pour I).
- **Événements d'activité** à chaque transition (E12).
- **Pause globale** : l'admin peut suspendre le pipeline (coût, incident).

## Non évalué

- Le débit réel de la file Postgres (à mesurer avec IAF-E13 US13.8).
- Les moteurs de workflow de l'option B : aucune vérification de version, de licence ni d'empreinte n'a été faite.

## Questions ouvertes

- Volumétrie attendue (documents par jour) ?
- Faut-il une file de priorité (un creator qui attend son premier document doit passer avant un lot) ?
