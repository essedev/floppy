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

- **Un dominio.** Gli indirizzi dei dischi hanno un solo certificato wildcard se il DNS è presso un provider che Traefik sa aggiornare via API (sotto sono documentati Cloudflare e OVH, va bene qualsiasi provider supportato da Traefik), oppure un certificato per disco senza nessuna credenziale DNS.
- **Un server SMTP** per i link di accesso.
- **Uno storage S3** presso qualsiasi provider, un bucket per disco, nella stessa regione del server, con il versioning e, dove il provider lo offre, l’Object Lock (vedi [Il bucket di un disco](#il-bucket-di-un-disco)).

## Come ci arrivano i Mac

Tutto passa dalla porta 443, quella che le reti degli uffici lasciano aperta:

- `https://disk.example.com` è il pannello di amministrazione, con l’API sotto `/api/`.
- `<etichetta>.meta.disk.example.com` sulla stessa porta è il Valkey di un disco. Traefik legge il nome nell’handshake TLS e manda la connessione a quel disco; l’etichetta è casuale, quindi il nome di un disco non compare né nel DNS né nei registri dei certificati.

Il TLS termina su Traefik, che rinnova anche i certificati; dietro, il traffico resta sulla rete privata del server.

## Installazione con Dokploy

1. **DNS**: fate puntare `disk.example.com` e `*.meta.disk.example.com` al server (record A, e AAAA se ha IPv6). Basta che siano aperte le porte 80 e 443.
2. **Traefik**: in Dokploy, sotto Web Server, Traefik, modificate la configurazione, aggiungete i blocchi di `deploy/traefik/traefik.yml` e ricaricate Traefik. I timeout di Traefik, e le altre applicazioni che serve, restano come sono: le connessioni ferme ai dischi non vengono chiuse. I certificati dei dischi arrivano in uno di due modi:
   - **Un solo wildcard**, con la challenge DNS: aggiungete entrambi i blocchi, il resolver e l’indirizzo da cui il servizio meta pubblica le sue rotte, e in Modify Environment le credenziali del provider DNS. **Cloudflare**: `CF_DNS_API_TOKEN`, un token limitato alla zona con Zone DNS Edit e Zone Read. **OVH**: `OVH_ENDPOINT=ovh-eu`, `OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET` e `OVH_CONSUMER_KEY`, con una consumer key autorizzata a `POST` e `DELETE` su `/domain/zone/*`.
   - **Un certificato per disco**, senza credenziali DNS nel Traefik condiviso: aggiungete solo il blocco delle rotte e impostate `FLOPPY_META_CERT_SCOPE=host` e `FLOPPY_META_CERT_RESOLVER=letsencrypt` (il resolver di Dokploy). Un disco nuovo ha il suo certificato, con la challenge HTTP, pochi secondi dopo la creazione; la sua etichetta casuale compare nei registri dei certificati e non dice niente del disco.
3. **Il servizio**: create un servizio Compose da %repo% con percorso `deploy/docker-compose.yml`, tipo Docker Compose. Lasciate vuota la scheda Domains e spente le Isolated Deployments: le rotte sono nel file.
4. **Variabili**: copiate `deploy/.env.example` nella scheda Environment e compilatelo: il dominio, i segreti generati, l’SMTP, la sottorete di `dokploy-network` come proxy fidato, e la prima organizzazione con l’indirizzo del suo amministratore e l’endpoint S3 predefinito.
5. **Deploy.** Il primo avvio prepara il database e crea l’organizzazione; il suo amministratore accede da `https://disk.example.com` con il link che arriva per email, poi crea i dischi e invita le persone.
6. **Backup**: nella scheda Backups del compose aggiungete un backup del database del servizio `db` (database `floppy`, utente `floppy_owner`) e un backup del volume `meta-data`, tutti e due giornalieri verso una destinazione S3 presso un altro provider. Il backup del volume gira con il servizio acceso (vedi [Durabilità e backup](#durabilità-e-backup)).

Senza Dokploy, lo stesso file gira con `docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build` su qualsiasi server il cui Traefik legga le label Docker e abbia gli stessi due blocchi.

## Il bucket di un disco

In modalità manuale ogni disco ha un bucket e una chiave che create presso il provider e incollate quando create il disco. La chiave sta nell’albero dei file del disco, che ogni Mac che può vedere il disco replica: contatela come nota a tutti loro. Preparate il bucket in modo che questa chiave possa fare il lavoro del disco e niente che distrugga la cronologia.

1. **Create il bucket** nella regione del server con versioning e Object Lock. L’Object Lock si può attivare solo quando il bucket viene creato. Poi impostate la conservazione predefinita in **modalità governance, 7 giorni**: ogni blocco resta almeno così a lungo, e le cancellazioni di JuiceFS diventano segni di cancellazione.
2. **Aggiungete la regola di scadenza** di `deploy/s3/lifecycle.json`: le versioni vecchie scadono 7 giorni dopo essere state sostituite o cancellate, così i blocchi cancellati non restano a pagamento per sempre.
3. **Create un utente per il disco** e associategli la policy di `deploy/s3/disk-policy.json`, con il nome del bucket al posto di `BUCKET`. Permette quello che usa JuiceFS, solo su quel bucket, e vieta di cambiare versioning, regola di scadenza o Object Lock e di forzare la conservazione. Impostate la regola di scadenza prima della policy: dopo, la chiave del disco non può più.
4. **Verificate**, con la chiave dell’utente in un file con le righe `AWS_ACCESS_KEY_ID=` e `AWS_SECRET_ACCESS_KEY=` (lo script non la stampa mai):

   ```bash
   S3_ENDPOINT=https://s3.example.com AWS_REGION=<regione> deploy/s3/check-disk-bucket.sh <bucket> <file della chiave>
   ```

   Scrive, legge e cancella un oggetto di prova, poi prova quello che la policy deve rifiutare con richieste che, se passassero, non cambierebbero niente. Una policy nuova può metterci qualche minuto a diventare attiva: se all’inizio i rifiuti non arrivano, rilanciatelo.
5. **Create il disco** nel pannello di amministrazione con il bucket e la chiave dell’utente.

Quanto costa: un blocco che JuiceFS cancella o compatta resta a pagamento per altri 7 giorni. Cosa copre: un file cancellato resta 7 giorni nel cestino del disco; uno modificato torna dal backup notturno dei metadati; e nessuna chiave, nemmeno quella del disco, può eliminare un blocco nei suoi primi 7 giorni.

- **OVH** non ha un permesso separato per eliminare le versioni: `s3:DeleteObject` fa entrambe le cose, e a JuiceFS serve, quindi lì solo l’Object Lock protegge le versioni. Il pannello può mostrare la conservazione predefinita sbagliata; leggetela con `aws s3api get-object-lock-configuration`.
- **AWS**: aggiungete anche `s3:DeleteObjectVersion` ai divieti della policy.

## Sicurezza

- Il pannello di amministrazione manda una Content Security Policy stretta, HSTS, nessun referrer e rifiuta di essere incorniciato in altre pagine.
- Ogni Mac ha una sua password su ciascun disco che può vedere, in sola lettura o in lettura e scrittura; revocare un Mac o ridurre l’accesso di qualcuno ha effetto subito.
- I segreti che il control plane deve usare, come le chiavi S3 e la password SMTP, sono cifrati nel database con una chiave che vive solo nell’ambiente del server. Tenetene una copia fuori dal server.

## Durabilità e backup

- **L’albero dei file è il filesystem.** Senza, i blocchi su S3 non si ricompongono in file. Il Valkey di ogni disco scrive ogni modifica su disco prima di rispondere, e salva con sé i suoi utenti, quindi un riavvio del servizio meta riporta su ogni disco in pochi secondi.
- **Fate il backup di entrambi i volumi ogni giorno, fuori dal server.** Su Dokploy, il suo backup del database di `db` e il suo backup del volume `meta-data` (passo 6). Il backup del volume è valido anche con il servizio acceso: il Valkey di ogni disco ha scritto ogni modifica prima di rispondere, e le sue fotografie vengono scritte intere. Tenete fuori dal server anche `FLOPPY_SECRET_KEY`, altrimenti il backup del database non può decifrare le chiavi dei dischi.
- **Provate un ripristino una volta.** Dal backup del volume prendete la cartella di un disco sotto `instances/`, avviate `valkey-server` su di essa e montate il disco in sola lettura sul suo bucket con JuiceFS (`juicefs mount --read-only redis://<host>:<porta>/1`); i suoi file devono rileggersi. Lo stesso mount riporta indietro un file modificato dal backup di una notte.
- **Le repliche sui Mac non sono un backup**: seguono il server, cancellazioni comprese.
- **Neanche JuiceFS è un backup.** L’Object Lock copre una chiave usata male, non l’account del provider: prima che un bucket tenga l’unica copia del lavoro di un team, replicatelo ogni notte su un secondo provider, accanto al backup dei metadati della stessa notte.

Se il servizio meta si ferma, i dischi montati passano in sola lettura: la navigazione e le clip in cache continuano a funzionare dalla replica di ogni Mac, i salvataggi aspettano che torni. Se si ferma il control plane, per i dischi montati non cambia niente.
