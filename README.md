# Fluix

Disco remoto condiviso per il team video di Acme: sul Mac compare come un volume nel Finder, i file stanno su object storage S3 (OVH) attraverso JuiceFS, con una cache locale che rende veloci i file già usati.

Stato: spike locale fatto, pacchetto di test per macOS pronto, in attesa del bucket OVH.

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): analisi, soluzione, alternative considerate, costi.
- [docs/ROADMAP.md](docs/ROADMAP.md): domande aperte, pilota, passi successivi.

## Pacchetto di test

```bash
cp macos/config.env.example my-test.env   # compila bucket, chiavi, SMB_PASSWORD
scripts/package.sh my-test.env            # crea dist/fluix.zip
```

Chi lo riceve scompatta lo zip e lancia `bash fluix/install.sh` dal Terminale: il disco compare nel Finder come Fluix. Comandi: `~/.fluix/bin/fluix status|start|stop|warmup|free|uninstall`. Lo zip contiene le chiavi del bucket: si passa in privato.
