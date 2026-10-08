# Cicli

Log del lavoro fatto, il più recente in cima. I piani stanno in [ROADMAP.md](ROADMAP.md), il perché delle scelte in [ARCHITECTURE.md](ARCHITECTURE.md).

## 3. Seconda installazione della tester: DNS e log (08/10/2026)

- **Cosa è successo**: la creazione della VM è fallita perché il DNS dell'ufficio risponde SERVFAIL per `cloud.debian.org` (già causa del fallimento silenzioso del 6/10). Rilanciando, `install.sh` ha detto "Updating" su una VM mai finita, l'avvio si è bloccato e `lima.log` ha superato i 50 GB riempiendo il disco. Diagnosi della tester, verificata: i blocchi di errore finivano nel log. La copia di 9,7 GB del 6/10 era andata a 14 MB/s, il suo upload.
- **Correzioni**: immagine Debian e JuiceFS dentro lo zip (406 MB), VM creata dal file locale (verificato nel log di Lima); segnale di installazione completata, con VM a metà cancellata e ricreata; messaggi solo sul terminale, trap che scatta una volta, log accorciato oltre 50 MB, timeout di 5 minuti all'avvio.
- **Provato**: lo stato della tester riprodotto con il Lima vero e un'immagine irraggiungibile (VM a metà riconosciuta e ricreata, log senza blocchi di errore); installazione pulita dallo zip nuovo in circa 90 s, disco della VM di 35 GiB.
- **Trovato provando**: fd 3 ereditato dall'host agent di Lima teneva aperto il terminale dell'installazione; chiuso per limactl.
- **Consegna**: zip rifatto con `CACHE_SIZE_MB=15360` (cache fino a 15 GB, VM fino a circa 25 GB sul Mac) e passato alla tester, che ha reinstallato; il test è in corso ([ROADMAP.md](ROADMAP.md)). Cancellati dal bucket i volumi orfani `fluix-tester-883398/` e `fluix/`.

## 2. Il primo test di una collega e le difese sullo spazio (06/10/2026)

- **Incidente**: sul Mac della tester (macOS 27.0.1, 16 GB di RAM, 10 GB liberi su 228) il caricamento di 9,9 GB e l'uso di CapCut hanno riempito il disco del Mac. Il file della VM non è più cresciuto, il filesystem della VM è andato in errore, CapCut ha visto "disconnessione dal server" e ogni comando nella VM rispondeva `Input/output error`. I 9,9 GB erano già tutti su OVH. Anche la prima installazione era fallita in silenzio.
- **Causa**: il controllo di spazio di JuiceFS guarda la VM, non il Mac.
- **Correzioni**: tetto della cache calcolato sullo spazio del Mac e disco della VM dimensionato su quel tetto; agente ogni 2 minuti che riavvia VM e servizi, libera la cache col Mac quasi pieno e monta il disco se manca; `fstrim` dopo `free`; `status` che vede una VM rotta; errori che mostrano il log; aggiornamento esplicito in `install.sh`.
- **Trovati provando le correzioni**: dopo uno stop del Mac l'agente rimontava il disco come `Fluix-1`, che rompe i progetti CapCut; un agente impaziente smontava a forza un disco occupato da una copia; `systemctl is-active` su due unità non vedeva Samba fermo. Corretti e riprovati: 3 clip copiati con 5 controlli dell'agente in parallelo, identici byte per byte.
- **Per la tester**: reinstallazione pulita dopo aver liberato spazio; il volume orfano va cancellato dal bucket (fatto nel ciclo 3).

## 1. Dall'analisi al pacchetto di test (06/10/2026)

**Obiettivo**: capire se un disco remoto su S3 regge il montaggio video da Mac, e arrivare a qualcosa da far provare a una collega.

**Fatto**:

- **Analisi**: categoria dei filesystem in streaming (LucidLink, SpaceFS), scelta di JuiceFS su OVH, costi e alternative ([ARCHITECTURE.md](ARCHITECTURE.md)).
- **Spike locale** (`spike/`): Garage in Docker come S3, latenza e banda simulate con Toxiproxy, JuiceFS in un container Linux condiviso al Mac via SMB. JuiceFS direttamente sul Mac richiede l'estensione del kernel di macFUSE (FUSE-T e FSKit non bastano), la macchina Linux con SMB no.
- **Pacchetto macOS** (`macos/`, `scripts/package.sh`): VM Lima con backend `vz`, comando `fluix`, zip con `install.sh`, nessun permesso di amministratore. Un volume per Mac generato all'installazione, così lo stesso zip va a più persone.
- **Test su OVH Milano**: bucket `fluix-test` con un utente dedicato. Riproduzione fuori cache a 12 MB/s sulla linea di Simone, salto a freddo intorno al secondo, in cache come un disco locale. Corretti lungo la strada il punto di montaggio `Fluix-1` e la cache dei file caricati (`--cache-large-write`).
- **RAM della VM**: da 2.060 a 1.035 MB sul Mac, con `memory` a 1 GiB e `--buffer-size` a 256 MB, a parità di prestazioni.
- **Mockup del pannello** nella barra dei menu (`mockups/panel.html`), tre varianti A, B e C.

**Decisioni**: macchina Linux con SMB invece di macFUSE; solo macOS con lo strato del sistema isolato; zip privato invece di Homebrew per il test; app Tauri da Portsage invece di Swift, con una prima versione limitata a ciò che fa il comando. Motivi in [ARCHITECTURE.md](ARCHITECTURE.md).

**Prossimo passo**: il test sul Mac della collega ([ROADMAP.md](ROADMAP.md)).
