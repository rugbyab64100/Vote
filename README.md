# Bot Discord — Vote pour rôle temporaire

## Fonctionnalités
- Commande slash `/punition`.
- Paramètres : membre cible, rôle, nombre de votes « Pour », durée du vote en minutes, durée du rôle en minutes.
- La cible ne peut ni voter pour ni voter contre.
- Un utilisateur ne peut voter qu'une fois par vote.
- Le rôle est attribué dès que le nombre requis de votes « Pour » est atteint.
- Retrait automatique du rôle à l'expiration, avec échéances conservées dans SQLite.

## Déploiement sur FadeHost
1. Téléversez les fichiers `bot.py` et `requirements.txt` dans un dépôt GitHub.
2. Dans FadeHost, choisissez **Bots → Host a bot → Discord bot → Your own repository**.
3. Sélectionnez le dépôt GitHub et le plan gratuit.
4. Collez le token dans le champ prévu à cet effet. FadeHost le fournit au programme sous le nom `DISCORD_TOKEN`.
5. Configurez un stockage persistant pour SQLite. Si FadeHost fournit le répertoire `/data`, laissez `DB_PATH` à `/data/vote_role_bot.sqlite3`.
6. Déployez le bot.

## Permissions Discord
Le bot a besoin de :
- Manage Roles
- View Channels
- Send Messages
- Embed Links
- Read Message History (pas indispensable pour les boutons, mais utile)

Le rôle du bot doit être placé au-dessus du rôle temporaire et au-dessus du rôle le plus élevé des membres ciblés, sinon Discord bloquera l'attribution.

## Sécurité
- Ne mettez jamais le token dans GitHub ou dans ce fichier.
- Saisissez-le uniquement dans le champ sécurisé de l'hébergeur.
- Si le token est divulgué, réinitialisez-le depuis le portail développeur Discord.

## Utilisation
Exemple :
`/punition membre:@Joueur role:@RôleTemporaire votes_positifs:5 duree_vote:10 duree_role:30`

Le vote nécessite 5 votes favorables dans les 10 minutes. Si le seuil est atteint, le rôle est attribué pendant 30 minutes.

## Notes
- Les commandes slash peuvent prendre un court délai pour apparaître après le premier démarrage.
- La vue d'un vote en cours n'est pas restaurée si le processus redémarre pendant ce vote. Les échéances des rôles déjà attribués sont, elles, conservées dans SQLite si le stockage utilisé est persistant.
- Un vote terminé sans atteindre le seuil requis ne donne pas le rôle.
