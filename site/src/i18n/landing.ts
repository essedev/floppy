// Strings of the landing page in both languages. The product name comes from brand.ts; the
// numbers in the properties are the measured ones from docs/ARCHITECTURE.md.

import { brand, type Lang } from '@/brand';

/** One of the three properties under the hero. */
interface Property {
  title: string;
  text: string;
  readout: string;
}

/** One kind of traffic in the "How it works" diagram. */
interface Traffic {
  kind: 'data' | 'meta' | 'ctl';
  title: string;
  text: string;
}

/** Every string the landing shows, for one language. */
export interface LandingStrings {
  pageTitle: string;
  lead: string;
  docsCta: string;
  githubCta: string;
  requirements: string;
  faceplate: { label: string; used: string; state: string; caption: string };
  propertiesTitle: string;
  properties: [Property, Property, Property];
  howTitle: string;
  howLead: string;
  traffic: [Traffic, Traffic, Traffic];
  diagram: {
    label: string;
    mac: string;
    finder: string;
    app: string;
    vm: string;
    vmSub: string;
    smb: string;
    controlPlane: string;
    controlPlaneSub: string;
    postgres: string;
    postgresSub: string;
    s3: string;
    s3Sub: string;
    data: string;
    meta: string;
    ctl: string;
  };
  installTitle: string;
  installText: string;
  installStatus: string;
  installMore: string;
  linksTitle: string;
  links: { docs: string; selfHosting: string; licenses: string; github: string };
}

/** Landing strings by language. */
export const landing: Record<Lang, LandingStrings> = {
  en: {
    pageTitle: 'A shared disk for video teams',
    lead: `${brand.name} shows up in the Finder like an external drive. The files live in an S3 bucket you choose, and the clips you open stay on the Mac, so the timeline plays as if they were local.`,
    docsCta: 'Read the docs',
    githubCta: 'Source on GitHub',
    requirements: 'Apple Silicon · macOS 13 or later · no administrator rights',
    faceplate: {
      label: `${brand.name} drive faceplate: a project downloading, 48.2 of 100 GB on this Mac`,
      used: '48.2 / 100 GB',
      state: '64%',
      caption: 'The disk as the menu bar app shows it: the slot fills with what this Mac keeps, the LED says what it is doing.',
    },
    propertiesTitle: 'What it is',
    properties: [
      {
        title: 'A disk in the Finder',
        text: `It mounts at ${brand.volume} like any external drive. The Finder and your editor open the files in place: no sync folder, no upload step, the same files on every Mac of the team.`,
        readout: brand.volume,
      },
      {
        title: 'Streaming from S3, with a local cache',
        text: 'Files are stored as 4 MB blocks in object storage. A clip starts while it downloads, only the parts you play are fetched, and what you have opened stays on the Mac’s SSD.',
        readout: 'Seek in cache 3 ms · cold 0.9 s',
      },
      {
        title: 'Self-hosted and open source',
        text: 'Your bucket, your database, your server. It speaks only standard S3 and PostgreSQL, and every line of it is public: the app and the command under MIT, the control plane under AGPL-3.0.',
        readout: 'MIT · AGPL-3.0',
      },
    ],
    howTitle: 'How it works',
    howLead: `On each Mac a small Linux VM runs JuiceFS and shares the disk to the Finder over SMB, on a link that never leaves the Mac: no kernel extension, nothing to approve in Recovery. From there, three kinds of traffic.`,
    traffic: [
      {
        kind: 'data',
        title: 'File data',
        text: 'The contents, in 4 MB blocks, between the VM and the S3 bucket. The heavy traffic, and the only one the cache saves.',
      },
      {
        kind: 'meta',
        title: 'Metadata',
        text: 'The file tree, between the VM and PostgreSQL. Small and frequent: names, sizes, which blocks make up a file.',
      },
      {
        kind: 'ctl',
        title: 'Control',
        text: 'Sign-in, configuration and heartbeat over HTTPS, between the app and the control plane. It is off the data path: if it stops, mounted disks keep working.',
      },
    ],
    diagram: {
      label: `Diagram: on each Mac the Finder reaches a Linux VM over SMB; the VM sends file data to S3 and metadata to PostgreSQL; the ${brand.name} app talks to the control plane.`,
      mac: 'Each Mac',
      finder: 'Finder',
      app: `${brand.name}.app`,
      vm: 'Linux VM',
      vmSub: 'JuiceFS · cache on the SSD',
      smb: 'SMB, private link',
      controlPlane: 'Control plane',
      controlPlaneSub: 'accounts, devices',
      postgres: 'PostgreSQL',
      postgresSub: 'the file tree',
      s3: 'S3 bucket',
      s3Sub: '4 MB blocks',
      data: 'data',
      meta: 'metadata',
      ctl: 'control',
    },
    installTitle: 'Install',
    installText: `One cask installs the app; on first start it creates the VM and mounts the disk. It needs Homebrew and about a minute and a half.`,
    installStatus: 'Not released yet',
    installMore: 'Install on a Mac, step by step',
    linksTitle: 'Read on',
    links: {
      docs: 'Introduction',
      selfHosting: 'Self-hosting',
      licenses: 'Licenses',
      github: 'GitHub',
    },
  },
  it: {
    pageTitle: 'Un disco condiviso per chi monta video',
    lead: `${brand.name} compare nel Finder come un disco esterno. I file stanno in un bucket S3 che scegli tu, e le clip che apri restano sul Mac: la timeline scorre come se fossero in locale.`,
    docsCta: 'Leggi la documentazione',
    githubCta: 'Codice su GitHub',
    requirements: 'Apple Silicon · macOS 13 o successivo · senza diritti di amministratore',
    faceplate: {
      label: `Frontale del disco ${brand.name}: un progetto in download, 48,2 GB su 100 su questo Mac`,
      used: '48,2 / 100 GB',
      state: '64%',
      caption: 'Il disco come lo mostra l’app nella barra dei menu: la fessura si riempie con quello che il Mac tiene, il LED dice cosa sta facendo.',
    },
    propertiesTitle: 'Cos’è',
    properties: [
      {
        title: 'Un disco nel Finder',
        text: `Si monta in ${brand.volume} come qualsiasi disco esterno. Il Finder e il programma di montaggio aprono i file dove sono: nessuna cartella da sincronizzare, nessun caricamento, gli stessi file su ogni Mac del gruppo.`,
        readout: brand.volume,
      },
      {
        title: 'Streaming da S3, con una cache locale',
        text: 'I file stanno nell’object storage in blocchi da 4 MB. Una clip parte mentre si scarica, arrivano solo i pezzi che riproduci, e quello che hai aperto resta sull’SSD del Mac.',
        readout: 'Salto in cache 3 ms · a freddo 0,9 s',
      },
      {
        title: 'Self-hosted e open source',
        text: 'Il tuo bucket, il tuo database, il tuo server. Parla solo S3 e PostgreSQL standard, e il codice è tutto pubblico: l’app e il comando con licenza MIT, il control plane AGPL-3.0.',
        readout: 'MIT · AGPL-3.0',
      },
    ],
    howTitle: 'Come funziona',
    howLead: `Su ogni Mac una piccola VM Linux esegue JuiceFS e passa il disco al Finder via SMB, su un collegamento che non esce dal Mac: niente estensioni del kernel, niente da approvare in modalità di recupero. Da lì, tre tipi di traffico.`,
    traffic: [
      {
        kind: 'data',
        title: 'Dati dei file',
        text: 'Il contenuto, in blocchi da 4 MB, fra la VM e il bucket S3. È il traffico pesante, e l’unico che la cache risparmia.',
      },
      {
        kind: 'meta',
        title: 'Metadati',
        text: 'L’albero dei file, fra la VM e PostgreSQL. Piccolo e frequente: nomi, dimensioni, quali blocchi compongono un file.',
      },
      {
        kind: 'ctl',
        title: 'Controllo',
        text: 'Accesso, configurazione e heartbeat in HTTPS, fra l’app e il control plane. Non sta sul percorso dei dati: se si ferma, i dischi montati continuano a funzionare.',
      },
    ],
    diagram: {
      label: `Schema: su ogni Mac il Finder raggiunge una VM Linux via SMB; la VM manda i dati dei file a S3 e i metadati a PostgreSQL; l’app ${brand.name} parla con il control plane.`,
      mac: 'Ogni Mac',
      finder: 'Finder',
      app: `${brand.name}.app`,
      vm: 'VM Linux',
      vmSub: 'JuiceFS · cache sull’SSD',
      smb: 'SMB, link privato',
      controlPlane: 'Control plane',
      controlPlaneSub: 'account, dispositivi',
      postgres: 'PostgreSQL',
      postgresSub: 'l’albero dei file',
      s3: 'Bucket S3',
      s3Sub: 'blocchi da 4 MB',
      data: 'dati',
      meta: 'metadati',
      ctl: 'controllo',
    },
    installTitle: 'Installazione',
    installText: `Un cask installa l’app; al primo avvio crea la VM e monta il disco. Serve Homebrew, e ci vuole circa un minuto e mezzo.`,
    installStatus: 'Non ancora pubblicato',
    installMore: 'Installare su un Mac, passo per passo',
    linksTitle: 'Per continuare',
    links: {
      docs: 'Introduzione',
      selfHosting: 'Self-hosting',
      licenses: 'Licenze',
      github: 'GitHub',
    },
  },
};
