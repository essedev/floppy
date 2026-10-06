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
| Client | `juicefs` in una piccola macchina Linux su ogni Mac, condiviso al Mac via SMB su `localhost` | Il volume nel Finder e la cache |

**Perché una macchina Linux e non `juicefs` direttamente sul Mac**: vedi [macFUSE](#macfuse). Il Mac monta una cartella di rete SMB, cosa che sa fare da sé senza estensioni; JuiceFS gira in Linux, dove FUSE è nativo. Il prezzo è un componente in più su ogni Mac.

**Regione**: dati e metadati il più vicino possibile a chi monta. Ogni operazione su file e cartelle è un giro verso il database: con 1-3 ms il disco sembra locale, con 30 ms no. Da verificare se OVH offre Object Storage e PostgreSQL gestito a Milano.

**Perché OVH**: circa 8 $/TB al mese e traffico in uscita gratuito in Europa. Per il video l'uscita conta più dello spazio, perché ogni clip non in cache si riscarica.

**Perché PostgreSQL gestito**: è il componente critico (vedi Backup) e uno gestito porta backup e PITR senza un server da curare. Coerente col principio guida. Il prezzo va verificato; l'alternativa è una VPS a circa 15-30 €/mese.

## La cache

La cache sta sull'SSD di ogni Mac, dentro il disco della macchina Linux, gestita dal client JuiceFS. È ciò che rende il disco veloce.

- JuiceFS divide ogni file in blocchi da 4 MB. Quando un programma legge un punto di un video, scarica solo i blocchi di quel punto e ne tiene una copia in cache. La volta dopo arrivano dall'SSD.
- **Come ci entrano i blocchi**: leggendo, con il prefetch che scarica in avanti durante la riproduzione, con `juicefs warmup <cartella>` che precarica un progetto intero, e scrivendo, ma solo con `--cache-large-write`, che di default è spento: chi carica il girato lo ha già in cache, i colleghi no.
- **Restano anche dopo il riavvio**, finché: la cache raggiunge `--cache-size` (si eliminano i blocchi usati meno di recente), lo spazio libero del Mac scende sotto `--free-space-ratio` (default 10%, la cache non riempie mai il disco), scade `--cache-expire` se impostato, oppure si liberano a mano con `juicefs warmup --evict <cartella>`. I blocchi di un file cancellato spariscono subito sul Mac che lo cancella; sugli altri diventano orfani e vengono eliminati per primi.
- **Ogni Mac ha la sua cache**: ciò che ha visto un collega non aiuta gli altri.
- **Non funziona offline**: anche con i blocchi in cache, l'albero dei file sta nel database. Senza rete il disco non si usa.
- **Mai dati vecchi**: un file modificato viene scritto in blocchi nuovi, quelli in cache restano solo inutilizzati.

## Prestazioni misurate

**Su OVH Milano** (06/10/2026, pacchetto macOS con VM Lima, bucket `fluix-test`, linea di Simone: 170 Mbps in download, 167 in upload, 34 ms a riposo; OVH Milano risponde in circa 15 ms):

| Operazione | Misura |
|---|---|
| Copia sul disco di 2,7 GB | 113 s (23,9 MB/s, il limite della linea) |
| Lettura di un clip da 434 MB non in cache | 38 s (12,1 MB/s) |
| Lettura in cache | 0,8 s |
| Salto (lettura di 1 MB) non in cache | media 0,9 s, massimo 1,9 s |
| Salto in cache | 0-3 ms |
| Warmup di un clip da 434 MB | 30 s (14,3 MB/s) |

- **La riproduzione regge anche fuori cache**: 12,1 MB/s contro i 7,5 che chiede un clip a 60 Mbps.
- **Lo scrubbing su un clip mai aperto è lento**: quasi un secondo a salto. Con il warmup sparisce (3 ms). Il prossimo parametro da provare è un blocco più piccolo di 4 MB, che rende più economico il salto a freddo al prezzo di più richieste.
- SMB costa ancora: la linea dà circa 21 MB/s, il Mac ne riceve 12 a freddo.

**Spike locale** (simulazione, prima di OVH):

Misure dello spike del 06/10/2026 (`spike/bench.sh`): Mac con Apple Silicon, Garage in Docker come S3, latenza di 25 ms simulata con Toxiproxy, banda limitata con `--download-limit` e `--upload-limit` di JuiceFS. Clip di prova: 4K HEVC a 60 Mbps, 434 MB ciascuna, simili al girato da telefono.

| Operazione | Collegamento 100/300 Mbps | Collegamento 1 Gbps |
|---|---|---|
| Copia sul disco di 2,7 GB | 86 s (circa 31 MB/s, limite di upload) | non misurata |
| Lettura di un clip non in cache | 68 s (6,7 MB/s) | 14 s (33 MB/s) |
| Lettura di un clip in cache | 1,5 s (circa 290 MB/s) | uguale |
| Salto (lettura di 1 MB) non in cache | media 0,8 s, massimo 1,6 s | media 0,2 s, massimo 2,9 s |
| Salto in cache | media 2-5 ms | uguale |
| Warmup di un clip | 43 s | non misurata |

- **In cache è come un disco locale**: la cache sta nella macchina Linux e SMB su `localhost` regge circa 160-290 MB/s.
- **Fuori cache decide la linea**: un clip a 60 Mbps chiede 7,5 MB/s. A 1 Gbps la riproduzione non in cache ha 4 volte il margine; a 100 Mbps non regge (6,7 MB/s) e il warmup prima di montare diventa obbligatorio.
- **SMB costa metà della banda a freddo**: dentro la macchina Linux JuiceFS legge a 10,7 MB/s su 100 Mbps, via SMB il Mac ne riceve 6,7. Il client SMB di macOS tiene poche letture in volo; prefetch più alto aiuta poco (3: 5 MB/s, 8: 6,7 MB/s, 16: 6,3 MB/s).
- **Il salto a freddo paga il blocco intero**: leggere 1 MB in un punto nuovo scarica il blocco da 4 MB che lo contiene.
- Garage locale in Docker non è OVH: le misure isolano latenza e banda, non le prestazioni del servizio vero. Vanno ripetute su OVH.
- La prima apertura di una cartella con molti video è lenta, perché Finder e CapCut leggono un pezzo di ogni file per le anteprime. Non ancora misurata.

## Configurazione

| Impostazione | Valore | Motivo |
|---|---|---|
| Compressione | spenta | Il video è già compresso |
| Dimensione blocchi | 4 MB (default) | Bilancia letture sequenziali e salti |
| Cestino | 7 giorni | Recupero di cancellazioni accidentali |
| `writeback` | spento | Un file risulta salvato solo quando è su S3. Copie più lente, ma nessun file rimasto solo su un Mac chiuso a metà caricamento |
| `--cache-size` | 100-200 GB | Ragionevole su un Mac da 512 GB |
| `--cache-expire` | 30 giorni | I progetti vecchi si liberano da soli |
| `--cache-large-write` | attivo | Chi carica un girato di solito lo monta: i blocchi caricati restano in cache. Di default JuiceFS non li tiene |
| `--prefetch` | 8 | Misurato: il valore migliore a freddo via SMB |
| `--buffer-size` | 1 GB | Riproduzione e scrubbing |
| `--enable-xattr` | attivo | Samba ci salva i metadati del Finder; senza, la cancellazione dal Mac fallisce con "Operation not supported" |
| Cache dei metadati | timeout più lunghi | Meno giri verso il database dal Finder |
| Samba | modulo `fruit` (con `samba-vfs-modules`), firma e cifratura spente, I/O asincrono | Compatibilità col Finder; firma spenta e I/O asincrono portano la lettura in cache da 69 a 163 MB/s. Spente solo perché SMB resta su `127.0.0.1` |
| Samba, file nascosti | `veto files` su `.accesslog`, `.config`, `.stats`, `.trash` | I file interni di JuiceFS non compaiono nel Finder |
| Spotlight | `mdutil -i off` sul volume | Niente indicizzazione che legge tutto |

## Flusso di lavoro con CapCut

- **I media stanno su Fluix, le bozze di CapCut restano sul Mac**: CapCut salva i progetti nella sua cartella locale e richiama i video dal percorso in cui stanno.
- **Una bozza ha un solo proprietario**: due persone non possono aprire la stessa bozza. Per passarsi un progetto si usa lo spazio cloud di CapCut oppure l'export.
- **Una cartella per progetto**, con dentro il girato e gli export. Niente struttura imposta oltre questa finché il pilota non ne mostra il bisogno.
- **Da verificare nel pilota**: come reagisce CapCut a un video lento ad arrivare. Se va in errore o perde il collegamento al file, il warmup prima di montare diventa obbligatorio.

## Interfaccia

JuiceFS Community non ha alcuna interfaccia per l'utente finale: è un programma da terminale. Chi monta vede solo un volume nel Finder.

**Fase 1, pilota**: Finder più due Azioni rapide nel menu del tasto destro, "Rendi disponibile" (`juicefs warmup`) e "Libera spazio" (`juicefs warmup --evict`), eseguite dentro la macchina Linux, e il montaggio della cartella SMB all'accesso.

**Fase 2, solo se il pilota lo giustifica**: un'app nella barra dei menu. È la parte che i prodotti commerciali fanno pagare. Le funzioni candidate, ognuna legata a un problema da osservare:

| Problema | Funzione |
|---|---|
| Il disco non compare dopo uno stop o un cambio di rete | Montaggio all'accesso, stato connesso/non connesso, Riconnetti |
| Posso chiudere il Mac? | File in caricamento e avviso prima dello spegnimento (serve solo con `writeback` attivo) |
| Questo clip scatta | Quanto di un progetto è già sul Mac, "Rendi disponibile" con avanzamento |
| Il Mac è pieno | Spazio occupato dalla cache, liberazione per progetto |
| Credenziali in chiaro su ogni Mac | Accesso con SSO e credenziali personali revocabili |
| Non va e nessuno sa perché | "Invia diagnostica" con log e stato |

Forma prevista: **app Tauri** nella barra dei menu, sullo scheletro di Portsage (`~/Development/Projects/portsage`), che ha già icona e pannello nella barra dei menu, avvio all'accesso e un binario esterno incluso nell'app (`externalBin`). Swift scartato: non è nello stack di Acme e nessuno nel team lo manterrebbe; servirebbe solo per badge e menu nativi del Finder, che un'estensione Swift potrebbe aggiungere accanto all'app senza riscriverla. 

**Solo macOS per ora, con lo strato del sistema isolato.** L'interfaccia (stato, cache, warmup) è comune; cambia solo come gira JuiceFS, dietro un'interfaccia unica (monta, smonta, stato, warmup, libera spazio) con un modulo per sistema selezionato da `#[cfg(target_os)]`, come lo scanner di Portsage:

| Sistema | JuiceFS | Macchina Linux |
|---|---|---|
| macOS | in una VM Linux leggera, condiviso via SMB | sì, è l'unico modo senza estensione del kernel |
| Windows | `juicefs.exe` nativo con WinFsp, una lettera di unità | no |
| Linux | nativo con FUSE | no |

Il team lavora su Mac, quindi si costruisce solo quel modulo; Windows si aggiunge come modulo, non come riscrittura, quando serve. Da verificare allora la maturità del client Windows di JuiceFS e un canale di distribuzione al posto di Homebrew (installer o winget).

**Distribuzione con Homebrew**, come Portsage: un cask in un tap nostro, senza Apple Developer ID, con `xattr -cr` in postflight. Limiti: Homebrew va installato sui Mac dei colleghi (lo facciamo noi), gli aggiornamenti richiedono `brew upgrade`, e il trucco su app non firmate dipende dalle regole di Homebrew, che si stanno stringendo. L'accesso con SSO viene dopo.

Effort stimato, congettura: 3-4 giorni per stato, montaggio, warmup e cache partendo da Portsage, più la gestione della macchina Linux. Manutenzione a ogni aggiornamento di macOS o JuiceFS.

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

**JuiceFS sul Mac richiede l'estensione del kernel di macFUSE**, verificato nello spike su macOS 26.6 con JuiceFS 1.4.1 e macFUSE 5.4.0:

- **FUSE-T non funziona**: JuiceFS non usa libfuse ma parla direttamente col componente di macFUSE, e fallisce con `no FUSE mount utility found`.
- **Il backend FSKit di macFUSE non basta**: macFUSE lo supporta solo tramite libfuse3. Con `-o backend=fskit` il mount resta appeso e macOS chiede di abilitare le estensioni del kernel.
- **L'estensione del kernel** richiede, Mac per Mac, di spegnere, avviare in modalità di recupero e passare la sicurezza di avvio a "Ridotta". Per utenti non tecnici è l'opposto del principio guida.

Da qui la scelta della macchina Linux con SMB: nessuna estensione sul Mac. Nello spike era Docker Desktop, troppo pesante da chiedere a un montatore; nel pacchetto è una VM Lima (vedi [Pacchetto macOS](#pacchetto-macos)).

## Pacchetto macOS

Il modulo macOS, oggi un comando (`macos/bin/fluix`) e domani il motore dell'app Tauri. Si distribuisce come zip costruito da `scripts/package.sh <config.env>`, senza Homebrew; chi lo riceve lancia `bash install.sh` dal Terminale.

- **VM Lima 2.2.1**, binario ufficiale incluso nello zip e verificato con il checksum della release. Backend `vz` (framework di virtualizzazione di Apple), Debian 13, 2 CPU, 2 GiB di RAM, disco sparso da 200 GiB per la cache. Nessun permesso di amministratore: tutto vive in `~/.fluix`, con le VM in un `LIMA_HOME` proprio che non tocca altre installazioni di Lima.
- **Rete `vzNAT`**: la VM ha un indirizzo sul Mac (es. `192.168.64.2`) e SMB ci va diretto, senza l'inoltro di porte che nello spike con Docker costava banda. L'inoltro automatico di Lima è spento (`portForwards` con `ignore`), così la VM non espone porte sul Mac.
- **Dentro la VM**: `vm/guest/guest-install.sh` installa Samba e JuiceFS e due servizi systemd, il mount (`fluix-mount`, che formatta il volume al primo avvio) e `smbd`, che parte solo a mount pronto. Si rilancia senza danni per aggiornare.
- **Sul Mac**: il volume si monta con `osascript` (`mount volume`) e compare in `/Volumes/Fluix` e nella barra laterale del Finder; Spotlight è spento sul volume; un agente launchd (`dev.esse.fluix`) avvia VM e mount all'accesso.
- **Punto di montaggio**: di solito `/Volumes/Fluix`, ma se il nome è occupato (una cartella rimasta da uno smontaggio, un'altra condivisione con lo stesso nome) macOS usa `Fluix-1`. Il comando lo legge da `mount` invece di darlo per scontato.
- **Timeout SMB**: macOS fa fallire una lettura SMB dopo 30 s senza risposta. Il comando alza il limite a 600 s solo per l'indirizzo della VM, in un blocco suo dentro `~/Library/Preferences/nsmb.conf` (per utente, senza amministratore). Un errore di I/O è comparso una volta a 100 Mbps simulati, con una lettura a freddo lanciata subito dopo un caricamento di 2,7 GB; non si è ripresentato nel confronto A/B, il limite resta come protezione per le linee lente.
- **Configurazione**: `config.env` (modello in `macos/config.env.example`) con bucket, chiavi, password SMB tra Mac e VM, cache e limiti di banda simulati. Lo zip contiene le chiavi: si passa in privato, con una chiave OVH dedicata al bucket di test, da revocare a fine test.
- **Metadati del test**: SQLite dentro la VM. Disinstallare il pacchetto o perdere la VM rende illeggibili i file nel bucket; per più persone serve PostgreSQL (`META_URL`).
- **Peso**: la VM occupa circa 2,5 GB di RAM (buffer di JuiceFS e cache delle pagine di Linux) e sul disco cresce con la cache. Su un Mac da 8 GB va ridotto.
- **Installazione misurata**: 84-111 s, compreso il download dell'immagine Debian.
- **Prestazioni**: come lo spike con Docker a 100 Mbps simulati: lettura a freddo 5,3-7,2 MB/s, salto a freddo in media 0,8 s. A caldo le letture le serve anche la cache del client SMB di macOS.
- **Da fare**: le Azioni rapide nel Finder ("Rendi disponibile", "Libera spazio"); per ora sono `fluix warmup <percorso>` e `fluix free <percorso>`.

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
