---
title: Introduzione
description: Cos’è, per chi è, e cosa non fa.
---

%name% è un disco remoto condiviso per gruppi su macOS che lavorano con file grandi. Compare nel Finder come **%name%**, come un disco esterno, e i file stanno in un object storage S3 che scegli tu. Una cache locale su ogni Mac rende i file che hai già aperto veloci come un SSD interno.

## Per chi è

Piccoli gruppi e studi che vogliono un posto solo per il loro lavoro: gli stessi file su ogni Mac, senza cartelle da sincronizzare e senza un abbonamento per postazione. È pensato per il video, dove le clip sono grandi e la timeline salta da un punto all’altro, e va bene anche per servizi fotografici, progetti di design e 3D, audio e archivi condivisi. Chi lo usa non deve sapere niente di bucket o database: apre il Finder e lavora.

Non va bene per migliaia di file minuscoli scritti tutti insieme, per computer con Windows o Linux, o per lavorare del tutto offline; [Costi e alternative](/it/costs/) dice cosa scegliere invece.

## Cosa fa

- **Monta un disco** in `%volume%`. Qualsiasi programma apre i file direttamente, e il percorso resta lo stesso: i progetti che puntano alle clip con il percorso completo continuano a trovarle.
- **Fa streaming da S3.** I file sono salvati in blocchi da 1 MB. Riprodurre o saltare in un punto scarica solo i blocchi di quel punto, e durante la riproduzione una breve lettura anticipata scarica quello che viene dopo.
- **Tiene una cache** sull’SSD del Mac. Quello che apri resta lì, e **Rendi disponibile** precarica un progetto intero prima di iniziare a montare. **Libera spazio** restituisce il posto.
- **Condivide un disco per gruppo.** L’albero dei file sta in un Valkey centrale, e ogni Mac ne tiene una replica: le cartelle si aprono senza aspettare la rete, e un file salvato su un Mac compare subito sugli altri.

## Cosa non fa

- **Non salva offline.** Senza rete il disco resta sfogliabile e le clip in cache si riproducono, ma salvare, rinominare e cancellare falliscono con "read-only file system" finché la connessione non torna. Le clip che non sono in cache non si possono leggere.
- **Non ha permessi per cartella.** Tutti nel gruppo vedono e possono cancellare ogni file. Il cestino (7 giorni di default) e la replica del bucket coprono gli errori.
- **Non è un backup.** Il bucket va replicato su un secondo provider; vedi [Self-hosting](/it/self-hosting/).

## Requisiti

- Un Mac con Apple Silicon e macOS 13 o successivo, con [Homebrew](https://brew.sh).
- Nessun diritto di amministratore e nessuna estensione del kernel: %name% esegue il filesystem in una piccola VM Linux. [Come funziona](/it/how-it-works/) spiega perché.
- Un gruppo che ospita il proprio control plane, Valkey e bucket S3, o ne usa uno gestito.

Prossimo passo: [installare su un Mac](/it/install/).
