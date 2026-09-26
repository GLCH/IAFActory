# ADR 0003 : connecteurs sécurisés

Statut : propose (2026-09-26). Rien de ce qui suit n'est implémenté.

## Contexte

Un connecteur donne à IAFActory un accès à une source externe. C'est la surface d'attaque la plus sensible : elle détient des identifiants, sort vers le réseau, et rapporte du contenu que personne n'a relu.

## Décisions proposées

1. **Secrets** : chiffrement par enveloppe. Une clé de données par connecteur, chiffrée par une clé maître fournie par secret Docker (fichier monté), jamais par variable d'environnement en clair dans l'image. Les identifiants ne sont jamais renvoyés par l'API après création (écriture seule), ni journalisés, ni placés dans un prompt. Interface `SecretStore` pour pouvoir passer plus tard à un coffre externe. Le choix du coffre (Vault, OpenBao ou autre) n'est pas fait ; les licences et versions sont à vérifier avant tout choix.
2. **Isolation** : chaque exécution de connecteur tourne dans un conteneur dédié, utilisateur non root, système de fichiers en lecture seule, sans accès aux magasins de données. Le connecteur ne parle qu'à la passerelle de connecteurs, qui écrit dans le stockage.
3. **Réseau sortant** : liste blanche d'hôtes par connecteur, imposée par la passerelle (proxy sortant). Résolution DNS puis blocage des adresses privées, loopback et métadonnées cloud (protection SSRF), y compris après redirection.
4. **Privilège minimal** : portée en lecture seule par défaut ; toute écriture vers la source est refusée sauf décision explicite documentée.
5. **Contenu non fiable** : tout document importé est une donnée. Pas d'exécution, pas d'instruction suivie depuis un document (injection de prompt) : les extraits sont délimités et l'agent n'a que les outils listés dans sa définition.
6. **Fichiers** : type vérifié par contenu et non par extension, taille plafonnée, empreinte sha256, analyse antivirus à décider.
7. **Audit** : chaque lecture, création, modification, rotation et révocation est journalisée avec l'acteur, le connecteur et le projet. Journal non modifiable par le creator.
8. **Cycle de vie** : révocation immédiate, rotation planifiable, expiration des jetons ; un connecteur orphelin (projet effacé) est détruit avec ses secrets.

## Ce qui reste à décider

- Quelles sources externes, dans quel ordre (US9.5) ?
- Coffre externe ou chiffrement applicatif seul pour la première version ?
- Antivirus : quel moteur, et coût de maintenance ?
- Où s'exécutent les connecteurs en production (même hôte, autre réseau) ?
