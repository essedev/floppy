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
    lead: `${name} mounts like an external drive on every Mac of the team. The footage lives in your S3 bucket; what you open stays on the Mac and plays as if it were local.`,
    installCta: 'Install',
    docsCta: 'Read the docs',
    requirements: 'Apple Silicon · macOS 13 or later · no administrator rights · no kernel extension',
    faceplate: {
      label: `The ${name} faceplate: a slot that fills with what this Mac keeps in its cache, 48.2 of 100 GB, and an LED for the state of the disk.`,
      caption: 'The faceplate of the menu bar app: the slot fills with what this Mac keeps, the LED shows what the disk is doing.',
      plateOk: 'CONNECTED',
      states: { ok: 'Connected', dl: 'Downloading', err: 'Unreachable', off: 'Off' },
    },
    whoTitle: 'For teams that edit video on Macs',
    whoLead: 'Small teams cutting phone footage in CapCut and similar editors. Everyone sees the same files; nobody syncs, uploads, or needs to know what a bucket is.',
    properties: [
      {
        title: 'A disk in the Finder',
        text: `Mounted at ${brand.volume}, the same path on every Mac, so CapCut always finds its clips.`,
        readout: brand.volume,
      },
      {
        title: 'Only what you play is downloaded',
        text: 'Files sit in S3 as 1 MB blocks, and a jump fetches only the blocks there. What you open stays on the SSD; a whole project can be made available before you edit.',
        readout: 'Jump in cache: 0-3 ms',
      },
      {
        title: 'One disk for the whole team',
        text: 'Each Mac keeps a copy of the file tree: folders open at once, and a new file appears on every Mac in a moment. Offline the disk is read-only: you browse and play what is cached.',
        readout: '300 files listed in 0.2-0.3 s',
      },
    ],
    howTitle: 'How it works',
    howLead: 'A small Linux VM on each Mac runs JuiceFS and serves the disk to the Finder over SMB, on a link that never leaves the Mac. No kernel extension, no administrator rights.',
    traffic: [
      {
        kind: 'data',
        title: 'File data',
        text: '1 MB blocks between the VM and your bucket. The heavy traffic, and what the cache saves.',
      },
      {
        kind: 'meta',
        title: 'File tree',
        text: 'A central Valkey with a replica in each VM. Reads stay on the Mac; each write is one round trip.',
      },
      {
        kind: 'ctl',
        title: 'Control',
        text: 'Sign-in and one revocable access per Mac. Off the data path: if it stops, disks keep working.',
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
    measuredLead: 'From runs written up in the repository. For scale: a 60 Mbps phone clip needs 7.5 MB/s.',
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
    costLead: 'Any S3 provider works: on OVH in Milan, about $8 per TB a month with free egress in Europe. Self-hosted, add a fixed €20-30 a month for the metadata server. A new person costs nothing.',
    costCaption: 'Estimated monthly cost',
    costHead: ['Active data', 'SpaceFS Teams, 3 people', 'SpaceFS Teams, 10 people', `${name}, any team size`, `${name}’s bucket backup`],
    costRows: [
      ['3 TB', '$90', '$300', 'about €40-55', 'about €20'],
      ['5 TB', '$114-138', '$300', 'about €55-70', 'about €35'],
      ['10 TB', '$174-258', '$300', 'about €95-110', 'about €70'],
    ],
    costNote: 'Estimates from published prices. The backup is a copy of the bucket on Backblaze B2, which a self-hosted disk needs; the commercial prices do not say whether they include one.',
    costHiddenTitle: 'What the table leaves out',
    costHidden: 'Your time: keeping Valkey and its backups healthy, helping a Mac that does not mount, carrying the JuiceFS patches to new releases. For a small team that is the real price; it pays off with more people, more TB, or data that must stay on your own infrastructure.',
    compareTitle: 'Next to LucidLink and SpaceFS',
    compareLead: `Both stream files from object storage with a local cache, and LucidLink replicates the file tree to every client, as ${name} does. Both are priced per person.`,
    competitors: [
      {
        name: 'LucidLink',
        facts: [
          'The industry standard.',
          'Per member a month: $7 with 100 GB, $27 with 400 GB.',
          'Your own bucket on Enterprise, about €45 per TB with the storage.',
          'Permissions per person and per folder.',
        ],
      },
      {
        name: 'SpaceFS Teams',
        facts: [
          'The same architecture, app and support included.',
          '$30 per person a month, 1 TB per person, pooled.',
          'Private beta; macOS 26.4 or later on Apple Silicon.',
          'Permissions per person and per folder.',
        ],
      },
      {
        name,
        facts: [
          'Open source, on any S3 bucket.',
          'No per-seat price: storage plus a fixed metadata server.',
          'macOS 13 or later on Apple Silicon, no administrator rights.',
          'Permissions are all or nothing.',
        ],
      },
    ],
    compareNote: 'Prices and features as each vendor publishes them.',
    elsewhereTitle: 'Pick something else if',
    elsewhere: [
      'your team works in one office: a NAS on 10 GbE is faster and simpler;',
      'you need permissions per person or per folder: LucidLink and SpaceFS have them;',
      `you edit on Windows or Linux: ${name} is for macOS.`,
    ],
    openTitle: 'Open source, self-hosted or hosted',
    openSource: {
      title: 'Open source',
      text: 'The app and the command are MIT. The control plane is AGPL-3.0: offer a modified copy as a service and you share the changes. Built on JuiceFS, Lima and Samba.',
      readout: 'MIT · AGPL-3.0',
      more: 'Licenses',
    },
    hosting: {
      title: 'Self-hosted or hosted',
      text: 'One codebase, two ways to run it. Self-hosted: your bucket, your Valkey, the control plane in Docker Compose. Hosted: run for you, one Valkey instance per organization. Only standard S3 and the Redis protocol, so no provider lock-in.',
      readout: 'S3 · Valkey',
      more: 'Self-hosting',
    },
    installTitle: 'Install',
    installText: 'One Homebrew cask. On first start the app signs you in through the browser, creates the VM and mounts the disk.',
    installCopy: 'Copy',
    installCopied: 'Copied',
    installRequirements: [
      'Apple Silicon, macOS 13 or later.',
      'Homebrew, which asks for administrator rights once, to install itself.',
      'About a minute and a half to the disk in the Finder.',
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
    footer: `${name}: the app and the command under MIT, the control plane under AGPL-3.0.`,
  },
  it: {
    pageTitle: 'Un disco condiviso per chi monta video',
    description: `${name} è un disco condiviso per chi monta video su Mac: compare nel Finder, i file stanno in un bucket S3 che scegli tu, e una cache tiene sul Mac le clip che apri. Open source, in proprio o in hosting.`,
    lead: `${name} si monta come un disco esterno su ogni Mac del gruppo. Il girato sta nel tuo bucket S3; quello che apri resta sul Mac e scorre come se fosse in locale.`,
    installCta: 'Installa',
    docsCta: 'Leggi la documentazione',
    requirements: 'Apple Silicon · macOS 13 o successivo · senza diritti di amministratore · senza estensioni del kernel',
    faceplate: {
      label: `Il frontale di ${name}: una fessura che si riempie con quello che il Mac tiene in cache, 48,2 GB su 100, e un LED per lo stato del disco.`,
      caption: 'Il frontale dell’app nella barra dei menu: la fessura si riempie con quello che il Mac tiene, il LED dice cosa sta facendo il disco.',
      plateOk: 'COLLEGATO',
      states: { ok: 'Collegato', dl: 'Download', err: 'Non raggiungibile', off: 'Spento' },
    },
    whoTitle: 'Per chi monta video in gruppo, su Mac',
    whoLead: 'Piccoli gruppi che montano girato da telefono con CapCut e programmi simili. Tutti vedono gli stessi file; nessuno sincronizza, carica, o deve sapere cos’è un bucket.',
    properties: [
      {
        title: 'Un disco nel Finder',
        text: `Montato in ${brand.volume}, lo stesso percorso su ogni Mac: CapCut ritrova sempre le sue clip.`,
        readout: brand.volume,
      },
      {
        title: 'Si scarica solo quello che guardi',
        text: 'I file stanno su S3 in blocchi da 1 MB, e un salto scarica solo i blocchi di quel punto. Quello che apri resta sull’SSD; un progetto intero si può rendere disponibile prima di montare.',
        readout: 'Salto in cache: 0-3 ms',
      },
      {
        title: 'Un disco per tutto il gruppo',
        text: 'Ogni Mac tiene una copia dell’albero dei file: le cartelle si aprono subito, e un file nuovo compare su ogni Mac in un attimo. Senza rete il disco è in sola lettura: sfogli e riproduci quello che è in cache.',
        readout: '300 file elencati in 0,2-0,3 s',
      },
    ],
    howTitle: 'Come funziona',
    howLead: 'Una piccola VM Linux su ogni Mac esegue JuiceFS e passa il disco al Finder via SMB, su un collegamento che non esce dal Mac. Niente estensioni del kernel, niente diritti di amministratore.',
    traffic: [
      {
        kind: 'data',
        title: 'Dati dei file',
        text: 'Blocchi da 1 MB fra la VM e il tuo bucket. Il traffico pesante, e quello che la cache risparmia.',
      },
      {
        kind: 'meta',
        title: 'Albero dei file',
        text: 'Un Valkey centrale con una replica in ogni VM. Le letture restano sul Mac; ogni scrittura è un solo giro.',
      },
      {
        kind: 'ctl',
        title: 'Controllo',
        text: 'Accesso e un permesso revocabile per ogni Mac. Fuori dal percorso dei dati: se si ferma, i dischi continuano a funzionare.',
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
    measuredLead: 'Da misure documentate nel repository. Per avere una scala: una clip da telefono a 60 Mbps ha bisogno di 7,5 MB/s.',
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
    costLead: 'Va bene qualsiasi fornitore S3: su OVH a Milano circa 8 $ al TB al mese, con traffico in uscita gratuito in Europa. In proprio si aggiungono 20-30 € fissi al mese per il server dei metadati. Una persona in più non costa niente.',
    costCaption: 'Costo mensile stimato',
    costHead: ['Dati attivi', 'SpaceFS Teams, 3 persone', 'SpaceFS Teams, 10 persone', `${name}, qualsiasi gruppo`, `Backup del bucket di ${name}`],
    costRows: [
      ['3 TB', '90 $', '300 $', 'circa 40-55 €', 'circa 20 €'],
      ['5 TB', '114-138 $', '300 $', 'circa 55-70 €', 'circa 35 €'],
      ['10 TB', '174-258 $', '300 $', 'circa 95-110 €', 'circa 70 €'],
    ],
    costNote: 'Stime da prezzi pubblici. Il backup è una copia del bucket su Backblaze B2, che un disco in proprio richiede; i prezzi commerciali non dicono se ne includono uno.',
    costHiddenTitle: 'Quello che la tabella non dice',
    costHidden: 'Il tuo tempo: tenere in salute Valkey e i suoi backup, aiutare un Mac che non monta, portare le patch di JuiceFS sulle nuove versioni. Per un gruppo piccolo è quello il prezzo vero; conviene con più persone, più TB, o dati che devono restare sulla tua infrastruttura.',
    compareTitle: 'Accanto a LucidLink e SpaceFS',
    compareLead: `Entrambi portano i file in streaming dall’object storage con una cache locale, e LucidLink replica l’albero dei file su ogni client, come fa ${name}. Entrambi hanno un prezzo a persona.`,
    competitors: [
      {
        name: 'LucidLink',
        facts: [
          'Lo standard del settore.',
          'A persona al mese: 7 $ con 100 GB, 27 $ con 400 GB.',
          'Il proprio bucket con Enterprise, circa 45 € al TB spazio compreso.',
          'Permessi per persona e per cartella.',
        ],
      },
      {
        name: 'SpaceFS Teams',
        facts: [
          'La stessa architettura, app e assistenza incluse.',
          '30 $ a persona al mese, 1 TB a persona, in comune.',
          'Beta privata; macOS 26.4 o successivo su Apple Silicon.',
          'Permessi per persona e per cartella.',
        ],
      },
      {
        name,
        facts: [
          'Open source, su qualsiasi bucket S3.',
          'Nessun prezzo a persona: lo spazio più un server dei metadati a costo fisso.',
          'macOS 13 o successivo su Apple Silicon, senza diritti di amministratore.',
          'Permessi tutto o niente.',
        ],
      },
    ],
    compareNote: 'Prezzi e funzioni come li pubblica ogni fornitore.',
    elsewhereTitle: 'Scegli altro se',
    elsewhere: [
      'il gruppo lavora in un solo ufficio: un NAS su 10 GbE è più veloce e più semplice;',
      'servono permessi per persona o per cartella: LucidLink e SpaceFS li hanno;',
      `monti su Windows o Linux: ${name} è per macOS.`,
    ],
    openTitle: 'Open source, in proprio o in hosting',
    openSource: {
      title: 'Open source',
      text: 'L’app e il comando sono MIT. Il control plane è AGPL-3.0: chi ne offre una copia modificata come servizio condivide le modifiche. Si basa su JuiceFS, Lima e Samba.',
      readout: 'MIT · AGPL-3.0',
      more: 'Licenze',
    },
    hosting: {
      title: 'In proprio o in hosting',
      text: 'Un solo codice, due modi di usarlo. In proprio: il tuo bucket, il tuo Valkey, il control plane in Docker Compose. In hosting: gestito per te, un’istanza di Valkey per organizzazione. Solo S3 standard e il protocollo di Redis, quindi nessun legame con un fornitore.',
      readout: 'S3 · Valkey',
      more: 'Self-hosting',
    },
    installTitle: 'Installazione',
    installText: 'Un cask di Homebrew. Al primo avvio l’app ti fa accedere dal browser, crea la VM e monta il disco.',
    installCopy: 'Copia',
    installCopied: 'Copiato',
    installRequirements: [
      'Apple Silicon, macOS 13 o successivo.',
      'Homebrew, che chiede i diritti di amministratore una volta sola, per installare se stesso.',
      'Circa un minuto e mezzo per avere il disco nel Finder.',
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
    footer: `${name}: l’app e il comando con licenza MIT, il control plane AGPL-3.0.`,
  },
};
