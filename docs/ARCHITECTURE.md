# Architettura

Fluix è un disco remoto condiviso per il team che monta video: sul Mac compare come un volume nel Finder, i file stanno su object storage S3, e chi lavora non deve sapere altro. Questo documento raccoglie l'analisi fatta finora e le decisioni che ne sono uscite. È un progetto in fase di ipotesi: niente è ancora installato.

Prezzi e caratteristiche dei servizi di terzi sono rilevati al 06/10/2026 e vanno riverificati prima di decidere.

## Principio guida

**Il più semplice possibile, e funzionale.** Ogni componente in più deve risolvere un problema osservato, non ipotizzato. Per questo si parte da un pilota senza app né automazioni (vedi [ROADMAP.md](ROADMAP.md)) e si costruisce solo ciò che il pilota dimostra necessario.

## Chi lo usa

- Più persone, su Mac, non tecniche.
- Montano con **CapCut** e strumenti simili, non con suite professionali: niente proxy, niente project server, niente collaborazione gestita dal programma di montaggio.
- Il girato è soprattutto da telefono (HEVC/H.264, 4K a circa 50-100 Mbps).
- Quello che vogliono è uno solo: **un disco che compare sul Mac** e su cui i file ci sono.

## Perché non basta un disco di rete classico

Montare dal cloud con SMB, NFS o un mount S3 semplice funziona male per due motivi fisici:

- **Latenza**: verso un datacenter europeo ci sono 20-30 ms. Lo scrubbing di un video fa letture casuali continue, e ogni lettura paga quel ritardo.
- **Banda**: una fibra da 1 Gbps porta al massimo circa 110 MB/s, una FTTC molto meno. L'upload è spesso il collo di bottiglia per caricare il girato.

S3 poi non è un filesystem: va bene per copiare file interi, non per leggerne pezzi sparsi come fa una timeline. Serve un **filesystem in streaming** con cache locale, cioè la categoria di LucidLink.

## Soluzione: JuiceFS su OVH

JuiceFS (Community Edition, open source) ha la stessa architettura di LucidLink: i dati stanno a blocchi su S3, i metadati (nomi, cartelle, dimensioni) in un database condiviso, e ogni Mac tiene una cache locale.

| Componente | Scelta | Ruolo |
|---|---|---|
| Dati | Bucket OVH Object Storage Standard, versioning attivo | I blocchi dei file |
| Metadati | PostgreSQL, preferibilmente gestito da OVH, nella stessa regione | Albero di file e cartelle, lock |
| Client | `juicefs` + macFUSE su ogni Mac | Il volume nel Finder e la cache |

**Regione**: dati e metadati il più vicino possibile a chi monta. Ogni operazione su file e cartelle è un giro verso il database: con 1-3 ms il disco sembra locale, con 30 ms no. Da verificare se OVH offre Object Storage e PostgreSQL gestito a Milano.

**Perché OVH**: circa 8 $/TB al mese e traffico in uscita gratuito in Europa. Per il video l'uscita conta più dello spazio, perché ogni clip non in cache si riscarica.

**Perché PostgreSQL gestito**: è il componente critico (vedi Backup) e uno gestito porta backup e PITR senza un server da curare. Coerente col principio guida. Il prezzo va verificato; l'alternativa è una VPS a circa 15-30 €/mese.

## La cache

La cache è una cartella sull'SSD interno di ogni Mac, gestita dal client JuiceFS. È ciò che rende il disco veloce.

- JuiceFS divide ogni file in blocchi da 4 MB. Quando un programma legge un punto di un video, scarica solo i blocchi di quel punto e ne tiene una copia in cache. La volta dopo arrivano dall'SSD.
- **Come ci entrano i blocchi**: leggendo, con il prefetch che scarica in avanti durante la riproduzione, con `juicefs warmup <cartella>` che precarica un progetto intero, e scrivendo (chi carica il girato lo ha già in cache, i colleghi no).
- **Restano anche dopo il riavvio**, finché: la cache raggiunge `--cache-size` (si eliminano i blocchi usati meno di recente), lo spazio libero del Mac scende sotto `--free-space-ratio` (default 10%, la cache non riempie mai il disco), scade `--cache-expire` se impostato, oppure si liberano a mano con `juicefs warmup --evict <cartella>`. I blocchi di un file cancellato spariscono subito sul Mac che lo cancella; sugli altri diventano orfani e vengono eliminati per primi.
- **Ogni Mac ha la sua cache**: ciò che ha visto un collega non aiuta gli altri.
- **Non funziona offline**: anche con i blocchi in cache, l'albero dei file sta nel database. Senza rete il disco non si usa.
- **Mai dati vecchi**: un file modificato viene scritto in blocchi nuovi, quelli in cache restano solo inutilizzati.

## Prestazioni attese

Stime per una fibra da 1 Gbps in download e 300 Mbps in upload, da confermare nel pilota.

| Operazione | Fluix | Chiavetta USB 3 |
|---|---|---|
| Lettura di un file in cache | 2-3 GB/s (SSD del Mac) | 100-400 MB/s |
| Lettura di un file non in cache | circa 50-100 MB/s | 100-400 MB/s |
| Primo accesso o salto nella timeline | circa 0,1-0,3 s | istantaneo |
| Copia di 10 GB sul disco | circa 5 min (300 Mbps up), circa 15 min (100 Mbps up) | 1-2 min |

- Un video 4K da telefono chiede circa 7-8 MB/s: anche senza cache la riproduzione scorre.
- Il primo salto su un clip mai aperto ha un attimo di attesa; il warmup della cartella prima di iniziare lo elimina.
- La prima apertura di una cartella con molti video è lenta, perché Finder e CapCut leggono un pezzo di ogni file per le anteprime.
- **La connessione decide tutto**: con fibra FTTH l'esperienza è vicina a un disco esterno, con FTTC (100/20 Mbps) è frustrante, soprattutto in caricamento.

## Configurazione

| Impostazione | Valore | Motivo |
|---|---|---|
| Compressione | spenta | Il video è già compresso |
| Dimensione blocchi | 4 MB (default) | Bilancia letture sequenziali e salti |
| Cestino | 7 giorni | Recupero di cancellazioni accidentali |
| `writeback` | spento | Un file risulta salvato solo quando è su S3. Copie più lente, ma nessun file rimasto solo su un Mac chiuso a metà caricamento |
| `--cache-size` | 100-200 GB | Ragionevole su un Mac da 512 GB |
| `--cache-expire` | 30 giorni | I progetti vecchi si liberano da soli |
| `--prefetch`, `--buffer-size` | alti (buffer 1-2 GB) | Riproduzione e scrubbing |
| Cache dei metadati | timeout più lunghi | Meno giri verso il database dal Finder |
| macFUSE | `noappledouble`, `noapplexattr` | Niente file `._*` e attributi inutili |
| Spotlight | `mdutil -i off` sul volume | Niente indicizzazione che legge tutto |

## Flusso di lavoro con CapCut

- **I media stanno su Fluix, le bozze di CapCut restano sul Mac**: CapCut salva i progetti nella sua cartella locale e richiama i video dal percorso in cui stanno.
- **Una bozza ha un solo proprietario**: due persone non possono aprire la stessa bozza. Per passarsi un progetto si usa lo spazio cloud di CapCut oppure l'export.
- **Una cartella per progetto**, con dentro il girato e gli export. Niente struttura imposta oltre questa finché il pilota non ne mostra il bisogno.
- **Da verificare nel pilota**: come reagisce CapCut a un video lento ad arrivare. Se va in errore o perde il collegamento al file, il warmup prima di montare diventa obbligatorio.

## Interfaccia

JuiceFS Community non ha alcuna interfaccia per l'utente finale: è un programma da terminale. Chi monta vede solo un volume nel Finder.

**Fase 1, pilota**: Finder più due Azioni rapide nel menu del tasto destro, "Rendi disponibile" (`juicefs warmup`) e "Libera spazio" (`juicefs warmup --evict`), e un agente launchd che monta il disco all'accesso.

**Fase 2, solo se il pilota lo giustifica**: un'app nella barra dei menu. È la parte che i prodotti commerciali fanno pagare. Le funzioni candidate, ognuna legata a un problema da osservare:

| Problema | Funzione |
|---|---|
| Il disco non compare dopo uno stop o un cambio di rete | Montaggio all'accesso, stato connesso/non connesso, Riconnetti |
| Posso chiudere il Mac? | File in caricamento e avviso prima dello spegnimento (serve solo con `writeback` attivo) |
| Questo clip scatta | Quanto di un progetto è già sul Mac, "Rendi disponibile" con avanzamento |
| Il Mac è pieno | Spazio occupato dalla cache, liberazione per progetto |
| Credenziali in chiaro su ogni Mac | Accesso con SSO e credenziali personali revocabili |
| Non va e nessuno sa perché | "Invia diagnostica" con log e stato |

Forma prevista: app Swift nativa (SwiftUI `MenuBarExtra`) con il binario `juicefs` incluso, più un piccolo backend che verifica l'accesso su un SSO e rilascia le credenziali. Vincoli non eliminabili: account Apple Developer (99 $/anno) per firma e notarizzazione, e macFUSE che resta un'installazione separata con permessi di amministratore. Effort stimato, congettura: 3-5 giorni per stato, montaggio, warmup e cache; 6-10 giorni con SSO e credenziali personali; più manutenzione a ogni aggiornamento di macOS o JuiceFS.

## Accessi e sicurezza

- Ogni Mac contiene le credenziali di database e bucket. **Non ci sono permessi per persona**: chi ha il disco vede e può cancellare tutto. Cestino e versioning mitigano le cancellazioni.
- **Uscita di una persona**: senza app, vanno ruotate le credenziali su tutti i Mac. Con l'app di fase 2, credenziali personali (un ruolo PostgreSQL e una chiave S3 per persona) si revocano singolarmente. I permessi restano comunque "tutto o niente".
- Se serve controllo per utente o per cartella, JuiceFS Community non lo offre in modo semplice; SpaceFS e LucidLink sì.

## Backup e resilienza

- **Il database dei metadati è il punto critico**: se si perde, si perde il filesystem, perché i blocchi su S3 da soli non si ricompongono in file. Servono PITR e un `juicefs dump` giornaliero conservato fuori dal database.
- **JuiceFS non è un backup**: il bucket va replicato su un secondo provider (Backblaze B2, circa 7 $/TB) con versioning.
- **Archivio**: i progetti chiusi escono da JuiceFS e vanno con `rclone` in un bucket separato, come file normali, leggibili anche senza il database.
- Monitoraggio minimo: un avviso se database o bucket non rispondono. JuiceFS espone metriche Prometheus.

## macFUSE

È il rischio tecnico principale lato Mac.

- Le versioni classiche usano un'estensione del kernel: su Apple Silicon richiedono di riavviare in modalità di recupero e ridurre la sicurezza di avvio, Mac per Mac.
- macFUSE 5 su macOS 26 ha un backend FSKit che non lo richiede. **Non è verificato che JuiceFS ci funzioni**: è il primo test del pilota.
- Ogni aggiornamento di macOS può rompere il montaggio.

## Alternative considerate

| Alternativa | Esito | Motivo |
|---|---|---|
| SSD esterno Thunderbolt | Scartata come soluzione di team | Velocità massima, ma non condivisa |
| Mount S3 semplice (rclone `--vfs-cache-mode full`) | Scartata | Nessun lock, rischio di sovrascritture tra persone; prefetch lineare; mount che si blocca dopo uno stop del Mac |
| Progetto attivo su SSD + `rclone sync` su S3 | Scartata per il team | Robusta per una persona sola, non dà un disco condiviso |
| NAS in ufficio a 10 GbE + sync su S3 | Da riconsiderare | È l'architettura giusta se il team lavora nello stesso ufficio: prestazioni locali, gestione più semplice |
| **SpaceFS Teams** | **Benchmark commerciale** | Stessa architettura, app e supporto inclusi. Startup giovane (pre-seed da 2,4 M$ ad agosto 2026), prestazioni non verificate da terzi, regione dei dati non dichiarata |
| LucidLink | Alternativa matura, più cara | Standard del settore. Starter 7 $/membro con 100 GB, Business 27 $/membro con 400 GB, spazio extra su preventivo |
| LucidLink Custom sul nostro bucket | Possibile | 32 £/TB di licenza più lo storage, circa 45 €/TB. OVH non è tra i provider qualificati |
| Suite Studios | Non valutata | Le fonti si contraddicono su cosa sia (filesystem montato o workstation in cloud) |

## Costi

**Storage S3** (IVA esclusa):

| Provider | Spazio | Uscita |
|---|---|---|
| OVH Object Storage Standard | circa 8 $/TB al mese (fonte terza) | gratuita in Europa |
| Backblaze B2 | 6,95 $/TB al mese | gratuita fino a 3 volte i dati salvati, poi 0,01 $/GB |
| Hetzner | 6,49 €/mese con 1 TB, poi 8,70 €/TB | 1 TB incluso, poi 1 €/TB |
| Cloudflare R2 | 15 $/TB al mese | gratuita |

Wasabi è escluso: ha cambiato i prezzi dal 01/07/2026 e il nuovo listino non è stato trovato.

**Confronto per un team di 3 persone** (stime):

| TB attivi | SpaceFS Teams (30 $/persona, 1 TB/persona in pool, extra 6-12 $ ogni 500 GB) | Fluix (storage OVH + database) |
|---|---|---|
| 3 TB | 90 $ | circa 40-55 € |
| 5 TB | 114-138 $ | circa 55-70 € |
| 10 TB | 174-258 $ | circa 95-110 € |

Il fai-da-te costa circa la metà, ma la differenza (circa 500-1.800 € l'anno con 3-5 persone e pochi TB) va confrontata con il tempo di setup, di gestione e, se serve, di sviluppo dell'app. Fluix conviene con più persone o più TB, oppure se i dati devono stare su un'infrastruttura nostra.

## Fonti

- [SpaceFS](https://spacefs.com/), [RuntimeWire su SpaceFS](https://runtimewire.com/article/space-streams-cloud-files-directly-into-premiere-photoshop-and-other-mac-apps), [IT Brief UK](https://itbrief.co.uk/story/space-raises-usd-2-4-million-for-ai-native-filesystem)
- [LucidLink, listino G-Cloud 2026](https://assets.applytosupply.digitalmarketplace.service.gov.uk/g-cloud-15/documents/93417/461052283002657-pricing-document-2026-01-30-1117.pdf), [Cutsio, prezzi LucidLink 2026](https://cutsio.com/blog/lucidlink-pricing-2026-plans-costs-hidden-fees), [Backblaze, LucidLink con B2](https://www.backblaze.com/docs/cloud-storage-integrate-lucidlink-with-backblaze-b2)
- [Shade, Suite Studios vs LucidLink](https://shade.inc/blog/suite-studios-vs-lucidlink)
- [Backblaze B2 pricing](https://www.backblaze.com/cloud-storage/pricing), [OVH S3 FAQ](https://docs.ovhcloud.com/de/guides/storage-and-backup/object-storage/s3-faq.md), [getdeploying OVH vs Backblaze](https://getdeploying.com/backblaze-vs-ovh), [Hetzner pricing 2026](https://agentdeals.dev/hetzner-pricing-2026), [Wasabi pricing FAQ](https://docs.wasabi.com/docs/may-2026-wasabi-pricing-faqs)
- [JuiceFS Q&A](https://juicefs.com/en/blog/usage-tips/juicefs-24-qas-for-beginners), [Pigsty, JuiceFS su PostgreSQL](https://pigsty.io/docs/juice/)
