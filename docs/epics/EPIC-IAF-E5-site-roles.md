# EPIC IAF-E5 : site, roles et droits

Jira : IAF-5, stories IAF-29 a IAF-33 (US5.1 a US5.5). Statut : user stories redigees, regles d'exclusion a preciser.

Objectif : un site ou chaque role fait ce qui lui est permis, et rien d'autre. Les droits sont appliques dans l'API, pas seulement dans l'interface.

Regles : le viewer voit tout sauf ce que le creator proprietaire lui interdit, sur les projets de ce creator uniquement. Le creator cree et gere ses agents et leurs tests. L'admin cree les creators, arrete et efface tout projet.

Questions transverses : authentification (locale, SSO) ? granularite des exclusions (projet, agent, document, graphe) ? un viewer est-il restreint par defaut ? un utilisateur peut-il avoir plusieurs roles ?

## US5.1 Creer un creator (admin)
- En tant qu'admin, je cree un compte creator.
- Prerequis : US5.2.
- Acceptance criteria : seul un admin le peut (403 sinon, teste) ; identifiant unique ; le creator recoit un moyen d'activer son compte sans mot de passe en clair dans un journal ; creation journalisee.
- Exemples : un creator tente de creer un creator : 403.
- Questions : envoi d'invitation par courriel ?

## US5.2 Se connecter
- En tant qu'utilisateur, je me connecte et l'API connait mon role.
- Prerequis : IAF-1 T4 (Postgres).
- Acceptance criteria : mots de passe haches (algorithme verifie dans la doc avant choix) ; session ou jeton avec expiration ; toute route protegee refuse l'anonyme ; le role vient du serveur, jamais du client.
- Exemples : jeton expire : 401 ; role modifie cote client : ignore.
- Questions : SSO plus tard ?

## US5.3 Gerer mes projets et les exclusions viewer (creator)
- En tant que creator, je cree un projet et j'interdis certaines ressources a certains viewers.
- Prerequis : US5.2.
- Acceptance criteria : un creator ne voit et ne modifie que ses projets ; une exclusion vise un viewer et une ressource de son projet ; impossible de poser une exclusion sur le projet d'un autre creator ; l'exclusion est effective immediatement.
- Exemples : le creator A exclut le viewer V de l'agent X : V ne le voit plus mais voit les autres agents de A ; A n'a aucun effet sur les projets de B.
- Questions : granularite exacte, heritage projet vers agents.

## US5.4 Consulter en tant que viewer
- En tant que viewer, je vois tous les projets et ressources sauf mes exclusions.
- Prerequis : US5.3.
- Acceptance criteria : lecture seule partout (toute ecriture : 403) ; une ressource exclue est absente des listes, des recherches et du graph RAG, et son URL directe renvoie 404 ; teste par un jeu de cas croises viewer x creator x ressource.
- Exemples : V exclu de X : la recherche graph RAG de V ne renvoie aucun passage des documents de X.
- Decision 2026-09-26 : un viewer peut utiliser les agents dans des sessions (voir [IAF-E10](EPIC-IAF-E10-sessions.md)), sans droit de modification de l'agent ni du projet. Les exclusions s'appliquent aussi en session. Un viewer ne peut toujours pas creer, modifier, arreter ou supprimer un agent.
- Exemples complementaires : V (viewer) ouvre une session avec l'agent Y (non exclu) : autorise ; V tente de changer la consigne de Y : 403.

## US5.5 Arreter et effacer un projet (admin)
- En tant qu'admin, j'arrete ou j'efface tout projet de tout creator.
- Prerequis : IAF-E4 US4.2.
- Acceptance criteria : l'arret stoppe agents et groupes du projet ; l'effacement demande une confirmation explicite et supprime donnees Postgres, graphe Neo4j, graphe nomme Fuseki et fichiers ; action journalisee avec l'admin auteur ; un creator ne peut pas effacer le projet d'un autre.
- Exemples : apres effacement du projet P, plus aucun noeud Neo4j ni triplet Fuseki associe a P (verifie par requete).
- Questions : suppression definitive ou corbeille avec delai ?
