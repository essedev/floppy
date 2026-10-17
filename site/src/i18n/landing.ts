// Strings of the landing page in both languages. The product name, the cask and the links
// come from brand.ts. The site describes the finished product (site/README.md); every number
// is a measured or estimated one from docs/ARCHITECTURE.md (measured performance, costs,
// alternatives): change it there first, then here.

import { brand, type Lang } from '@/brand';

const { name } = brand;

/** A titled paragraph with a short readout under it. */
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

/** One measured operation and its value. */
interface Measure {
  label: string;
  value: string;
  /** What qualifies the value (mean, share of the line), printed after the label. */
  note?: string;
}

/** A set of measures taken on the same setup. */
interface MeasureGroup {
  title: string;
  /** The setup it was measured on, as a readout. */
  setup: string;
  rows: Measure[];
}

/** One product in the comparison with the category. */
interface Competitor {
  name: string;
  facts: string[];
}

/** The four states the faceplate's LED can show. */
type LedState = 'ok' | 'dl' | 'err' | 'off';

/** Every string the landing shows, for one language. */
export interface LandingStrings {
  pageTitle: string;
  description: string;
  lead: string;
  installCta: string;
  docsCta: string;
  requirements: string;
  faceplate: {
    label: string;
    caption: string;
    /** What the faceplate prints under the LED when connected. */
    plateOk: string;
    /** Names of the LED states, for the legend. */
    states: Record<LedState, string>;
  };
  whoTitle: string;
  whoLead: string;
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
    /** The VM's subtitle where the wide diagram has room. */
    vmSubLong: string;
    smb: string;
    controlPlane: string;
    controlPlaneSub: string;
    meta: string;
    metaSub: string;
    s3: string;
    s3Sub: string;
    edgeData: string;
    edgeMeta: string;
    edgeCtl: string;
  };
  measuredTitle: string;
  measuredLead: string;
  measures: [MeasureGroup, MeasureGroup, MeasureGroup];
  measuredMore: string;
  costTitle: string;
  costLead: string;
  costCaption: string;
  costHead: [string, string, string, string, string];
  costRows: [string, string, string, string, string][];
  costNote: string;
  costHiddenTitle: string;
  costHidden: string;
  compareTitle: string;
  compareLead: string;
  competitors: [Competitor, Competitor, Competitor];
  compareNote: string;
  elsewhereTitle: string;
  elsewhere: string[];
  openTitle: string;
  openSource: Property & { more: string };
  hosting: Property & { more: string };
  installTitle: string;
  installText: string;
  installCopy: string;
  installCopied: string;
  installRequirements: string[];
  installMore: string;
  linksTitle: string;
  links: { docs: string; how: string; selfHosting: string; licenses: string; github: string };
  /** Link to the same page in the other language. */
  otherLanguage: string;
  /** The line at the foot of the page. */
  footer: string;
}

/** Landing strings by language. */
export const landing: Record<Lang, LandingStrings> = {
  en: {
    pageTitle: 'A shared disk for video teams',
    description: `${name} is a shared disk for video teams on Macs: it shows up in the Finder, the files live in an S3 bucket you choose, and a cache keeps the clips you open on the Mac. Open source, self-hosted or hosted.`,
    lead: `${name} shows up on every Mac of the team like an external drive. The footage lives in an S3 bucket you choose, and the clips you open stay on the Mac’s SSD, so the timeline plays as if they were local.`,
    installCta: 'Install',
    docsCta: 'Read the docs',
    requirements: 'Apple Silicon · macOS 13 or later · no administrator rights · no kernel extension',
    faceplate: {
      label: `The ${name} faceplate: a slot that fills with what this Mac keeps in its cache, 48.2 of 100 GB, and an LED for the state of the disk.`,
      caption: 'The disk as the menu bar app shows it: the slot fills with what this Mac keeps, the LED says what the disk is doing.',
      plateOk: 'CONNECTED',
      states: { ok: 'Connected', dl: 'Downloading', err: 'Unreachable', off: 'Off' },
    },
    whoTitle: 'For teams that edit video on Macs',
    whoLead: 'Small teams cutting phone footage in CapCut and editors like it, who want the footage in one place: the same files on every Mac, no sync folder, no upload step. The people editing open the Finder and work; buckets and databases are for whoever sets it up.',
    properties: [
      {
        title: 'A disk in the Finder',
        text: `It mounts at ${brand.volume} like an external drive, at the same path on every Mac. CapCut finds clips by their full path, and the path never changes.`,
        readout: brand.volume,
      },
      {
        title: 'Only what you play is downloaded',
        text: 'Files are stored in S3 as 1 MB blocks. Playing or jumping to a point fetches only the blocks there, and what you open stays on the Mac’s SSD. A whole project can be made available before you start editing, and freed when you are done.',
        readout: 'Jump in cache: 0-3 ms',
      },
      {
        title: 'One disk for the whole team',
        text: 'Each Mac keeps a copy of the file tree: folders open at once, and a file saved on one Mac appears on the others in a moment. Without a network the disk stays browsable and cached clips play; it is read-only until the connection is back.',
        readout: '300 files listed in 0.2-0.3 s',
      },
    ],
    howTitle: 'How it works',
    howLead: 'On each Mac a small Linux VM runs JuiceFS, the open source file system that does the heavy lifting, and serves the disk to the Finder over SMB on a link that never leaves the Mac. That is how it works without a kernel extension or administrator rights. Behind it, three kinds of traffic.',
    traffic: [
      {
        kind: 'data',
        title: 'File data',
        text: 'The contents, in 1 MB blocks, between the VM and your S3 bucket. The heavy traffic, and the one the cache on the SSD saves.',
      },
      {
        kind: 'meta',
        title: 'File tree',
        text: 'Names, folders, sizes, which blocks make up a file: in a central Valkey, with a replica inside each VM. Reads stay on the Mac; a write is one round trip to the central copy.',
      },
      {
        kind: 'ctl',
        title: 'Control',
        text: 'Sign-in in the browser and one revocable access per Mac, between the app and the control plane. It is off the data path: if it stops, mounted disks keep working.',
      },
    ],
    diagram: {
      label: `Diagram: on each Mac the Finder reaches a Linux VM over SMB on a private link; the VM sends file data to the S3 bucket and file tree changes to a central Valkey, which keeps a replica in the VM up to date; the ${name} app talks to the control plane.`,
      mac: 'Each Mac',
      finder: 'Finder',
      app: `${name}.app`,
      vm: 'Linux VM',
      vmSub: 'JuiceFS · cache · replica',
      vmSubLong: 'JuiceFS · cache · a replica of the file tree',
      smb: 'SMB, private link',
      controlPlane: 'Control plane',
      controlPlaneSub: 'sign-in, access per Mac',
      meta: 'Valkey',
      metaSub: 'the file tree, central',
      s3: 'S3 bucket',
      s3Sub: '1 MB blocks',
      edgeData: 'data',
      edgeMeta: 'file tree',
      edgeCtl: 'control',
    },
    measuredTitle: 'Measured numbers',
    measuredLead: 'Every number here comes from a run written up in the repository, with its setup next to it. For scale: a 60 Mbps phone clip needs 7.5 MB/s to play.',
    measures: [
      {
        title: 'OVH in Milan, 170 Mbps',
        setup: 'Home fiber · 4 MB blocks · Mac over SMB',
        rows: [
          { label: 'Reading a 434 MB clip not in cache', value: '12.1 MB/s' },
          { label: 'Jumping to a point in cache', value: '0-3 ms' },
          { label: 'Jumping to a point not in cache', value: '0.9 s', note: 'mean' },
          { label: 'Making that clip available', value: '30 s' },
          { label: 'Copying 2.7 GB to the disk', value: '113 s', note: 'the line’s limit' },
        ],
      },
      {
        title: 'A 100 Mbps line, 1 MB blocks',
        setup: '25 ms to S3 · 4 MB readahead · Mac over SMB',
        rows: [
          { label: 'Reading a clip not in cache', value: '11.6 MB/s', note: '93% of the line' },
          { label: 'Jumping to a point not in cache', value: '0.38 s', note: 'mean' },
          { label: 'First read after a jump while scrubbing', value: '0.40 s', note: 'mean' },
          { label: 'The same cold read on a 1 Gbps line', value: '103 MB/s' },
        ],
      },
      {
        title: 'The file tree, 15 ms away',
        setup: 'Valkey · from the Finder over SMB',
        rows: [
          { label: 'Listing 300 files, tree over the network', value: '75-117 s' },
          { label: 'The same, with the replica on the Mac', value: '0.20-0.30 s' },
          { label: 'A new file showing up on another Mac', value: '0.00-0.01 s' },
          { label: 'Creating 100 small files', value: '13.7-17 s' },
        ],
      },
    ],
    measuredMore: 'How each number was measured',
    costTitle: 'Your bucket, no per-seat price',
    costLead: `The files live in a bucket you pick, at any provider that speaks standard S3; on OVH in Milan it is about $8 per TB a month, with free egress in Europe. Self-hosted, a team pays the storage and a fixed €20-30 a month for the metadata server. Adding a person costs nothing; adding a TB costs the storage.`,
    costCaption: 'Estimated monthly cost',
    costHead: ['Active data', 'SpaceFS Teams, 3 people', 'SpaceFS Teams, 10 people', `${name}, any team size`, `${name}’s bucket backup`],
    costRows: [
      ['3 TB', '$90', '$300', 'about €40-55', 'about €20'],
      ['5 TB', '$114-138', '$300', 'about €55-70', 'about €35'],
      ['10 TB', '$174-258', '$300', 'about €95-110', 'about €70'],
    ],
    costNote: 'Estimates from published prices. The backup is a copy of the bucket on a second provider (Backblaze B2), which a self-hosted disk needs anyway; the commercial prices do not say whether they include one.',
    costHiddenTitle: 'What the table leaves out',
    costHidden: 'The time to run it. Keeping Valkey up and its backups checked, answering when a Mac does not mount, carrying the JuiceFS patches across releases: for a small team that time is the real price. It pays off with more people, more TB, or data that has to stay on infrastructure you control.',
    compareTitle: 'Next to LucidLink and SpaceFS',
    compareLead: `LucidLink and SpaceFS solve the same problem: data streamed from object storage, with a cache on each computer. LucidLink also replicates the file tree to every client, as ${name} does. They are priced per person; ${name} is open source, run on your own servers or hosted.`,
    competitors: [
      {
        name: 'LucidLink',
        facts: [
          'The industry standard for streaming a shared file system.',
          'Per member a month: Starter $7 with 100 GB, Business $27 with 400 GB.',
          'Your own bucket on the Enterprise plan, about €45 per TB with the storage.',
          'Permissions per person and per folder.',
        ],
      },
      {
        name: 'SpaceFS Teams',
        facts: [
          'The same architecture, with the app and support included.',
          '$30 per person a month, with 1 TB per person pooled.',
          'In private beta, for macOS 26.4 or later on Apple Silicon.',
          'Permissions per person and per folder.',
        ],
      },
      {
        name,
        facts: [
          'Open source, on a bucket you pick at any S3 provider.',
          'No per-seat price: the storage and a fixed metadata server.',
          'No kernel extension, no administrator rights; macOS 13 or later on Apple Silicon.',
          'Permissions are all or nothing: whoever has the disk sees and can delete everything.',
        ],
      },
    ],
    compareNote: 'Prices and features as each vendor publishes them.',
    elsewhereTitle: 'Pick something else if',
    elsewhere: [
      'Your team works in one office: a NAS on 10 GbE gives local speed and is simpler to run.',
      'You need permissions per person or per folder: LucidLink and SpaceFS have them.',
      `You edit on Windows or Linux: ${name} is for macOS.`,
    ],
    openTitle: 'Open source, self-hosted or hosted',
    openSource: {
      title: 'Open source',
      text: 'The app and the command are MIT, free to reuse anywhere. The control plane is AGPL-3.0: whoever runs a modified copy as a service for others shares the changes. It builds on JuiceFS and Lima (Apache-2.0) and Samba (GPL-3.0).',
      readout: 'MIT · AGPL-3.0',
      more: 'Licenses',
    },
    hosting: {
      title: 'Self-hosted or hosted',
      text: 'The same code runs two ways. Self-hosted: one organization, your bucket, your Valkey, the control plane in Docker Compose. Hosted: run for you, each organization on its own Valkey instance. The core speaks only standard S3 and a Redis-compatible protocol, so it is tied to no provider.',
      readout: 'S3 · Valkey',
      more: 'Self-hosting',
    },
    installTitle: 'Install',
    installText: `${name} installs with a Homebrew cask. On first start the app asks you to sign in in the browser, creates the VM and mounts the disk in the Finder.`,
    installCopy: 'Copy',
    installCopied: 'Copied',
    installRequirements: [
      'A Mac with Apple Silicon and macOS 13 or later.',
      'Homebrew, which asks for administrator rights once, to install itself. The app and the disk never do.',
      'About a minute and a half for the first start.',
    ],
    installMore: 'The install, step by step',
    linksTitle: 'Read on',
    links: {
      docs: 'Introduction',
      how: 'How it works',
      selfHosting: 'Self-hosting',
      licenses: 'Licenses',
      github: 'GitHub',
    },
    otherLanguage: 'Italiano',
    footer: `${name} is open source: the app and the command under MIT, the control plane under AGPL-3.0.`,
  },
  it: {
    pageTitle: 'Un disco condiviso per chi monta video',
    description: `${name} è un disco condiviso per chi monta video su Mac: compare nel Finder, i file stanno in un bucket S3 che scegli tu, e una cache tiene sul Mac le clip che apri. Open source, in proprio o in hosting.`,
    lead: `${name} compare su ogni Mac del gruppo come un disco esterno. Il girato sta in un bucket S3 che scegli tu, e le clip che apri restano sull’SSD del Mac: la timeline scorre come se fossero in locale.`,
    installCta: 'Installa',
    docsCta: 'Leggi la documentazione',
    requirements: 'Apple Silicon · macOS 13 o successivo · senza diritti di amministratore · senza estensioni del kernel',
    faceplate: {
      label: `Il frontale di ${name}: una fessura che si riempie con quello che il Mac tiene in cache, 48,2 GB su 100, e un LED per lo stato del disco.`,
      caption: 'Il disco come lo mostra l’app nella barra dei menu: la fessura si riempie con quello che il Mac tiene, il LED dice cosa sta facendo il disco.',
      plateOk: 'COLLEGATO',
      states: { ok: 'Collegato', dl: 'Download', err: 'Non raggiungibile', off: 'Spento' },
    },
    whoTitle: 'Per chi monta video in gruppo, su Mac',
    whoLead: 'Piccoli gruppi che montano girato da telefono con CapCut e programmi simili, e vogliono il materiale in un posto solo: gli stessi file su ogni Mac, senza cartelle da sincronizzare né caricamenti. Chi monta apre il Finder e lavora; bucket e database sono affare di chi lo installa.',
    properties: [
      {
        title: 'Un disco nel Finder',
        text: `Si monta in ${brand.volume} come un disco esterno, allo stesso percorso su ogni Mac. CapCut ritrova le clip dal percorso completo, e il percorso non cambia mai.`,
        readout: brand.volume,
      },
      {
        title: 'Si scarica solo quello che guardi',
        text: 'I file stanno su S3 in blocchi da 1 MB. Riprodurre o saltare a un punto scarica solo i blocchi di quel punto, e quello che apri resta sull’SSD del Mac. Un progetto intero si può rendere disponibile prima di montare, e liberare quando hai finito.',
        readout: 'Salto in cache: 0-3 ms',
      },
      {
        title: 'Un disco per tutto il gruppo',
        text: 'Ogni Mac tiene una copia dell’albero dei file: le cartelle si aprono subito, e un file salvato su un Mac compare sugli altri in un attimo. Senza rete il disco resta sfogliabile e le clip in cache si riproducono; si può solo leggere finché la connessione non torna.',
        readout: '300 file elencati in 0,2-0,3 s',
      },
    ],
    howTitle: 'Come funziona',
    howLead: 'Su ogni Mac una piccola VM Linux esegue JuiceFS, il file system open source che fa il lavoro pesante, e passa il disco al Finder via SMB, su un collegamento che non esce dal Mac. Così funziona senza estensioni del kernel e senza diritti di amministratore. Dietro, tre tipi di traffico.',
    traffic: [
      {
        kind: 'data',
        title: 'Dati dei file',
        text: 'Il contenuto, in blocchi da 1 MB, fra la VM e il tuo bucket S3. È il traffico pesante, e quello che la cache sull’SSD risparmia.',
      },
      {
        kind: 'meta',
        title: 'Albero dei file',
        text: 'Nomi, cartelle, dimensioni, quali blocchi compongono un file: in un Valkey centrale, con una replica dentro ogni VM. Le letture restano sul Mac; una scrittura è un solo giro fino alla copia centrale.',
      },
      {
        kind: 'ctl',
        title: 'Controllo',
        text: 'Accesso dal browser e un permesso revocabile per ogni Mac, fra l’app e il control plane. Non sta sul percorso dei dati: se si ferma, i dischi montati continuano a funzionare.',
      },
    ],
    diagram: {
      label: `Schema: su ogni Mac il Finder raggiunge una VM Linux via SMB su un collegamento privato; la VM manda i dati dei file al bucket S3 e le modifiche all’albero dei file a un Valkey centrale, che tiene aggiornata una replica nella VM; l’app ${name} parla con il control plane.`,
      mac: 'Ogni Mac',
      finder: 'Finder',
      app: `${name}.app`,
      vm: 'VM Linux',
      vmSub: 'JuiceFS · cache · replica',
      vmSubLong: 'JuiceFS · cache · replica dell’albero dei file',
      smb: 'SMB, link privato',
      controlPlane: 'Control plane',
      controlPlaneSub: 'accessi, un permesso per Mac',
      meta: 'Valkey',
      metaSub: 'l’albero dei file, centrale',
      s3: 'Bucket S3',
      s3Sub: 'blocchi da 1 MB',
      edgeData: 'dati',
      edgeMeta: 'albero dei file',
      edgeCtl: 'controllo',
    },
    measuredTitle: 'Numeri misurati',
    measuredLead: 'Ogni numero viene da una misura documentata nel repository, con la configurazione accanto. Per avere una scala: una clip da telefono a 60 Mbps ha bisogno di 7,5 MB/s per essere riprodotta.',
    measures: [
      {
        title: 'OVH a Milano, 170 Mbps',
        setup: 'Fibra di casa · blocchi da 4 MB · Mac via SMB',
        rows: [
          { label: 'Leggere una clip da 434 MB non in cache', value: '12,1 MB/s' },
          { label: 'Saltare a un punto in cache', value: '0-3 ms' },
          { label: 'Saltare a un punto non in cache', value: '0,9 s', note: 'in media' },
          { label: 'Rendere disponibile quella clip', value: '30 s' },
          { label: 'Copiare 2,7 GB sul disco', value: '113 s', note: 'il limite della linea' },
        ],
      },
      {
        title: 'Una linea da 100 Mbps, blocchi da 1 MB',
        setup: '25 ms da S3 · lettura anticipata da 4 MB · Mac via SMB',
        rows: [
          { label: 'Leggere una clip non in cache', value: '11,6 MB/s', note: 'il 93% della linea' },
          { label: 'Saltare a un punto non in cache', value: '0,38 s', note: 'in media' },
          { label: 'Prima lettura dopo un salto, scorrendo la clip', value: '0,40 s', note: 'in media' },
          { label: 'La stessa lettura a freddo su una linea da 1 Gbps', value: '103 MB/s' },
        ],
      },
      {
        title: 'L’albero dei file a 15 ms',
        setup: 'Valkey · dal Finder via SMB',
        rows: [
          { label: 'Elencare 300 file, albero in rete', value: '75-117 s' },
          { label: 'Lo stesso, con la replica sul Mac', value: '0,20-0,30 s' },
          { label: 'Un file nuovo che compare su un altro Mac', value: '0,00-0,01 s' },
          { label: 'Creare 100 file piccoli', value: '13,7-17 s' },
        ],
      },
    ],
    measuredMore: 'Come è stato misurato ogni numero',
    costTitle: 'Il tuo bucket, nessun prezzo a persona',
    costLead: `I file stanno in un bucket che scegli tu, da qualsiasi fornitore che parli S3 standard; su OVH a Milano costa circa 8 $ al TB al mese, con traffico in uscita gratuito in Europa. In self-hosting un gruppo paga lo spazio e 20-30 € fissi al mese per il server dei metadati. Una persona in più non costa niente; un TB in più costa lo spazio.`,
    costCaption: 'Costo mensile stimato',
    costHead: ['Dati attivi', 'SpaceFS Teams, 3 persone', 'SpaceFS Teams, 10 persone', `${name}, qualsiasi gruppo`, `Backup del bucket di ${name}`],
    costRows: [
      ['3 TB', '90 $', '300 $', 'circa 40-55 €', 'circa 20 €'],
      ['5 TB', '114-138 $', '300 $', 'circa 55-70 €', 'circa 35 €'],
      ['10 TB', '174-258 $', '300 $', 'circa 95-110 €', 'circa 70 €'],
    ],
    costNote: 'Stime da prezzi pubblici. Il backup è una copia del bucket su un secondo fornitore (Backblaze B2), che un disco in self-hosting richiede comunque; i prezzi commerciali non dicono se ne includono uno.',
    costHiddenTitle: 'Quello che la tabella non dice',
    costHidden: 'Il tempo per gestirlo. Tenere su Valkey e controllarne i backup, rispondere quando un Mac non monta, portare le patch di JuiceFS da una versione all’altra: per un gruppo piccolo è quello il prezzo vero. Conviene con più persone, più TB, o dati che devono restare su un’infrastruttura che controlli tu.',
    compareTitle: 'Accanto a LucidLink e SpaceFS',
    compareLead: `LucidLink e SpaceFS risolvono lo stesso problema: dati in streaming dall’object storage, con una cache su ogni computer. LucidLink replica anche l’albero dei file su ogni client, come fa ${name}. Hanno un prezzo a persona; ${name} è open source, sui tuoi server o in hosting.`,
    competitors: [
      {
        name: 'LucidLink',
        facts: [
          'Lo standard del settore per un file system condiviso in streaming.',
          'A persona al mese: Starter 7 $ con 100 GB, Business 27 $ con 400 GB.',
          'Il proprio bucket con il piano Enterprise, circa 45 € al TB spazio compreso.',
          'Permessi per persona e per cartella.',
        ],
      },
      {
        name: 'SpaceFS Teams',
        facts: [
          'La stessa architettura, con app e assistenza incluse.',
          '30 $ a persona al mese, con 1 TB a persona in comune.',
          'In beta privata, per macOS 26.4 o successivo su Apple Silicon.',
          'Permessi per persona e per cartella.',
        ],
      },
      {
        name,
        facts: [
          'Open source, su un bucket che scegli tu da qualsiasi fornitore S3.',
          'Nessun prezzo a persona: lo spazio e un server dei metadati a costo fisso.',
          'Senza estensioni del kernel né diritti di amministratore; macOS 13 o successivo su Apple Silicon.',
          'Permessi tutto o niente: chi ha il disco vede e può cancellare tutto.',
        ],
      },
    ],
    compareNote: 'Prezzi e funzioni come li pubblica ogni fornitore.',
    elsewhereTitle: 'Scegli altro se',
    elsewhere: [
      'Il gruppo lavora in un solo ufficio: un NAS su 10 GbE dà velocità locale ed è più semplice da gestire.',
      'Servono permessi per persona o per cartella: LucidLink e SpaceFS li hanno.',
      `Monti su Windows o Linux: ${name} è per macOS.`,
    ],
    openTitle: 'Open source, in proprio o in hosting',
    openSource: {
      title: 'Open source',
      text: 'L’app e il comando sono MIT, liberi da riusare ovunque. Il control plane è AGPL-3.0: chi ne offre a terzi una copia modificata come servizio condivide le modifiche. Si appoggia a JuiceFS e Lima (Apache-2.0) e a Samba (GPL-3.0).',
      readout: 'MIT · AGPL-3.0',
      more: 'Licenze',
    },
    hosting: {
      title: 'In proprio o in hosting',
      text: 'Lo stesso codice gira in due modi. In proprio: un’organizzazione, il tuo bucket, il tuo Valkey, il control plane in Docker Compose. In hosting: gestito per te, ogni organizzazione sulla sua istanza di Valkey. Il nucleo parla solo S3 standard e un protocollo compatibile con Redis, quindi non è legato a nessun fornitore.',
      readout: 'S3 · Valkey',
      more: 'Self-hosting',
    },
    installTitle: 'Installazione',
    installText: `${name} si installa con un cask di Homebrew. Al primo avvio l’app ti chiede di accedere dal browser, crea la VM e monta il disco nel Finder.`,
    installCopy: 'Copia',
    installCopied: 'Copiato',
    installRequirements: [
      'Un Mac con Apple Silicon e macOS 13 o successivo.',
      'Homebrew, che chiede i diritti di amministratore una volta sola, per installare se stesso. L’app e il disco mai.',
      'Circa un minuto e mezzo per il primo avvio.',
    ],
    installMore: 'L’installazione, passo per passo',
    linksTitle: 'Per continuare',
    links: {
      docs: 'Introduzione',
      how: 'Come funziona',
      selfHosting: 'Self-hosting',
      licenses: 'Licenze',
      github: 'GitHub',
    },
    otherLanguage: 'English',
    footer: `${name} è open source: l’app e il comando con licenza MIT, il control plane AGPL-3.0.`,
  },
};
