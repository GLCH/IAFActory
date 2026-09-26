# EPIC IAF-E12 : traçabilité et tableaux de bord d'usage

Jira : IAF-67, stories IAF-74 a IAF-78 (US12.1 a US12.5). Statut : rédigé le 2026-09-26. Conception : [conception.md](../design/conception.md) section 11.

Objectif : savoir qui accède à quoi et combien la plateforme est utilisée, avec un historique d'activité et un tableau de bord simples. Grafana viendra plus tard, sur les mêmes données.

Questions transverses : durée de rétention ? le creator voit-il l'activité de ses viewers sur ses projets ? volumes attendus d'événements par jour ?

## US12.1 Journal d'activité
- En tant qu'admin, je veux que chaque accès et action soient enregistrés.
- Prérequis : US5.2.
- Acceptance criteria : table `activity_events` en ajout seul (aucune modification ni suppression par l'application hors purge planifiée) ; champs : horodatage, acteur, rôle, action, type et identifiant de ressource, projet, résultat (autorisé, refusé, erreur), identifiant de requête ; couvre au minimum : connexion, ouverture et fin de session, consultation de document, lancement et arrêt d'agent, exclusion posée ou levée, création de creator, arrêt et effacement de projet, changement de politique LLM ; un refus d'accès est un événement ; aucun contenu de document, de message ni de secret dans un événement (test par valeur sentinelle).
- Contexte : deux sources : événements IAFActory et usage LLM (US11.6).
- Exemples : le viewer V tente d'ouvrir une session sur l'agent X exclu : événement `session.open`, résultat `refusé`, sans le contenu de la demande.
- Questions : adresse IP dans l'événement (donnée personnelle) ?

## US12.2 Historique d'activité consultable
- En tant qu'utilisateur, je consulte l'historique d'activité qui me concerne.
- Prérequis : US12.1.
- Acceptance criteria : page avec filtres (période, acteur, action, projet, résultat) et pagination ; visibilité par rôle : chacun voit ses événements, le creator ceux de ses projets, l'admin tout ; un événement hors de sa portée n'apparaît ni dans la liste ni dans le nombre de résultats ; export CSV pour l'admin.
- Exemples : le creator A ne voit aucun événement du projet du creator B.
- Questions : le creator voit-il les événements des viewers sur ses projets ?

## US12.3 Tableau de bord d'usage simple
- En tant qu'admin ou creator, je vois l'usage en comptages simples.
- Prérequis : US12.1, US11.6.
- Acceptance criteria : activité par jour, par agent, par utilisateur ; refus et erreurs ; jetons et coût par consommateur et par modèle ; périodes 24 h, 7 jours, 30 jours ; le creator ne voit que ses projets ; chaque graphique repose sur une vue SQL nommée et stable.
- Contexte : volontairement simple ; le passage à Grafana réutilisera les vues (US12.5).
- Exemples : le tableau affiche 42 sessions hier, dont 3 refusées, et 310 000 jetons pour l'agent « Analyste contrats ».

## US12.4 Confidentialité et rétention
- En tant qu'admin, je maîtrise ce qui est conservé et combien de temps.
- Prérequis : US12.1.
- Acceptance criteria : durée de rétention configurable avec purge planifiée journalisée ; le contenu des prompts n'est stocké ni dans le journal d'activité ni dans les journaux de dépense de la passerelle (réglage à identifier dans la doc LiteLLM et testé par valeur sentinelle) ; suppression d'un utilisateur : ses événements sont anonymisés selon une règle documentée ; accès aux journaux réservé (jamais modifiable par un creator).
- Questions : durée de rétention par défaut ; obligations légales applicables (à qualifier).

## US12.5 Préparer Grafana
- En tant qu'équipe, je veux pouvoir brancher Grafana plus tard sans changer le modèle de données.
- Prérequis : US12.3.
- Acceptance criteria : rôle Postgres en lecture seule limité aux vues de reporting ; documentation des vues ; aucune donnée sensible dans ces vues ; le service Grafana n'est pas déployé dans ce périmètre.
- Statut : différé (décision de l'utilisateur : « à terme »).
