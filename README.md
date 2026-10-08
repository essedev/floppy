# Fluix

Disco remoto condiviso per il team video di Acme: sul Mac compare come un volume nel Finder, i file stanno su object storage S3 (OVH) attraverso JuiceFS, con una cache locale che rende veloci i file già usati.

Stato: pacchetto di test per macOS sul bucket OVH di Milano, in prova sul Mac di una collega; mockup del pannello dell'app in `mockups/panel.html`.

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): analisi, soluzione, alternative considerate, costi.
- [docs/ROADMAP.md](docs/ROADMAP.md): domande aperte, test in corso, pilota, passi successivi.
- [docs/CYCLES.md](docs/CYCLES.md): il lavoro fatto.

## Pacchetto di test

```bash
cp macos/config.env.example my-test.env   # compila bucket, chiavi, SMB_PASSWORD
scripts/package.sh my-test.env            # crea dist/fluix.zip
```

Chi lo riceve scompatta lo zip e lancia `bash fluix/install.sh` dal Terminale: il disco compare nel Finder come Fluix. Comandi: `~/.fluix/bin/fluix status|start|stop|warmup|free|uninstall`. Lo zip (circa 400 MB, con dentro l'immagine Debian e JuiceFS) contiene le chiavi del bucket: si passa in privato. Il disco della VM si dimensiona alla creazione sul tetto della cache (`CACHE_SIZE_MB`, ridotto se il Mac ha poco spazio) e poi non cambia.
