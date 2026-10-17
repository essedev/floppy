// Strings of the landing page in both languages. The product name, the cask and the links
// come from brand.ts. The site describes the finished product (site/README.md); the landing
// speaks in plain benefits and leaves the technical detail and the tables to the docs. Its
// few numbers come from docs/ARCHITECTURE.md: change them there first, then here.

import { brand, type Lang } from '@/brand';

const { name } = brand;

/** A benefit with a short readout and the doc page that explains it. */
interface Benefit {
  title: string;
  text: string;
  readout: string;
  /** Docs path (without the language prefix) and the link's text. */
  more: { path: string; label: string };
}

/** One use case: who, what they do with it, why it fits. */
interface UseCase {
  title: string;
  who: string;
  text: string;
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
  benefits: [Benefit, Benefit, Benefit];
  usesTitle: string;
  usesLead: string;
  uses: UseCase[];
  notFitTitle: string;
  notFit: string[];
  howTitle: string;
  howLead: string;
  howMore: string;
  diagram: {
    label: string;
    mac: string;
    finder: string;
    app: string;
    vm: string;
    vmSub: string;
    /** The subtitle where the wide diagram has room. */
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
  costTitle: string;
  costLead: string;
  costReadout: string;
  costCompare: string;
  costHidden: string;
  costMore: string;
  openTitle: string;
  openSource: { title: string; text: string; readout: string; more: string };
  hosting: { title: string; text: string; readout: string; more: string };
  installTitle: string;
  installText: string;
  installCopy: string;
  installCopied: string;
  installRequirements: string[];
  installMore: string;
  linksTitle: string;
  links: { docs: string; how: string; costs: string; selfHosting: string; licenses: string; github: string };
  /** Link to the same page in the other language. */
  otherLanguage: string;
  /** The line at the foot of the page. */
  footer: string;
}

/** Landing strings by language. */
export const landing: Record<Lang, LandingStrings> = {
  en: {
    pageTitle: 'A shared disk for big files',
    description: `${name} is a shared disk for teams on Macs: it shows up in the Finder, the files live in an S3 bucket you choose, and what you open stays on the Mac. Tuned for video. Open source, self-hosted or hosted.`,
    lead: `${name} mounts like an external drive on every Mac of the team. The files live in your own storage; what you open stays on the Mac, so even large video plays as if it were local.`,
    installCta: 'Install',
    docsCta: 'Read the docs',
    requirements: 'Apple Silicon · macOS 13 or later · no administrator rights · no kernel extension',
    faceplate: {
      label: `The ${name} faceplate: a slot that fills with what this Mac keeps, 48.2 of 100 GB, and an LED for the state of the disk.`,
      caption: 'The faceplate of the menu bar app: the slot fills with what this Mac keeps, the LED shows what the disk is doing.',
      plateOk: 'CONNECTED',
      states: { ok: 'Connected', dl: 'Downloading', err: 'Unreachable', off: 'Off' },
    },
    whoTitle: 'For teams on Macs that share big files',
    whoLead: 'Studios and small teams who keep their work on their own storage and want it on every Mac as a plain disk. Tuned for video, where files are large and the timeline jumps around.',
    benefits: [
      {
        title: 'A disk in the Finder',
        text: 'Every app opens files in place, at the same path on every Mac. Nobody syncs, uploads or learns what a bucket is.',
        readout: brand.volume,
        more: { path: '/introduction/', label: 'What it does' },
      },
      {
        title: 'Fast for what you use',
        text: 'Only what you open is downloaded, and it stays on the Mac. Make a project available before you start, free it when you are done.',
        readout: 'A clip on the Mac jumps in 0-3 ms',
        more: { path: '/how-it-works/', label: 'How the cache works' },
      },
      {
        title: 'One disk for the whole team',
        text: 'A new file shows up on every Mac in a moment, and folders open at once. Without a network you can still browse and open what is on the Mac.',
        readout: '300 files listed in a quarter of a second',
        more: { path: '/how-it-works/#when-the-network-drops', label: 'Offline and sharing' },
      },
    ],
    usesTitle: 'What teams use it for',
    usesLead: 'Anything big that a team opens more often than it writes.',
    uses: [
      {
        title: 'Video editing',
        who: 'Editors, production teams',
        text: 'Cut in CapCut, Final Cut or Premiere with the media on the disk. Large clips stream and stay on the Mac, so scrubbing stays smooth. This is what it is tuned for.',
      },
      {
        title: 'Photo shoots and libraries',
        who: 'Photographers, studios',
        text: 'Keep every shoot on the shared disk and open the RAW files from any Mac. Catalogs stay on each Mac, the photos in one place.',
      },
      {
        title: 'Design, 3D and architecture',
        who: 'Design and architecture studios',
        text: 'Heavy project files, models, textures and renders, in one copy on your storage. Each person keeps on the Mac what they work on.',
      },
      {
        title: 'Audio and music',
        who: 'Producers, sound designers',
        text: 'Sample libraries, stems and session audio on the shared disk. Large audio files are read from the network once, then from the Mac.',
      },
      {
        title: 'A shared archive',
        who: 'Any team',
        text: 'Finished projects on your own bucket instead of a cloud drive priced per seat. You pay for the storage, not for the people.',
      },
    ],
    notFitTitle: 'Where it does not fit',
    notFit: [
      'Thousands of tiny files written at once: each new file costs a trip to the server, about 0.14 s.',
      `Windows and Linux desktops: ${name} is for macOS.`,
      'Working fully offline: without a network the disk is read-only.',
      'Permissions per person or per folder: whoever has the disk sees everything.',
    ],
    howTitle: 'How it works',
    howLead: `${name} runs out of sight on each Mac and shows the disk in the Finder, with no kernel extension and no administrator password. The files live in your bucket; the list of files lives on a small server, with a copy on every Mac so folders open at once.`,
    howMore: 'The full picture, with the numbers',
    diagram: {
      label: `Diagram: on each Mac the Finder shows the ${name} disk, served on a private link from ${name} on the same Mac, which keeps the cache and a copy of the file list; the files go to your S3 bucket, changes to the file list go to one server for the team, and the app signs in with the control plane.`,
      mac: 'Each Mac',
      finder: 'Finder',
      app: `${name}.app`,
      vm: name,
      vmSub: 'cache · file list copy',
      vmSubLong: 'the cache and a copy of the file list',
      smb: 'private link',
      controlPlane: 'Sign-in',
      controlPlaneSub: 'access per Mac',
      meta: 'File list',
      metaSub: 'one server for the team',
      s3: 'Your bucket',
      s3Sub: 'the files',
      edgeData: 'files',
      edgeMeta: 'changes',
      edgeCtl: 'sign-in',
    },
    costTitle: 'Your storage, no per-seat price',
    costLead: 'Use any S3-compatible storage. A team pays for the storage and one small server, however many people use the disk.',
    costReadout: '3 TB for any team size: about €40-55 a month',
    costCompare: `LucidLink and SpaceFS do the same job, priced per person and with permissions per folder. ${name} has neither the price nor the permissions.`,
    costHidden: 'What it does cost is the time to run it, which pays off with more people, more data, or data that must stay on your own infrastructure.',
    costMore: 'Costs and alternatives, in detail',
    openTitle: 'Open source, self-hosted or hosted',
    openSource: {
      title: 'Open source',
      text: 'The app and the command are MIT. The control plane is AGPL-3.0: offer a modified copy as a service and you share the changes.',
      readout: 'MIT · AGPL-3.0',
      more: 'Licenses',
    },
    hosting: {
      title: 'Self-hosted or hosted',
      text: 'Run it on your own servers with Docker Compose, or have it run for you. Either way it works with any S3 storage, so you are tied to no provider.',
      readout: 'Docker Compose · any S3',
      more: 'Self-hosting',
    },
    installTitle: 'Install',
    installText: 'One Homebrew cask. On first start the app signs you in through the browser and mounts the disk.',
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
      costs: 'Costs and alternatives',
      selfHosting: 'Self-hosting',
      licenses: 'Licenses',
      github: 'GitHub',
    },
    otherLanguage: 'Italiano',
    footer: `${name}: the app and the command under MIT, the control plane under AGPL-3.0.`,
  },
  it: {
    pageTitle: 'Un disco condiviso per file grandi',
    description: `${name} è un disco condiviso per gruppi che lavorano su Mac: compare nel Finder, i file stanno in un bucket S3 che scegli tu, e quello che apri resta sul Mac. Pensato per il video. Open source, in proprio o in hosting.`,
    lead: `${name} si monta come un disco esterno su ogni Mac del gruppo. I file stanno nel tuo spazio; quello che apri resta sul Mac, così anche il video più pesante scorre come se fosse in locale.`,
    installCta: 'Installa',
    docsCta: 'Leggi la documentazione',
    requirements: 'Apple Silicon · macOS 13 o successivo · senza diritti di amministratore · senza estensioni del kernel',
    faceplate: {
      label: `Il frontale di ${name}: una fessura che si riempie con quello che il Mac tiene, 48,2 GB su 100, e un LED per lo stato del disco.`,
      caption: 'Il frontale dell’app nella barra dei menu: la fessura si riempie con quello che il Mac tiene, il LED dice cosa sta facendo il disco.',
      plateOk: 'COLLEGATO',
      states: { ok: 'Collegato', dl: 'Download', err: 'Non raggiungibile', off: 'Spento' },
    },
    whoTitle: 'Per gruppi su Mac che condividono file grandi',
    whoLead: 'Studi e piccoli gruppi che tengono il lavoro nel proprio spazio e lo vogliono su ogni Mac come un disco qualsiasi. Pensato per il video, dove i file sono grandi e la timeline salta da un punto all’altro.',
    benefits: [
      {
        title: 'Un disco nel Finder',
        text: 'Ogni programma apre i file dove sono, allo stesso percorso su ogni Mac. Nessuno sincronizza, carica o deve sapere cos’è un bucket.',
        readout: brand.volume,
        more: { path: '/introduction/', label: 'Cosa fa' },
      },
      {
        title: 'Veloce su quello che usi',
        text: 'Si scarica solo quello che apri, e resta sul Mac. Rendi disponibile un progetto prima di iniziare, liberalo quando hai finito.',
        readout: 'Una clip sul Mac salta in 0-3 ms',
        more: { path: '/how-it-works/', label: 'Come funziona la cache' },
      },
      {
        title: 'Un disco per tutto il gruppo',
        text: 'Un file nuovo compare su ogni Mac in un attimo, e le cartelle si aprono subito. Senza rete puoi ancora sfogliare e aprire quello che è sul Mac.',
        readout: '300 file elencati in un quarto di secondo',
        more: { path: '/how-it-works/#when-the-network-drops', label: 'Senza rete e in gruppo' },
      },
    ],
    usesTitle: 'Per cosa lo usano',
    usesLead: 'Qualsiasi cosa pesante che un gruppo apre più spesso di quanto la scriva.',
    uses: [
      {
        title: 'Montaggio video',
        who: 'Montatori, produzioni',
        text: 'Monta in CapCut, Final Cut o Premiere con il materiale sul disco. Le clip grandi arrivano in streaming e restano sul Mac, così scorrere la timeline resta fluido. È il caso per cui è pensato.',
      },
      {
        title: 'Servizi e archivi fotografici',
        who: 'Fotografi, studi',
        text: 'Ogni servizio sul disco condiviso, con i RAW apribili da qualsiasi Mac. I cataloghi restano su ogni Mac, le foto in un posto solo.',
      },
      {
        title: 'Design, 3D e architettura',
        who: 'Studi di design e di architettura',
        text: 'File di progetto pesanti, modelli, texture e render, in una copia sola nel tuo spazio. Ognuno tiene sul Mac quello su cui lavora.',
      },
      {
        title: 'Audio e musica',
        who: 'Produttori, sound designer',
        text: 'Librerie di campioni, stem e audio delle sessioni sul disco condiviso. I file audio grandi si leggono dalla rete una volta, poi dal Mac.',
      },
      {
        title: 'Un archivio condiviso',
        who: 'Qualsiasi gruppo',
        text: 'I progetti finiti nel tuo bucket invece che in un cloud con prezzo a persona. Paghi lo spazio, non le persone.',
      },
    ],
    notFitTitle: 'Dove non va bene',
    notFit: [
      'Migliaia di file minuscoli scritti tutti insieme: ogni file nuovo costa un giro fino al server, circa 0,14 s.',
      `Computer con Windows o Linux: ${name} è per macOS.`,
      'Lavorare del tutto offline: senza rete il disco è in sola lettura.',
      'Permessi per persona o per cartella: chi ha il disco vede tutto.',
    ],
    howTitle: 'Come funziona',
    howLead: `${name} lavora dietro le quinte su ogni Mac e mostra il disco nel Finder, senza estensioni del kernel e senza password di amministratore. I file stanno nel tuo bucket; l’elenco dei file sta su un piccolo server, con una copia su ogni Mac perché le cartelle si aprano subito.`,
    howMore: 'Il quadro completo, con i numeri',
    diagram: {
      label: `Schema: su ogni Mac il Finder mostra il disco di ${name}, servito su un collegamento privato da ${name} sullo stesso Mac, che tiene la cache e una copia dell’elenco dei file; i file vanno nel tuo bucket S3, le modifiche all’elenco a un server per il gruppo, e l’app accede tramite il control plane.`,
      mac: 'Ogni Mac',
      finder: 'Finder',
      app: `${name}.app`,
      vm: name,
      vmSub: 'cache · copia dell’elenco',
      vmSubLong: 'la cache e una copia dell’elenco dei file',
      smb: 'link privato',
      controlPlane: 'Accesso',
      controlPlaneSub: 'un permesso per Mac',
      meta: 'Elenco dei file',
      metaSub: 'un server per il gruppo',
      s3: 'Il tuo bucket',
      s3Sub: 'i file',
      edgeData: 'file',
      edgeMeta: 'modifiche',
      edgeCtl: 'accesso',
    },
    costTitle: 'Il tuo spazio, nessun prezzo a persona',
    costLead: 'Va bene qualsiasi spazio compatibile con S3. Un gruppo paga lo spazio e un piccolo server, qualunque sia il numero di persone che usano il disco.',
    costReadout: '3 TB per un gruppo di qualsiasi dimensione: circa 40-55 € al mese',
    costCompare: `LucidLink e SpaceFS fanno lo stesso lavoro, con un prezzo a persona e permessi per cartella. ${name} non ha né l’uno né gli altri.`,
    costHidden: 'Quello che costa davvero è il tempo per gestirlo, che conviene con più persone, più dati, o dati che devono restare sulla tua infrastruttura.',
    costMore: 'Costi e alternative, nel dettaglio',
    openTitle: 'Open source, in proprio o in hosting',
    openSource: {
      title: 'Open source',
      text: 'L’app e il comando sono MIT. Il control plane è AGPL-3.0: chi ne offre una copia modificata come servizio condivide le modifiche.',
      readout: 'MIT · AGPL-3.0',
      more: 'Licenze',
    },
    hosting: {
      title: 'In proprio o in hosting',
      text: 'Fallo girare sui tuoi server con Docker Compose, o usalo in hosting. In entrambi i casi funziona con qualsiasi spazio S3, quindi non sei legato a un fornitore.',
      readout: 'Docker Compose · qualsiasi S3',
      more: 'Self-hosting',
    },
    installTitle: 'Installazione',
    installText: 'Un cask di Homebrew. Al primo avvio l’app ti fa accedere dal browser e monta il disco.',
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
      costs: 'Costi e alternative',
      selfHosting: 'Self-hosting',
      licenses: 'Licenze',
      github: 'GitHub',
    },
    otherLanguage: 'English',
    footer: `${name}: l’app e il comando con licenza MIT, il control plane AGPL-3.0.`,
  },
};
