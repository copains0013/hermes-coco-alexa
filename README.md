# Hermès Coco — Alexa × Hermes × Home Assistant

Skill **fr-FR / Alexa-hosted Python**, conçue pour un usage privé. Le code est public ; la configuration et les identifiants ne le sont pas.

## Importer dans la console Alexa

1. Créer une skill nommée `Hermès Coco`, locale **French (FR)**.
2. Choisir **Other → Custom → Alexa-hosted (Python)**, pas Smart Home ni Node.js.
3. Dans Templates → Import skill, coller :
   `https://github.com/copains0013/hermes-coco-alexa.git`
4. Après création, Save / Build Model. Dans Code, vérifier le handler `lambda_function.lambda_handler` puis Deploy.
5. Dans Test, sélectionner Development, puis taper `ouvre hermès coco`.

Le message d’accueil et l’aide fonctionnent immédiatement. **Les questions réelles ne fonctionnent qu’après configuration de la liaison privée.** Le modèle ne reçoit pas toutes les paroles : les demandes utilisent une phrase porteuse (limitation Amazon.SearchQuery).

### Parler à Hermès

- « Alexa, ouvre Hermès Coco. »
- « Ma question est quelle est la capitale du Japon ? »
- « Continue. » → récupérer la réponse calculée sur Orange.
- « Ma question est et quelle langue parle-t-on dans ce pays ? » → contexte conservé.
- « Ma réponse est le salon. » → précision à une question d’Hermès.
- « Fais ceci active mon scénario du soir. » → seulement si un script correspondant est autorisé.
- « Oui / non. » → confirmer / annuler une action proposée.
- « Aide / stop. »

Les calculs sont asynchrones : Alexa ne reste **pas** à écouter indéfiniment. `Continue` récupère une réponse en attente **dans la même session Alexa**. Si Alexa ferme la session, rouvrir la skill n’y réattache pas la demande précédente. Pas de réponse spontanée différée ni de promesse de conversation libre sans préfixe.

## Liaison privée, distincte de l’import

Architecture : **Echo → Lambda Alexa → Cloudflare Access (Service Auth) → tunnel → serveur loopback Orange → Hermes / scripts HA autorisés**.

Aucune règle Cloudflare, aucun service existant et aucune automatisation Home Assistant ne sont modifiés par l’import ou par les tests.

### Sur Orange

- Python 3.11+ et Hermes CLI installé/authentifié.
- Vérifier `hermes chat --help` : `--query-file`, `--run-budget`, `--toolsets`, `--resume` et `--ignore-rules` sont requis.
- Installer les fichiers dans un répertoire dédié, par exemple `/opt/hermes-coco-alexa`.
- Créer un dossier `private` mode **0700** ; configuration, jeton de pont et éventuel jeton HA mode **0600**.
- Copier `orange/config.example.json` en `private/config.local.json` et remplacer les placeholders **localement, jamais dans le dépôt ni dans une conversation**.
- Générer le jeton du pont localement : `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'` dans une redirection vers le fichier privé sous `umask 077`. Ne pas montrer la sortie.
- Utiliser un jeton HA créé par le propriétaire dans son profil Home Assistant ; jamais extrait de `.storage/auth`.
- Démarrer : `python3 orange/server.py --config private/config.local.json`.
- L’origine écoute exclusivement sur **127.0.0.1:18764**. Toutes les routes utiles demandent le jeton du pont et un identifiant de compte Alexa autorisé. Ni page admin ni écoute LAN.
- Le fichier systemd est un **exemple**, pas un service activé. Adapter l’utilisateur, son HOME et le chemin du binaire Hermes avant installation. Ne pas remplacer le gateway Hermes.

### Dans Cloudflare

Créer une application Access dédiée au hostname du pont, politique **Service Auth**, avec un service token réservé à cette Lambda. Ensuite seulement, publier un tunnel vers `http://127.0.0.1:18764`. Ne jamais contourner Access pour résoudre un problème de connexion. Vérifier absence d’accès anonyme et conservation de toutes les autres routes. L’application Home Assistant n’a pas besoin d’être exposée à Alexa.

### Dans la skill importée (privée)

Ajouter un fichier **`private_config.json` à côté de `lambda_function.py`**, sur la console Alexa, à partir de `lambda/config.example.json` :

- URL HTTPS exacte du pont ;
- jeton aléatoire du pont ;
- identifiant et secret du service token Cloudflare Access ;
- ID exact de la skill (`amzn1.ask.skill.…`) ;
- liste des IDs de comptes Alexa autorisés (`amzn1.ask.account.…`).

Les IDs de compte se trouvent dans le JSON d’une requête de test de la console Alexa (`context.System.user.userId`). Ils ne sont **pas** le login Amazon, ni l’ID de l’Echo. Reporter le même ID dans la configuration privée du pont. Les secrets ne doivent jamais entrer dans Telegram, captures d’écran, commits ou ce dépôt public. Ne jamais synchroniser le fichier privé vers le dépôt d’import public.

## Actions autorisées : fermées par défaut

`actions: {}` signifie **aucune action activée**. Questions et précisions seules. Hermes n’a accès qu’aux outils web dans ce chemin ; il n’a pas le terminal, le système de fichiers ni l’outil de commande Home Assistant. La passerelle exécute uniquement un script explicitement choisi par l’administrateur, après confirmation vocale.

Exemple **fictif**, à adapter à un script HA réel préalablement vérifié :

```json
"actions": {
  "mode_soir": {"label": "Activer le mode soir du salon", "script": "mode_soir_salon"}
}
```

La passerelle appelle seulement `POST http://127.0.0.1:8123/api/services/script/<script_autorisé>` avec un corps vide. Pas de commande système, URL arbitraire, nom de service inventé par le modèle ni paramètres libres. Confirmation valable deux minutes. Une confirmation répétée du même job n’appelle pas le script deux fois. Un timeout est un **résultat incertain**, jamais une preuve d’échec et jamais une relance automatique. « Accepté par HA » ne prouve pas que l’appareil a physiquement bougé.

**Un compte Amazon n’est pas une identité vocale.** Toute personne près d’un Echo de ce compte peut potentiellement confirmer. Ne pas autoriser serrures, alarmes, paiements, suppressions ou actions dangereuses avec cette seule sécurité. Les clés Cloudflare protègent la liaison réseau, pas l’identité de la personne qui parle.

## Confidentialité et limites

- Les transcriptions passent par Amazon et par le fournisseur LLM configuré dans Hermes. Ce n’est pas une chaîne 100 % locale.
- Le serveur n’écrit pas de logs de transcriptions ni de secrets. Hermes peut néanmoins conserver son historique dans sa base de sessions locale : c’est un stockage distinct.
- Les jobs du pont restent en mémoire, expirent après 15 minutes et sont perdus à son redémarrage. La propriété d’une session Hermes est contrôlée par le pont, jamais acceptée sur un simple ID fourni par le client.
- Le token HA doit être protégé et révocable ; Home Assistant ne garantit pas forcément un scope restreint par script.
- Les requêtes d’Alexa doivent être authentifiées par la Lambda Alexa-hosted ; ce handler n’est **pas** un endpoint HTTP Alexa auto-hébergé vérifiant une signature Amazon.
- Les réponses ne sont pas instantanées : le moteur Hermes démarre un processus par tour, avec budget 75 secondes / timeout 100 secondes. Quatre questions maximum en file, 64 jobs maximum en mémoire.
- Les quotas/coûts Amazon, Cloudflare et fournisseur Hermes restent ceux du compte ; aucune activation de facturation n’est effectuée par ce projet.
- Pas de publication sur le store Amazon. La certification publique aurait ses propres exigences de confidentialité, compte et modèles.

## Vérification

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q lambda orange tests
```

Tests synthétiques de handlers, SSML, ownership, confirmations, idempotence, expiration, erreurs et réseau loopback réel. Ils ne simulent **pas** une validation réelle par Amazon ni une action physique de maison.

La structure correspond au format d’import Python officiel :

```
lambda/lambda_function.py
lambda/requirements.txt
skill-package/skill.json
skill-package/interactionModels/custom/fr-FR.json
```

Références officielles :
- [Import Alexa-hosted depuis Git](https://developer.amazon.com/en-US/docs/alexa/hosted-skills/alexa-hosted-skills-git-import.html)
- [AMAZON.SearchQuery et phrases porteuses](https://developer.amazon.com/en-US/docs/alexa/custom-skills/slot-type-reference.html)
- [Hermes CLI](https://hermes-agent.nousresearch.com/docs/user-guide/cli/)
