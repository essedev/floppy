# CLAUDE.md

Fluix è il disco remoto condiviso del team video di Acme: su un Mac compare nel Finder come **Fluix**, i file stanno su object storage S3 (OVH) attraverso JuiceFS. Su macOS JuiceFS gira in una VM Lima (Debian) e arriva al Mac via SMB, perché montato direttamente richiederebbe l'estensione del kernel di macFUSE. Oggi il prodotto è un comando bash (`macos/bin/fluix`) distribuito in uno zip privato; l'app Tauri nella barra dei menu è in progetto (`mockups/panel.html`).

Repo privato. Prosa dei doc in italiano; codice, commenti e commit in inglese.

## Glossario

- **Disco / Fluix**: quello che l'utente vede nel Finder. **Volume**: il filesystem JuiceFS dietro, con un nome (`VOLUME_NAME`) e i suoi metadati. Un volume per Mac nel test, uno condiviso con PostgreSQL dopo.
- **Metadati**: albero di file e cartelle di JuiceFS. Nel test stanno in SQLite **dentro la VM** (`/var/lib/fluix/meta.db`): perdere la VM rende illeggibili i dati nel bucket.
- **Cache**: i blocchi già scaricati, sul disco della VM. Nella UI si chiama "disponibile su questo Mac"; **warmup** = "Rendi disponibile" (`fluix warmup`), **evict** = "Libera spazio" (`fluix free`).
- **A freddo / a caldo**: letture di blocchi non in cache (passano dalla rete) o già in cache.
- **Spike**: lo stack Docker in `spike/` (Garage come S3 locale, Toxiproxy per la latenza, JuiceFS in un container). Serve a misurare senza rete vera; non è il prodotto.
- **L'utente**: chi monta video con CapCut, non tecnico. **Simone**: chi sviluppa e decide.

## Comandi

```bash
scripts/package.sh my-test.env [dist]    # zip per un tester: Lima + comando + VM + config (contiene le chiavi)
bash fluix/install.sh             # dal pacchetto scompattato: installa in ~/.fluix e monta
~/.fluix/bin/fluix status  # setup|start|stop|status|warmup <path>|free <path>|uninstall

# Applicare modifiche del repo all'installazione di questo Mac (tiene VM e volume)
cp macos/bin/fluix ~/.fluix/bin/ && cp macos/vm/guest/* ~/.fluix/vm/guest/ \
  && ~/.fluix/bin/fluix setup

# Dentro la VM
export LIMA_HOME=~/.fluix/vms
~/.fluix/lima/bin/limactl shell --workdir / fluix <cmd>   # es. sudo journalctl -u fluix-mount

# Spike locale
spike/setup.sh                 # Garage + Toxiproxy + container drive, segreti in spike/.env
spike/bench.sh                 # upload, letture a freddo/caldo, salti, warmup
```

Non ci sono comandi di qualità: gli script si controllano almeno con `bash -n`.

## Gotcha

**Mai `juicefs mount` direttamente su macOS.** Con FUSE-T fallisce (`no FUSE mount utility found`), con il backend FSKit di macFUSE resta appeso, e l'unica via che funziona è l'estensione del kernel, che obbliga ogni Mac a passare dalla modalità di recupero. JuiceFS gira in Linux (la VM), il Mac monta SMB.

**Il punto di montaggio non è fisso.** macOS usa `/Volumes/Fluix-1` quando il nome è occupato (cartella rimasta da uno smontaggio, un'altra condivisione `Fluix`), ed è successo in questa forma: ogni comando cercava `/Volumes/Fluix` e falliva.
- Sbagliato: `VOLUME=/Volumes/Fluix; dd if="$VOLUME/x"`
- Giusto: `mount_point` in `macos/bin/fluix`, che legge `/sbin/mount`.

**Percorsi assoluti per gli strumenti di sistema.** In alcuni shell e sotto launchd `/usr/sbin` non è nel PATH: `diskutil` "non trovato" ha fatto fallire in silenzio i rimontaggi di un benchmark. Si scrive `/usr/sbin/diskutil`, `/usr/bin/osascript`, `/sbin/mount`.

**Non disinstallare né cancellare la VM per "ripartire puliti".** `fluix uninstall` e `limactl delete` buttano i metadati: i dati nel bucket restano ma non si leggono più, e una reinstallazione con lo stesso `VOLUME_NAME` sullo stesso bucket si scontra con quelli vecchi. Per applicare modifiche si rilancia `setup`.

**`setup` non cambia la RAM di una VM già creata.** `memory` in `lima.yaml` vale solo alla creazione; su una VM esistente serve `limactl edit --memory <GiB> fluix` a VM ferma. Il resto (`vm/guest/`, config) `setup` lo riapplica.

**JuiceFS dietro Samba vuole `--enable-xattr`.** Senza, Samba (modulo `fruit`, che richiede il pacchetto `samba-vfs-modules`) non salva i metadati del Finder e cancellare un file dal Mac dà `Operation not supported`.

**JuiceFS non tiene in cache i blocchi caricati** se non con `--cache-large-write` (default spento). I doc lo davano per scontato ed era falso.

**macOS abbandona una lettura SMB dopo 30 s** e il Finder riceve un errore di I/O. Il comando alza `max_resp_timeout` solo per l'indirizzo della VM, in un blocco `# fluix begin/end` di `~/Library/Preferences/nsmb.conf`: non riscrivere quel file per intero, contiene configurazione dell'utente.

**Simulare la banda con i limiti di JuiceFS, non con Toxiproxy.** Il `bandwidth` di Toxiproxy vale per connessione e JuiceFS ne apre molte: 2,7 GB sono "passati" a 150 MB/s con un limite di 37 MB/s. Toxiproxy serve solo per la latenza; la banda è `--download-limit`/`--upload-limit` (`DOWNLOAD_LIMIT_MBPS`, `UPLOAD_LIMIT_MBPS`).

**Docker ha esaurito le subnet predefinite** su questo Mac (molti progetti compose): lo spike fissa una subnet esplicita in `spike/docker-compose.yml`. Non si fa `docker network prune`, le reti sono di altri progetti.

**Una UI si verifica in un browser vero.** Il mockup passava i controlli in node e nel browser disegnava una variante su tre per un errore JavaScript. Si carica in Chrome headless (Playwright con `executablePath` di Chrome) e si guarda lo screenshot.

## Segreti

`spike/.env`, `my-test.env`, `~/.fluix/config.env` e ogni `dist/*.zip` contengono chiavi S3 e password SMB: sono fuori da git (`*.env`, `dist/`) e non si stampano mai, nemmeno in un log di debug. Per controllare un file si mostrano i nomi delle chiavi, non i valori. `package.sh` crea lo zip con `umask 077`. Le chiavi S3 di OVH aprono ogni bucket del progetto: per un tester serve un utente dedicato.

## Tocca tutte le superfici

Una modifica al comportamento del disco passa quasi sempre da più file:

- `macos/bin/fluix` (lato Mac) e `macos/vm/guest/` (lato VM: `fluix-mount`, `guest-install.sh`, `smb.conf`, il servizio systemd).
- `macos/vm/lima.yaml` se cambia la VM, e allora il gotcha su `setup` qui sopra.
- `macos/config.env.example` se nasce un'opzione nuova in `config.env`.
- `macos/install.sh` e `scripts/package.sh` se cambia cosa finisce nello zip.
- `docs/ARCHITECTURE.md`: sezioni "Configurazione" e "Pacchetto macOS", e "Prestazioni misurate" se cambia un numero.
- Lo zip già consegnato non si aggiorna da solo: va ricostruito e rimandato.

Stati inversi: `setup` scrive il blocco in `nsmb.conf` e l'agente launchd, `uninstall` li toglie; `warmup` ha `free`; `start` ha `stop`. Chi aggiunge un effetto sul Mac dell'utente aggiunge la sua rimozione in `uninstall`.

## Da non compromettere

- **Nessun permesso di amministratore** per installare e usare il disco, e nessuna estensione del kernel: è il motivo per cui esiste la VM.
- **SMB solo sul collegamento privato Mac-VM** (indirizzo vzNAT), con l'inoltro porte di Lima spento: firma e cifratura SMB sono disattivate perché il traffico non esce dal Mac.
- **Niente dati dell'utente cancellati senza conferma**: `uninstall` chiede di scrivere `delete`, e i dati nel bucket si toccano solo su richiesta esplicita.

## Precedenza

Queste sono regole di default per questo repo: il `CLAUDE.md` globale vale sotto, quello che Simone dice in chat vale sopra tutto.

## Dove trovare il resto

- [README.md](README.md): cos'è e come si costruisce e consegna lo zip.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): perché JuiceFS, VM e SMB, configurazione, prestazioni misurate, alternative scartate, costi.
- [docs/CONVENTIONS.md](docs/CONVENTIONS.md): convenzioni di codice, delta sul canone Acme.
- [docs/ROADMAP.md](docs/ROADMAP.md): domande aperte e prossimi passi.
- [docs/CYCLES.md](docs/CYCLES.md): cosa è stato fatto e quando.
