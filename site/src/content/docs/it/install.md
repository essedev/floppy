---
title: Installare su un Mac
description: Installare l’app con Homebrew, accedere e trovare il disco nel Finder.
---

## Prima di iniziare

- Un Mac con Apple Silicon e macOS 13 o successivo.
- [Homebrew](https://brew.sh) installato.
- Un invito dall’amministratore del gruppo: arriva per email.

## Installazione

```sh
brew install --cask %cask%
```

L’app non chiede la password di amministratore e non installa estensioni del kernel. Tutto quello che serve al disco (l’immagine della VM Linux, JuiceFS, i pacchetti) è dentro l’app, quindi non si scarica nient’altro.

L’app non è notarizzata da Apple: il cask le toglie il flag di quarantena e macOS la apre senza avvisi. Usa il nome completo qui sopra: dice anche a Homebrew di fidarsi di questo solo cask del tap.

## Primo avvio

1. Apri **%name%** dalla cartella Applicazioni. La sua icona compare nella barra dei menu.
2. Se il tuo gruppo ha un suo server di %name%, inserisci il suo indirizzo nella finestra di accesso.
3. Scegli **Accedi**: si apre il browser, inserisci la tua email e clicca il link che ricevi. L’app non chiede mai una password.
4. Scegli **Installa %name%**: l’app copia il motore del disco su questo Mac, crea la VM Linux e mostra l’avanzamento. Ci vuole circa un minuto e mezzo e non scarica niente.
5. Il disco compare nel Finder come **%name%**, sotto Posizioni, in `%volume%`.

## Aggiornamenti

```sh
brew upgrade --cask %cask%
```

Dopo l’aggiornamento riapri l’app. Quando la nuova versione porta un motore nuovo, il pannello lo dice e lo applica quando scegli **Aggiorna ora**: il disco si riavvia, quindi prima salva e chiudi i progetti aperti. I file, la cache e le impostazioni del disco restano come sono.

## Lavorare con il disco

- Apri i file direttamente dal Finder o dal programma di montaggio. Una clip mai aperta parte in meno di un secondo; una in cache, subito. Senza rete puoi ancora sfogliare il disco e riprodurre quello che è in cache, ma non salvare.
- Prima di montare un progetto grande, scegli **Rendi disponibile** sulla sua cartella nel pannello della barra dei menu: tutto il progetto entra in cache.
- Quando il Mac resta senza spazio, scegli **Libera spazio** sui progetti che non stai montando. I file restano sul disco e si riscaricano quando li apri.

## Disinstallare

```sh
brew uninstall --cask %cask%
```

Così si rimuove solo l’app. Il disco su questo Mac, con la sua VM e la cache in `~/.%slug%`, resta, e reinstallando l’app lo ritrovi com’era. Per rimuovere anche quello, d’accordo con l’amministratore del gruppo, c’è `~/.%slug%/bin/%slug% uninstall`, che chiede conferma.
