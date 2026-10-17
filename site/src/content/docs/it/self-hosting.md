---
title: Self-hosting
description: Far girare un %name% tutto vostro su un server con Dokploy, con il vostro dominio e qualsiasi S3.
---

Tutto quello che serve a %name% gira su un’infrastruttura scelta da voi. Parla solo S3 standard e il protocollo Redis, che Valkey implementa: nessuna API di un provider fa parte del nucleo.

## Cosa si fa girare

Un solo file Docker Compose, `deploy/docker-compose.yml` nel repository, con quattro servizi:

- **Il control plane**: l’API che conosce organizzazioni, persone, Mac e dischi, manda i link di accesso e decide chi vede ciascun disco. Non tiene dati dei file, e i dischi montati continuano a funzionare anche quando è spento.
- **Il pannello di amministrazione**, servito dallo stesso indirizzo dell’API.
- **PostgreSQL 18**, per le tabelle del control plane.
- **Il servizio meta**, che fa girare un Valkey per ogni disco: l’albero dei file del disco, scritto da JuiceFS, con una sua replica su ogni Mac che può vedere quel disco. Una replica copia un Valkey intero, quindi ogni disco ha il suo e nessun Mac riceve mai un disco che non può vedere.

Oltre al server servono:

- **Un dominio** con il DNS presso un provider che Traefik sa aggiornare via API, per il certificato wildcard degli indirizzi dei dischi. Sotto sono documentati Cloudflare e OVH; va bene qualsiasi provider DNS supportato da Traefik.
- **Un server SMTP** per i link di accesso.
- **Uno storage S3** presso qualsiasi provider, un bucket per disco, nella stessa regione del server.

## Come ci arrivano i Mac

Tutto passa dalla porta 443, quella che le reti degli uffici lasciano aperta:

- `https://disk.example.com` è il pannello di amministrazione, con l’API sotto `/api/`.
- `<etichetta>.meta.disk.example.com` sulla stessa porta è il Valkey di un disco. Traefik legge il nome nell’handshake TLS e manda la connessione a quel disco; l’etichetta è casuale, quindi il nome di un disco non compare né nel DNS né nei registri dei certificati.

Il TLS termina su Traefik, che rinnova anche i certificati; dietro, il traffico resta sulla rete privata del server.

## Installazione con Dokploy

1. **DNS**: fate puntare `disk.example.com` e `*.meta.disk.example.com` al server (record A, e AAAA se ha IPv6). Basta che siano aperte le porte 80 e 443.
2. **Traefik**: in Dokploy, sotto Web Server, Traefik, modificate la configurazione e aggiungete i blocchi di `deploy/traefik/traefik.yml`: nessun timeout di lettura sulla porta 443 (le connessioni a Valkey restano aperte anche ferme), un resolver che ottiene il wildcard con la challenge DNS e l’indirizzo da cui il servizio meta pubblica le sue rotte. In Modify Environment aggiungete le credenziali del provider DNS, poi ricaricate Traefik.
   - **Cloudflare**: `CF_DNS_API_TOKEN`, un token limitato alla zona con Zone DNS Edit e Zone Read.
   - **OVH**: `OVH_ENDPOINT=ovh-eu`, `OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET` e `OVH_CONSUMER_KEY`, con una consumer key autorizzata a `POST` e `DELETE` su `/domain/zone/*`.
3. **Il servizio**: create un servizio Compose da %repo% con percorso `deploy/docker-compose.yml`, tipo Docker Compose. Lasciate vuota la scheda Domains e spente le Isolated Deployments: le rotte sono nel file.
4. **Variabili**: copiate `deploy/.env.example` nella scheda Environment e compilatelo: il dominio, i segreti generati, l’SMTP, la sottorete di `dokploy-network` come proxy fidato, e la prima organizzazione con l’indirizzo del suo amministratore e l’endpoint S3 predefinito.
5. **Deploy.** Il primo avvio prepara il database e crea l’organizzazione; il suo amministratore accede da `https://disk.example.com` con il link che arriva per email, poi crea i dischi e invita le persone.

Senza Dokploy, lo stesso file gira con `docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build` su qualsiasi server il cui Traefik legga le label Docker e abbia gli stessi tre blocchi.

## Sicurezza

- Il pannello di amministrazione manda una Content Security Policy stretta, HSTS, nessun referrer e rifiuta di essere incorniciato in altre pagine.
- Ogni Mac ha una sua password su ciascun disco che può vedere, in sola lettura o in lettura e scrittura; revocare un Mac o ridurre l’accesso di qualcuno ha effetto subito.
- I segreti che il control plane deve usare, come le chiavi S3 e la password SMTP, sono cifrati nel database con una chiave che vive solo nell’ambiente del server. Tenetene una copia fuori dal server.

## Durabilità e backup

- **L’albero dei file è il filesystem.** Senza, i blocchi su S3 non si ricompongono in file. Il Valkey di ogni disco scrive ogni modifica su disco prima di rispondere, e salva con sé i suoi utenti, quindi un riavvio del servizio meta riporta su ogni disco in pochi secondi.
- **Fate il backup di entrambi i volumi** (`db-data` e `meta-data`) e tenete fuori dal server un `juicefs dump` giornaliero di ogni disco.
- **Le repliche sui Mac non sono un backup**: seguono il server, cancellazioni comprese.
- **Neanche JuiceFS è un backup.** Replicate i bucket su un secondo provider, con il versioning attivo lì.

Se il servizio meta si ferma, i dischi montati passano in sola lettura: la navigazione e le clip in cache continuano a funzionare dalla replica di ogni Mac, i salvataggi aspettano che torni. Se si ferma il control plane, per i dischi montati non cambia niente.
