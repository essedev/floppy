# Floppy

[English](README.md) · **Italiano**

Un disco remoto condiviso per chi monta video su macOS: compare nel Finder come un volume qualsiasi, i file stanno su object storage S3, e una cache locale rende i clip già usati veloci come su un SSD locale.

## Perché esiste

Montare video da un bucket nel cloud non funziona con un semplice mount di rete: una timeline legge pezzi sparsi di file grandi, e ogni lettura paga la latenza verso il datacenter. I filesystem in streaming con cache locale (LucidLink, SpaceFS) risolvono il problema, con un prezzo per utente. Floppy costruisce la stessa cosa con componenti open source, sullo storage che scegli tu: [JuiceFS](https://juicefs.com) divide i file in blocchi su S3 e li tiene in cache su ogni Mac. In self-hosting non ha un prezzo per utente: un team paga lo storage e un piccolo server per i metadati, qualunque sia il numero di persone.

Su macOS JuiceFS richiederebbe l'estensione del kernel di macFUSE, cioè avviare ogni Mac in modalità di recupero. Floppy fa girare JuiceFS in una piccola VM Linux ([Lima](https://lima-vm.io), con il framework di virtualizzazione di Apple) e la condivide al Mac via SMB su un collegamento privato: nessuna estensione del kernel, nessun permesso di amministratore.

```
Finder  ->  SMB (private Mac-VM link)  ->  Samba + JuiceFS in a Lima VM  ->  S3 bucket
                                            cache on the Mac's SSD           metadata: SQLite in the VM (test)
                                                                                       or PostgreSQL (shared)
```

## Installazione

Floppy si distribuisce come cask di Homebrew: l'app della barra dei menu con il motore dentro, così l'installazione non scarica niente oltre all'app.

```bash
brew install --cask essedev/floppy/floppy
```

Il tap (`essedev/homebrew-floppy`) non è ancora pubblicato. L'app si costruisce con `make app` (`scripts/build-app.sh`), che vuole Docker acceso perché il JuiceFS patchato si compila e i pacchetti Debian della VM si scaricano in container. Produce `dist/app/Floppy.app` (circa 555 MB: Lima, l'immagine Debian, JuiceFS con le patch di Floppy da `spike/juicefs-replica`, i pacchetti Debian) e il DMG che il cask scarica (circa 410 MB). Al primo avvio l'app installa il motore in `~/.floppy` e prepara il disco; dopo un aggiornamento propone l'aggiornamento del motore. `scripts/release-app.sh` pubblica una release e il cask (`packaging/homebrew`).

Finché la versione per i team non configura il disco dall'accesso, chi prova riceve uno zip costruito da una configurazione compilata, con lo stesso motore:

```bash
cp macos/config.env.example my-test.env   # bucket, chiavi, SMB_PASSWORD
scripts/package.sh my-test.env            # crea dist/floppy.zip
```

Lo zip (circa 445 MB) contiene anche le chiavi del bucket: va passato in privato. Sul Mac si scompatta e si lancia:

```bash
bash floppy/install.sh
```

Serve un Mac con Apple Silicon e macOS 13 o successivo, ci vogliono circa 90 secondi, e il disco compare nel Finder come **Floppy**.

## Uso

```bash
~/.floppy/bin/floppy status          # VM, disco, spazio libero del Mac, cache in uso
~/.floppy/bin/floppy warmup <path>   # rende un progetto disponibile sul Mac
~/.floppy/bin/floppy free <path>     # lo toglie dalla cache
~/.floppy/bin/floppy stop | start
~/.floppy/bin/floppy uninstall       # rimuove VM, cache e agente
```

Un agente launchd controlla ogni 2 minuti che VM, JuiceFS e Samba siano attivi e che il disco sia montato, e svuota la cache se il Mac resta senza spazio. La dimensione della cache si calcola dallo spazio libero del Mac a ogni setup.

Nella versione per i team, senza un `config.env`, l'app nella barra dei menu guida lo stesso comando: scrive in `~/.floppy/disks.json` i dischi che la persona può vedere, passa i segreti a `floppy disks apply`, e ogni disco compare come una cartella di **Floppy**, in sola lettura o in lettura e scrittura; `floppy disks clear` li toglie tutti da un Mac revocato. Il contratto è in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#team-disks).

## Stato

Agli inizi: un pacchetto di test per macOS, provato su OVH Object Storage a Milano, con i metadati in SQLite dentro ogni VM, quindi ogni Mac ha il suo disco. La versione per i team è in costruzione: il control plane (`server/`, accesso, persone, dispositivi, dischi, cambio della chiave, modi dello storage) e la sua dashboard (`web/`) funzionano dall'inizio alla fine su uno stack locale; l'app nella barra dei menu (`app/`) ha l'interfaccia ma non è ancora collegata; il motore della versione per i team (una replica Valkey e un mount JuiceFS per disco nella VM, i dischi come cartelle) funziona su uno stack locale e aspetta che l'app lo guidi. Misure, decisioni e alternative sono in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), i piani in [docs/ROADMAP.md](docs/ROADMAP.md).

## Licenza

[MIT](LICENSE)
