# EPIC IAF-E5 : site, roles et droits

Jira : IAF-5, stories IAF-29 a IAF-33 (US5.1 a US5.5), IAF-82 (US5.6, fait), IAF-96 (US5.7, nouvelle). Statut : capacites des creators arretees le 2026-09-27 (US5.6), voir conception.md section 9.

Objectif : un site ou chaque role fait ce qui lui est permis, et rien d'autre. Les droits sont appliques dans l'API, pas seulement dans l'interface.

Regles : le viewer voit tout sauf ce que le creator proprietaire lui interdit, sur les projets de ce creator uniquement. Le creator cree et gere ses agents et leurs tests. L'admin cree les creators, arrete et efface tout projet.

Questions transverses : authentification (locale, SSO) ? granularite des exclusions (projet, agent, document, graphe) ? un viewer est-il restreint par defaut ? un utilisateur peut-il avoir plusieurs roles ?

## US5.1 Creer un creator (admin)
- En tant qu'admin, je cree un compte creator.
- Prerequis : US5.2.
- Acceptance criteria : seul un admin le peut (403 sinon, teste) ; identifiant unique ; le creator recoit un moyen d'activer son compte sans mot de passe en clair dans un journal ; creation journalisee.
- Contexte : decide le 2026-09-27 (US5.6) : l'admin cree AUSSI les comptes viewer, voir US5.7. Aucun creator ne peut creer de compte, quel que soit le role.
- Exemples : un creator tente de creer un creator : 403.
- Questions : envoi d'invitation par courriel ?

## US5.2 Se connecter
- En tant qu'utilisateur, je me connecte et l'API connait mon role.
- Prerequis : IAF-1 T4 (Postgres).
- Acceptance criteria : mots de passe haches (algorithme verifie dans la doc avant choix) ; session ou jeton avec expiration ; toute route protegee refuse l'anonyme ; le role vient du serveur, jamais du client.
- **Premiere implementation le 2026-09-27** (`site/`, ADR 0007) : FastAPI + SQLAlchemy + Postgres, mot de passe Argon2id (`argon2-cffi`, recommandation OWASP verifiee), cookie de session signe (`itsdangerous`, 12 h) ne portant que l'id utilisateur (le role est relu en base a chaque requete, jamais depuis le cookie). Verifie reellement dans le navigateur : connexion admin -> tableau de bord avec le bon role affiche ; deconnexion -> `/` redirige vers `/login` ; `GET /` anonyme -> 303 vers `/login`. Tests automatises (`site/tests/test_auth.py`) : anonyme refuse, session valide acceptee, cookie falsifie sans effet.
- Contexte / piege reel rencontre : un `User(...)` construit sans passer par une session SQLAlchemy (ex. dans un test) n'a pas encore `is_active=True` (le defaut de colonne ne s'applique qu'au flush) ; explicite dans le test, a garder en tete pour tout code qui construit un `User` hors ORM.
- Exemples : jeton expire ou falsifie : redirection vers `/login` (equivalent 401 gere globalement, pas un JSON brut puisque le site est rendu cote serveur).
- Questions : SSO plus tard ? Verrouillage apres echecs de connexion repetes : non fait.

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

## US5.6 Definir les capacites des creators
- Jira : IAF-82.
- En tant que responsable produit, je definis precisement ce que peuvent faire les creators, avant de coder les droits.
- Prerequis : aucun (atelier avec l'utilisateur) ; alimente US3.10, US11.3, IAF-E9.
- Acceptance criteria : une matrice creator (action x ressource) validee par l'utilisateur, couvrant au minimum : glossaire commun (proposer, modifier, promouvoir), classes documentaires (creer, fusionner, valider, rejeter), agents (creer, modifier, arreter), choix du modele et du budget LLM de ses agents, connecteurs (types autorises, sources), exclusions viewer, tests de validation, consultation de l'activite ; chaque ligne indique creator, admin, ou les deux ; les cas limites sont listes ; la matrice remplace la section 9 de la conception.
- **Statut : fait le 2026-09-27.** Matrice complete dans conception.md section 9. Quatre decisions structurantes : (1) l'admin cree tous les comptes, creator et viewer (US5.7 ajoutee) ; (2) le glossaire commun est gouverne par revue collective des creators, pas par un role de curateur ni par l'admin (US3.10 revisee) ; (3) tout connecteur, meme d'un type deja catalogue, est soumis a l'approbation de l'admin avant activation (US9.1 revisee) ; (4) l'admin fixe le budget LLM de chaque agent individuellement, le creator choisit seulement le fournisseur et le modele (US11.3, US4.1 revisees).
- Exemples : « Un creator peut-il choisir un modele plus cher pour son agent, dans la limite de son budget ? » Non : le budget est fixe par l'admin, le creator choisit le modele mais pas le budget.

## US5.7 Creer un viewer (admin)
- En tant qu'admin, je cree un compte viewer.
- Prerequis : US5.2. Decide en US5.6 (2026-09-27) : symmetrique de US5.1, un creator ne peut jamais creer de compte.
- Acceptance criteria : seul un admin le peut (403 sinon, teste) ; identifiant unique ; le viewer recoit un moyen d'activer son compte sans mot de passe en clair dans un journal ; creation journalisee ; le viewer voit tous les projets des la creation, sauf exclusions posees par les creators (US5.3).
- Exemples : un creator tente de creer un viewer : 403.
- Questions : envoi d'invitation par courriel ? un viewer peut-il etre associe a un ou plusieurs creators specifiques a la creation, ou est-il global d'emblee (hypothese : global, les exclusions font le filtrage) ?
