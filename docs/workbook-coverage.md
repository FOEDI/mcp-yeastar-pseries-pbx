# Couverture de l’onglet « CAS à traiter »

Cette matrice traduit les demandes du classeur `yeastar_export_CDR_mai_2026_pr_PowerBi_rapports__cas_d_usages__stats.xlsx` vers la surface MCP. Elle décrit la couverture du code actuel, validé sur des fixtures documentaires mais pas encore sur le PBX réel.

## Légende

- **Couvert** : le MCP calcule ou retourne directement la donnée.
- **Partiel** : la téléphonie est disponible, mais une règle métier ou une source externe est nécessaire.
- **Hors PBX** : la donnée n’existe pas de façon historique fiable dans les endpoints de lecture utilisés.

## Cas opérationnels

| Demande | État | Outils et règle d’usage |
|---|---|---|
| CAS 1.1 — appels « général » arrivant au mauvais service | **Partiel** | `get_ivr_analysis`, `get_routing_analysis`, `get_calls`, puis `get_call_details`. Les participants, IVR, queues, groupes et derniers participants sont disponibles dans le CDR v2.[1][2] La notion de « bon service » nécessite une matrice locale `SVI/touche/DID → service attendu`. |
| CAS 1.1 — premier choix SVI | **Couvert pour les choix documentés** | `get_ivr_analysis` agrège les touches et destinations du détail de rapport IVR.[3][4] |
| CAS 1.1 — nombre ayant tapé 1 et destination obtenue | **Couvert** | `get_ivr_analysis`; résultats agrégés par touche et destination. |
| CAS 1.1 — liste opérationnelle des appels ayant choisi 1 ou 2 | **Échantillon borné avec divulgation contrôlée** | `get_ivr_analysis(include_calls=true)` retourne au plus `max_call_examples` exemples, pas une liste exhaustive. Les numéros restent masqués sauf double opt-in. |
| CAS 1.2 — appels multi-segments, attente, boucle potentielle ou raccrochage | **Couvert avec qualification prudente** | `get_routing_analysis` calcule les appels multi-segments, routages longs, changements de destination et candidats de boucle; `get_call_details` donne les legs et métadonnées d’événements.[1][2] |
| CAS 1.2 — transfert aveugle versus accompagné | **Non garanti** | Les champs stables documentés permettent de parler de *transition* ou *changement de destination*, pas d’affirmer le type de transfert. Le MCP ne parse pas l’opaque `event_content`.[2] |
| CAS 2.0 — refusé, pas de réponse, occupé, échoué, messagerie, abandonné | **Couvert sauf “refusé”** | `get_call_stats`, `get_call_activity`, `get_calls`. `NO ANSWER`, `BUSY`, `FAILED`, `VOICEMAIL` et `ABANDONED` restent séparés. « Refusé » doit être défini comme règle métier. |
| CAS 2.0 — incohérences sur plusieurs mois | **Partiel** | `get_call_activity(bucket=month)`, `get_routing_analysis` et filtres de `get_calls`. La qualification « incohérence de procédure » nécessite des règles validées. |
| CAS 2.0 — exemples à 3/4 segments et attente longue | **Couvert** | `get_calls(segments=..., routing_duration=...)`, `get_routing_analysis`, puis détail borné. |
| CAS 2.1 — durée, boucles/rebonds, fin de parcours | **Couvert avec définitions explicites** | Durées exactes, segments, changement de destination, route finale, statut et partie ayant raccroché sont normalisés. Une boucle reste un *candidat* défini par la répétition de destination. |
| CAS 2.1 — raison précise de l’abandon/échec/messagerie | **Partiel** | Statut et `disconnected_by` sont exposés. Une cause métier plus précise ne doit pas être inventée lorsque Yeastar ne la fournit pas. |
| CAS 3.1 — contacts de l’annuaire passant par le SVI | **Couvert** | `get_contact_call_stats` joint localement l’annuaire société aux CDR et compte les appels avec/sans IVR.[1][5] |
| CAS 3.1 — contact qui aurait dû utiliser une ligne directe | **Partiel** | Ajouter un référentiel local `contact/segment → numéro direct attendu`; cette attente n’est pas déductible du CDR. |
| CAS 3.2 — conducteurs et clients clés | **Partiel** | Le rapprochement contact/appel est couvert. Les étiquettes « conducteur » et « client clé » doivent venir d’un phonebook Yeastar ou d’une liste métier validée.[5] |
| CAS 3.3 — collaborateur interne utilisant le SDA public d’un service interne | **Couvert si les SDA sont fournis** | `list_extensions`, puis `get_calls(call_to=<SDA>)`; filtrer les appels `Outbound` dont l’appelant est une extension interne.[1][6] |

## Tableaux de bord et statistiques

| Demande | État | Outils |
|---|---|---|
| STATS 00 — tableau de bord mensuel | **Couvert côté dataset MCP** | `get_call_activity(bucket=month)`, performances queues/agents/extensions/IVR/groupes. La création visuelle Power BI reste côté BI. |
| STATS 00 — M-1, trimestre, année, période libre | **Couvert** | Appels séparés avec périodes comparables; buckets jour/semaine/mois. Une période trop large est rejetée plutôt que tronquée. |
| STATS 00 — J-1 sur écran TV | **Couvert côté données** | `get_call_activity(bucket=hour)` et rapports queues. L’écran et le rafraîchissement Power BI sont externes au MCP. |
| STATS 00 — temps réel | **Hors V1** | Nécessite un collecteur persistant WebSocket/Webhook et du stockage; le serveur actuel reste local `stdio` et à la demande. |
| STATS 1 — nombre d’appels/choix par SVI | **Couvert** | `list_ivrs`, `get_ivr_analysis`. |
| STATS 1 — échecs, abandons, messageries | **Couvert** | `get_call_stats`, `get_call_activity`. |
| STATS 1 — routage supérieur à 60 secondes | **Couvert** | `get_routing_analysis(long_routing_seconds=60)` ou filtre `get_calls(routing_duration=">=60")`. `routing_duration` reste distinct de l’attente de queue officielle. |
| STATS 1 — renvoi vers autre service, sonnerie puis messagerie/raccrochage | **Couvert comme indicateurs de route** | `get_routing_analysis` et `get_call_details`; la destination métier nécessite le mapping service. |
| STATS 2 — volume/pourcentage retransféré | **Partiel** | Le MCP calcule les appels multi-segments et changements de destination. Il ne les nomme pas « transferts » sans preuve contractuelle.[1][2] |
| STATS 2 — temps avant changement de service | **Partiel** | Legs et temps écoulés sont disponibles par appel; le point de départ métier doit être défini. |
| STATS 3 — appels avec au moins N segments | **Couvert** | `get_routing_analysis(min_segments=N)` compte explicitement les appels atteignant le seuil et retourne des exemples bornés. Le filtre `segments` de `get_calls` n’est présenté que comme une égalité exacte documentée, pas comme un filtre de comparaison. |
| STATS 3 — routage supérieur à 30/45 secondes | **Couvert** | Seuil configurable dans `get_routing_analysis`; filtre exact disponible dans `get_calls`. |
| STATS 3 — routage très supérieur au temps de conversation | **Couvert après choix du seuil/ratio** | Le MCP expose `routing_seconds` et `handling_seconds`; ce dernier n’est pas présenté comme du temps de conversation. « Largement supérieur » doit être chiffré dans le rapport. |
| RAPPORT 3 — entrants/sortants/internes | **Couvert** | `get_call_activity` renvoie la dimension direction. |
| RAPPORT 4 — répartition par service | **Partiel** | Queues, IVR et groupes sont comptés. Une dimension « service » unifiée requiert une table de correspondance. |
| RAPPORT 5 — appels par collaborateur | **Couvert** | `list_extensions`, `get_extension_performance`; pour les agents de queue, `get_agent_call_summary`. |
| RAPPORT 6 — volume heure/jour/semaine/mois | **Couvert** | `get_call_activity(bucket=hour|day|week|month)`. |
| RAPPORT 7 — durée moyenne et classes 0–2, 2–4… 10+ minutes | **Couvert** | `get_call_activity(duration_band_seconds=120, duration_cap_seconds=600)`. |
| RAPPORT 8 — temps moyen avant décroché | **Couvert pour les queues; indicatif globalement** | `get_queue_performance`, `get_queue_wait_times`, ou routage CDR global. |
| RAPPORT 9 — temps moyen/cumulé en attente | **Couvert pour les valeurs documentées** | Les rapports queue exposent moyenne, maximum, cumul des appels répondus (`answered_waiting_time`) et cumul de tous les appels (`total_waiting_time`). Le routage CDR est une métrique distincte. |
| RAPPORT 10 — traitement moyen | **Couvert** | `get_queue_performance.average_service_seconds` et durées de traitement CDR. |
| RAPPORT 11 — taux perdus/abandonnés/non répondus | **Couvert** | Les catégories et taux queues restent séparés afin d’éviter un indicateur ambigu. |
| RAPPORT 12 — incidents téléphoniques mensuels | **Hors historique CDR** | Une vraie mesure exige syslog, supervision ou tickets historisés. Les erreurs d’appels peuvent seulement servir d’indicateurs, pas de décompte d’incidents. |
| RAPPORT 13 — temps en communication par collaborateur | **Couvert** | `get_extension_performance` retourne une ligne nommée par extension et le temps parlé; `communication_type` sépare entrant/sortant/interne.[3][6] |

## Référentiels métier encore nécessaires

Pour rendre les analyses « bon/mauvais routage » déterministes, fournir localement, sans les committer :

1. `DID/SDA → site et service`;
2. `IVR + touche → service attendu`;
3. `queue/IVR/groupe/extension → service`;
4. `contact ou phonebook → conducteur/client clé`;
5. `contact/segment → numéro direct conseillé`;
6. calendrier des changements et incidents validés.

## Confidentialité

- Les agrégations sont effectuées dans le MCP et les CDR sources ne sont pas retournés au modèle.
- Par défaut, les numéros sont masqués, les noms externes supprimés et les contacts rendus sous la forme `Contact <id>`.
- Les noms des agents et extensions internes restent disponibles pour les rapports de performance.
- Une donnée nominative exige simultanément `include_numbers=true` et `YEASTAR_ALLOW_RAW_NUMBERS=true`.
- Les exemples détaillés sont bornés; les analyses trop volumineuses échouent avec une demande de période plus étroite.

## Limites de validation

Les fixtures et le classeur prouvent que les champs utiles existent dans l’export observé, mais le test live reste à exécuter avec le firmware, les droits OpenAPI et la timeline du PBX cible. Les périodes couvrant l’ancienne et la nouvelle structure CDR doivent encore être interrogées séparément en v1/v2.

## Sources

[1] https://help.yeastar.com/en/p-series-software-edition/developer-guide/search-specific-cdr-v2.html
[2] https://help.yeastar.com/en/p-series-software-edition/developer-guide/query-cdr-detail-v2.html
[3] https://help.yeastar.com/en/p-series-software-edition/developer-guide/query-call-report-list.html
[4] https://help.yeastar.com/en/p-series-software-edition/developer-guide/query-call-report-detail.html
[5] https://help.yeastar.com/en/p-series-software-edition/developer-guide/query-company-contacts-list.html
[6] https://help.yeastar.com/en/p-series-software-edition/developer-guide/query-extension-list.html
