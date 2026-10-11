# Roadmap

Il principio è quello di [ARCHITECTURE.md](ARCHITECTURE.md#principio-guida): si costruisce solo ciò che il passo precedente ha dimostrato necessario. Il lavoro già fatto sta in [CYCLES.md](CYCLES.md).

## 1. Domande aperte

Da chiudere prima del pilota, perché cambiano la scelta:

- **Quante persone montano e quanti TB attivi**: decidono tra SpaceFS e Fluix.
- **Dove lavorano**: stesso ufficio (allora NAS + sync su S3) o da posti diversi (filesystem in streaming).
- **Che connessione hanno**: con FTTC il progetto non regge, con FTTH sì.
- **Requisiti sui dati**: se il girato è di clienti, regione e GDPR decidono anche SpaceFS (regione non dichiarata).
- **Versioning del bucket di produzione**: spento nel test, da decidere ([ARCHITECTURE.md](ARCHITECTURE.md#soluzione-juicefs-su-ovh)).
- Da verificare: PostgreSQL gestito OVH a Milano; regione 1-AZ o 3-AZ per la produzione; prezzo dello spazio extra SpaceFS sul piano Teams.

## 2. Test sul Mac della tester (in corso)

Tester prova sul suo Mac (macOS 27) lo zip di `scripts/package.sh` rifatto l'08/10/2026: bucket `fluix-test`, VM da 1 GiB, `CACHE_SIZE_MB=15360`, quindi cache fino a 15 GB e VM fino a circa 25 GB sul Mac. Il disco della VM si fissa alla creazione: alzare il tetto della cache oltre quella misura vuol dire reinstallare. Ogni installazione ha il suo disco, con i metadati nella VM: si prova il disco sul suo Mac, con CapCut e la sua linea, non la condivisione.

- **Stato**: in corso, di base funzionante. La lista dei test: installazione, CapCut con export sul Mac e su Fluix, riapertura del progetto, stop e riavvio del Mac, progetto più grande della cache, cambio di rete, `fluix free`. Lo stop l'aveva già provato Simone, ed è da lì che è nato il fix di `Fluix-1`; riavvio vero e cambio di rete li prova lei.
- **Da annotare con lei**: cosa scatta, cosa va in errore, quanto ci mette a caricare.
- **Salti a freddo**: provare blocchi da 1 MB invece che da 4 su un secondo volume nello stesso bucket. Se conviene, si rifà lo zip.
- **A fine test**: revocare la chiave dell'utente di test e cancellare dal bucket il volume di prova di Simone (`fluix-doppia-<codice>`, con `bench/` e `test/`) e quello di Tester (`fluix-tester-<codice>`); in tutto il bucket si svuota, perché è solo di test. I volumi orfani delle installazioni fallite (`fluix-tester-883398/`, `fluix/`) sono già cancellati.

Aperti, da chiudere prima del pilota:

- **Prossimo zip per Tester**: i pacchetti Debian di Samba e FUSE ora sono nello zip e l'installazione non scarica più niente ([ARCHITECTURE.md](ARCHITECTURE.md#pacchetto-macos)). Lo zip che ha lei li scarica ancora da `deb.debian.org`: le modifiche si raccolgono e le si manda uno zip solo dopo il suo feedback, invece di farla reinstallare a metà test.
- **Password SMB nell'URL di montaggio**: passa in `mount volume` ed è visibile in `ps` per un istante ([CONVENTIONS.md](CONVENTIONS.md#segreti)). Va spostata nel Portachiavi di macOS.
- **macOS 27**: non ancora validato, lo dice la fine del test di Tester.

## 3. Pilota

Un disco condiviso, una o due settimane di lavoro reale in CapCut. Setup stimato in circa un giorno (congettura).

- PostgreSQL gestito in `META_URL`: lo zip diventa unico per tutti e chi lo installa vede lo stesso disco.
- Le due Azioni rapide nel Finder al posto di `fluix warmup` e `fluix free`.
- Da misurare: scrubbing su clip non in cache, caricamento del girato, comportamento di CapCut con file lenti, prima apertura di una cartella con molti video.
- Da annotare: ogni volta che qualcosa va storto, cosa e quando. È l'input della fase 6.

## 4. Confronto con SpaceFS

In parallelo al pilota, la prova di 7 giorni di SpaceFS Teams sullo stesso progetto e lo stesso test. Se SpaceFS regge, la differenza di prezzo va pesata contro il tempo di gestione di Fluix.

## 5. Rollout al team

Solo se il pilota regge e il confronto premia Fluix: installazione sui Mac in uno script, backup (dump giornaliero, replica su Backblaze), avviso se database o bucket non rispondono, una pagina di procedure per chi gestisce.

## 6. App nella barra dei menu

- **Subito, senza aspettare il pilota**: scegliere la variante del [mockup](ARCHITECTURE.md#interfaccia) e chiudere le decisioni che lascia aperte, poi lo scheletro Tauri da Portsage con il pannello collegato al comando `fluix`.
- **Dopo il pilota**: solo le funzioni per i problemi osservati, scelte dalla lista in [ARCHITECTURE.md](ARCHITECTURE.md#interfaccia).
