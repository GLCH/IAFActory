# EPIC IAF-E10 : sessions avec les agents

Jira : IAF-40, stories IAF-58 a IAF-62 (US10.1 a US10.5). Statut : rédigé le 2026-09-26. Conception : [conception.md](../design/conception.md) section 8. Corrige l'hypothèse précédente (IAF-32) : un viewer peut utiliser les agents en session.

Objectif : viewers et creators conversent avec un agent qui leur est visible ; le viewer n'a aucun droit de modification et ne voit rien de ce que le creator lui interdit.

Questions transverses : le creator voit-il les sessions des viewers ? durée de conservation ? quotas ?

## US10.1 Ouvrir une session
- En tant que viewer, j'ouvre une session avec un agent que je peux voir.
- Prérequis : IAF-30, IAF-32, IAF-26.
- Acceptance criteria : la liste des agents proposés exclut les agents exclus pour moi ; ouvrir une session sur un agent exclu renvoie 404 ; l'agent doit être validé (IAF-E6) ou marqué utilisable par son creator ; la session enregistre agent, version d'agent, utilisateur.
- Exemples : le viewer V exclu de l'agent X : X absent de sa liste, POST direct de session : 404.

## US10.2 Échanger dans la session
- En tant que viewer, je pose des questions et j'obtiens des réponses sourcées.
- Prérequis : US10.1, IAF-24.
- Acceptance criteria : historique conservé pour le contexte ; chaque réponse cite ses sources (document, position) ; plafonds de jetons et de durée par tour et par session ; échec explicite (pas de silence) ; l'agent ne peut ni modifier sa définition ni écrire dans le graphe depuis une session viewer.
- Exemples : une question hors spécialité reçoit « hors du périmètre de cet agent » plutôt qu'une réponse inventée.

## US10.3 Filtrer les sources selon exclusions et spécialité
- En tant que creator, je veux que les exclusions posées à un viewer s'appliquent à chaque tour de conversation.
- Prérequis : US10.2, IAF-E7 US7.4, IAF-25 (spécialité).
- Acceptance criteria : récupération filtrée par `classes du document ∩ spécialité de l'agent`, puis exclusions du viewer, puis projet ; une exclusion posée pendant une session s'applique au tour suivant ; test croisé viewer x exclusion x agent x classe ; aucune citation ni extrait d'une source exclue dans la réponse, y compris paraphrasé de l'historique de la session.
- Contexte : cas délicat : une exclusion posée après qu'une source a déjà été citée dans l'historique de session. À trancher : retrait de l'historique ou non.
- Exemples : V exclu du document D, D a servi au tour 2 ; le creator exclut D ; au tour 3, la réponse ne cite plus D.

## US10.4 Consulter, reprendre et supprimer ses sessions
- En tant qu'utilisateur, je retrouve, reprends ou supprime mes sessions.
- Prérequis : US10.2.
- Acceptance criteria : un utilisateur ne voit que ses sessions ; suppression effective (messages inclus) ; règle de visibilité pour le creator affichée à l'utilisateur avant la première session.
- Questions : le creator voit-il les sessions de ses viewers ? (par défaut : non, à confirmer.)

## US10.5 Quotas et protection contre l'abus
- En tant qu'admin, je limite l'usage par utilisateur et par agent.
- Prérequis : US10.2.
- Acceptance criteria : quotas de messages et de jetons par jour ; limitation de débit par utilisateur ; dépassement renvoie 429 avec le délai ; l'admin arrête toutes les sessions d'un projet (lié à IAF-33).
- Exemples : au 101e message d'un quota de 100, réponse 429 avec l'heure de reprise.
