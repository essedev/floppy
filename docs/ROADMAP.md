# Roadmap

Il principio è quello di [ARCHITECTURE.md](ARCHITECTURE.md#principio-guida): si costruisce solo ciò che il passo precedente ha dimostrato necessario.

## 1. Domande aperte

Da chiudere prima del pilota, perché cambiano la scelta:

- **Quante persone montano e quanti TB attivi**: decidono tra SpaceFS e Fluix.
- **Dove lavorano**: stesso ufficio (allora NAS + sync su S3) o da posti diversi (filesystem in streaming).
- **Che connessione hanno**: con FTTC il progetto non regge, con FTTH sì.
- **Requisiti sui dati**: se il girato è di clienti, regione e GDPR decidono anche SpaceFS (regione non dichiarata).
- Da verificare: Object Storage e PostgreSQL gestito OVH a Milano; prezzo dello spazio extra SpaceFS sul piano Teams.

## 2. Spike locale (fatto il 06/10/2026)

Garage in Docker come S3, latenza e banda simulate, JuiceFS in un container Linux condiviso al Mac via SMB; si riproduce con `spike/setup.sh` e `spike/bench.sh`. Esito: JuiceFS direttamente sul Mac richiede l'estensione del kernel, la macchina Linux con SMB no; in cache il disco è come uno locale, fuori cache serve una linea da 1 Gbps. Numeri in [ARCHITECTURE.md](ARCHITECTURE.md#prestazioni-misurate). Da provare ancora: CapCut sul disco.

Poi il pacchetto di test senza Docker: VM Lima, comando `fluix`, zip con `install.sh` ([ARCHITECTURE.md](ARCHITECTURE.md#pacchetto-macos)). Installato e misurato prima contro Garage, poi sul bucket `fluix-test` su OVH Milano ([numeri](ARCHITECTURE.md#prestazioni-misurate)). Pronto da dare alla collega.

## 3. Pilota

Un Mac, una o due settimane di lavoro reale in CapCut, senza app. Setup stimato in circa un giorno (congettura).

- Bucket OVH, PostgreSQL, `juicefs format` con la configurazione di ARCHITECTURE.md.
- Sul Mac: la macchina Linux con JuiceFS e Samba, la cartella SMB montata all'accesso, due Azioni rapide nel Finder.
- Da misurare: le stesse prove dello spike su OVH, scrubbing su clip non in cache, caricamento del girato, comportamento di CapCut con file lenti, cosa succede dopo uno stop del Mac o un cambio di rete.
- Da annotare: ogni volta che qualcosa va storto, cosa e quando. È l'input della fase 6.

## 4. Confronto con SpaceFS

In parallelo al pilota, la prova di 7 giorni di SpaceFS Teams sullo stesso progetto e lo stesso test. Se SpaceFS regge, la differenza di prezzo va pesata contro il tempo di gestione di Fluix.

## 5. Rollout al team

Solo se il pilota regge e il confronto premia Fluix: installazione sui Mac in uno script, backup (dump giornaliero, replica su Backblaze), avviso se database o bucket non rispondono, una pagina di procedure per chi gestisce.

## 6. App nella barra dei menu

Condizionale: solo per i problemi osservati nel pilota, scelti dalla lista in [ARCHITECTURE.md](ARCHITECTURE.md#interfaccia).
