# Convenzioni

Questo file è il **delta** sul canone Acme: le regole trasversali stanno in `CONVENTIONS.md` e valgono anche qui. Il canone non è ancora copiato in `docs/canon/`: il target va aggiunto in `canon/scripts/canon_targets.toml` e riallineato con `make sync-canon`.

Il canone è scritto per Python e TypeScript. Il codice di oggi è bash e configurazione (Lima, systemd, Samba): le regole sotto ne sono la traduzione. L'app Tauri, quando arriva, segue il canone per TypeScript e lo scheletro di Portsage per Rust.

## Script bash

- **`set -euo pipefail`** in testa a ogni script. Un comando che può fallire legittimamente si scrive esplicito (`|| true` con un commento sul perché), mai lasciato ingoiare l'errore in silenzio.
- **Errori con `die`**: un messaggio che dice cosa fare, sullo stderr, e uscita diversa da zero. Niente stack di `echo` di debug lasciati nel codice.
- **Idempotenti**: `setup`, `guest-install.sh` e `spike/setup.sh` si rilanciano senza danni. Un passo che crea qualcosa controlla prima se c'è già.
- **Percorsi assoluti per gli strumenti di sistema** macOS (`/usr/sbin/diskutil`, `/usr/bin/osascript`, `/sbin/mount`): sotto launchd il PATH è ridotto.
- **Nessuno stato dato per scontato**: punto di montaggio, indirizzo della VM e stato della VM si leggono dal sistema a ogni uso, non si salvano in variabili globali.
- **Le funzioni prima, il dispatch in fondo**: `cmd_<nome>` per ogni sottocomando, un `case` finale che li chiama.
- Controllo minimo: `bash -n <script>`. Il repo non ha ancora `shellcheck` né un `make check`.

## Configurazione

- **Ogni opzione nuova** di `config.env` va in `macos/config.env.example` con un commento, e ha un default sensato nello script che la legge (`${NOME:-default}`).
- **I valori misurati** (memoria della VM, buffer, prefetch) portano nel commento accanto il numero che li giustifica e rimandano a `docs/ARCHITECTURE.md`.

## Segreti

Mai un segreto in un file tracciato, in un argomento di riga di comando visibile in `ps` o in un output. Si passano via file con permessi `600` o via stdin (es. `curl -K -`).

Eccezione nota, da chiudere: la password SMB tra Mac e VM passa nell'URL di `mount volume` (`macos/bin/fluix`, `mount_share`) e nello spike in quello di `mount_smbfs`, visibile in `ps` per un istante. Vale solo sul collegamento privato Mac-VM; la strada è il Portachiavi di macOS.
