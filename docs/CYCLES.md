# Cicli

Log del lavoro fatto, il più recente in cima. I piani stanno in [ROADMAP.md](ROADMAP.md), il perché delle scelte in [ARCHITECTURE.md](ARCHITECTURE.md).

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
