---
title: Installare su un Mac
description: Installare l’app con Homebrew, accedere e trovare il disco nel Finder.
---

:::caution[Non ancora pubblicato]
Il cask e l’app nella barra dei menu sono in costruzione. Fino alla prima versione, chi prova riceve uno zip costruito dal repository: vedi il [README](%repo%/blob/main/README.it.md#installazione).
:::

## Prima di iniziare

- Un Mac con Apple Silicon e macOS 13 o successivo.
- [Homebrew](https://brew.sh) installato.
- Un invito dall’amministratore del gruppo: arriva per email.

## Installazione

```sh
brew install --cask %cask%
```

L’app non chiede la password di amministratore e non installa estensioni del kernel.

## Primo avvio

1. Apri **%name%** dalla cartella Applicazioni. La sua icona compare nella barra dei menu.
2. Scegli **Accedi**: si apre il browser, inserisci la tua email e clicca il link che ricevi. L’app non chiede mai una password.
3. L’app crea la VM Linux su questo Mac e mostra l’avanzamento. Ci vuole circa un minuto e mezzo e non scarica niente: tutto quello che serve è nell’app.
4. Il disco compare nel Finder come **%name%**, sotto Posizioni, in `%volume%`.

## Lavorare con il disco

- Apri i file direttamente dal Finder o dal programma di montaggio. Una clip mai aperta parte dopo circa un secondo; una in cache, subito.
- Prima di montare un progetto grande, scegli **Rendi disponibile** sulla sua cartella nel pannello della barra dei menu: tutto il progetto entra in cache.
- Quando il Mac resta senza spazio, scegli **Libera spazio** sui progetti che non stai montando. I file restano sul disco e si riscaricano quando li apri.

## Disinstallare

```sh
brew uninstall --cask %cask%
```

TODO: non è ancora deciso se disinstallare il cask rimuove anche la VM e la cache, o se lo fa l’app dalle impostazioni.
