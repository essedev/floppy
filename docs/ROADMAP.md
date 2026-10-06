# Roadmap

Il principio è quello di [ARCHITECTURE.md](ARCHITECTURE.md#principio-guida): si costruisce solo ciò che il passo precedente ha dimostrato necessario.

## 1. Domande aperte

Da chiudere prima del pilota, perché cambiano la scelta:

- **Quante persone montano e quanti TB attivi**: decidono tra SpaceFS e Fluix.
- **Dove lavorano**: stesso ufficio (allora NAS + sync su S3) o da posti diversi (filesystem in streaming).
- **Che connessione hanno**: con FTTC il progetto non regge, con FTTH sì.
- **Requisiti sui dati**: se il girato è di clienti, regione e GDPR decidono anche SpaceFS (regione non dichiarata).
- Da verificare: Object Storage e PostgreSQL gestito OVH a Milano; JuiceFS con macFUSE 5 su backend FSKit; prezzo dello spazio extra SpaceFS sul piano Teams.

## 2. Pilota

Un Mac, una o due settimane di lavoro reale in CapCut, senza app. Setup stimato in circa un giorno (congettura).

- Bucket OVH, PostgreSQL, `juicefs format` con la configurazione di ARCHITECTURE.md.
- Sul Mac: macFUSE, `juicefs`, agente launchd, due Azioni rapide nel Finder.
- Da misurare: installazione di macFUSE, scrubbing su clip non in cache, caricamento del girato, comportamento di CapCut con file lenti, cosa succede dopo uno stop del Mac o un cambio di rete.
- Da annotare: ogni volta che qualcosa va storto, cosa e quando. È l'input della fase 4.

## 3. Confronto con SpaceFS

In parallelo al pilota, la prova di 7 giorni di SpaceFS Teams sullo stesso progetto e lo stesso test. Se SpaceFS regge, la differenza di prezzo va pesata contro il tempo di gestione di Fluix.

## 4. Rollout al team

Solo se il pilota regge e il confronto premia Fluix: installazione sui Mac in uno script, backup (dump giornaliero, replica su Backblaze), avviso se database o bucket non rispondono, una pagina di procedure per chi gestisce.

## 5. App nella barra dei menu

Condizionale: solo per i problemi osservati nel pilota, scelti dalla lista in [ARCHITECTURE.md](ARCHITECTURE.md#interfaccia).
