---
title: Introduzione
description: Cos’è, per chi è, e cosa non fa.
---

%name% è un disco remoto condiviso per chi monta video su macOS. Compare nel Finder come **%name%**, come un disco esterno, e i file stanno in un object storage S3 che scegli tu. Una cache locale su ogni Mac rende le clip che hai già aperto veloci come un SSD interno.

## Per chi è

Piccoli gruppi che montano video insieme e vogliono un posto solo per il girato: gli stessi file su ogni Mac, senza cartelle da sincronizzare e senza un abbonamento per postazione. Chi monta non deve sapere niente di bucket o database: apre il Finder e lavora.

## Cosa fa

- **Monta un disco** in `%volume%`. Qualsiasi programma apre i file direttamente, e il percorso resta lo stesso: i progetti che puntano alle clip con il percorso completo continuano a trovarle.
- **Fa streaming da S3.** I file sono salvati in blocchi da 4 MB. Riprodurre o saltare in un punto scarica solo i blocchi di quel punto, e durante la riproduzione il prefetch scarica in anticipo.
- **Tiene una cache** sull’SSD del Mac. Quello che apri resta lì, e **Rendi disponibile** precarica un progetto intero prima di iniziare a montare. **Libera spazio** restituisce il posto.
- **Condivide un disco per gruppo.** L’albero dei file sta in PostgreSQL, quindi ogni Mac vede gli stessi file un attimo dopo che sono stati salvati.

## Cosa non fa

- **Non funziona offline.** L’albero dei file è remoto: senza rete il disco non si può usare, nemmeno per le clip in cache.
- **Non ha permessi per cartella.** Tutti nel gruppo vedono e possono cancellare ogni file. Il cestino (7 giorni di default) e la replica del bucket coprono gli errori.
- **Non è un backup.** Il bucket va replicato su un secondo provider; vedi [Self-hosting](/it/self-hosting/).

## Requisiti

- Un Mac con Apple Silicon e macOS 13 o successivo, con [Homebrew](https://brew.sh).
- Nessun diritto di amministratore e nessuna estensione del kernel: %name% esegue il filesystem in una piccola VM Linux. [Come funziona](/it/how-it-works/) spiega perché.
- Un gruppo che ospita il proprio control plane, PostgreSQL e bucket S3, o ne usa uno gestito.

Prossimo passo: [installare su un Mac](/it/install/).
