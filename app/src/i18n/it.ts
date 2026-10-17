/**
 * Every string the app shows, in Italian. Copy comes from the concepts
 * (docs/concepts/concept-c-app.html, concept-d-onboarding.html); the few native
 * strings (tray menu and tooltip) are in src-tauri/src/text.rs.
 */

import { gbUnit } from "@/lib/format";
import type { CommandError, JobKind, Problem } from "@/lib/types";

export const it = {
  product: "Floppy",
  version: (v: string) => `Floppy ${v}`,

  face: {
    connected: "COLLEGATO",
    activity: "IN CORSO",
    starting: "AVVIO",
    stopping: "SPEGNIMENTO",
    unreachable: "SCOLLEGATO",
    off: "SPENTO",
    notInstalled: "NUOVO",
    needsSetup: "DA PREPARARE",
    error: "ERRORE",
    ready: "PRONTO",
    usage: (used: string, limit: string) => `${used} / ${limit} GB`,
    freeOnMac: (gb: string) => `${gb} GB LIBERI`,
  },

  panel: {
    label: "Pannello di Floppy",
    connected: { title: "Floppy è collegato", body: "I progetti sono nel Finder, sotto Floppy." },
    activity: {
      warmup: (name: string) => `Rendo disponibile ${name}`,
      warmupBody: "Puoi già montare: i file arrivano mentre lavori.",
      free: (name: string) => `Libero spazio da ${name}`,
      freeBody: "I file restano nel Finder e su Floppy.",
    },
    starting: { title: "Floppy si sta avviando", body: "Pochi secondi e i progetti tornano nel Finder." },
    stopping: { title: "Floppy si sta spegnendo", body: "Il disco sta lasciando il Finder." },
    unreachable: {
      title: "Floppy non è raggiungibile",
      body: (s: number) =>
        `Floppy riprova da solo: prossimo controllo tra ${s}\u00a0s. Intanto salva il progetto in CapCut.`,
      note: "Se non torna, premi Riprova ora. I file già aperti restano in CapCut.",
    },
    off: { title: "Floppy è spento", body: "Accendilo per ritrovare i progetti nel Finder." },
    notInstalled: {
      title: "Floppy non è installato su questo Mac",
      body: "Serve il pacchetto che ti manda chi gestisce il team.",
    },
    needsSetup: {
      title: "Floppy non è ancora pronto",
      body: "Manca un passo per preparare il disco su questo Mac.",
    },
    checking: "Controllo il disco…",
    openFinder: "Apri nel Finder",
    retryNow: "Riprova ora",
    turnOn: "Accendi Floppy",
    stop: "Interrompi",
    openSetup: "Apri la preparazione",
    settings: "Impostazioni",
    turnOff: "Spegni",
    stopConfirm: {
      title: "Spegnere Floppy?",
      body: "Il disco sparisce dal Finder e CapCut non vede più i file finché non lo riaccendi. Si libera anche la memoria che Floppy usa.",
      confirm: "Spegni",
    },
    cancel: "Annulla",
    dismiss: "Chiudi",
  },

  problem: {
    vm_stopped: "MACCHINA VIRTUALE FERMA",
    vm_broken: "MACCHINA VIRTUALE SENZA RISPOSTA",
    drive_down: "SERVIZIO DEL DISCO FERMO",
    not_mounted: "DISCO NON MONTATO",
    not_responding: "DISCO SENZA RISPOSTA",
    engine: "IL COMANDO NON RISPONDE",
  } satisfies Record<Problem, string>,

  notices: {
    wrongMount: {
      title: (mp: string) => `Il disco è in ${mp}`,
      body: "I progetti di CapCut cercano i file in /Volumes/Floppy. Spegni e riaccendi Floppy per rimetterlo al suo posto.",
    },
    lowSpace: {
      title: "Il Mac ha quasi finito lo spazio",
      body: (free: string, floor: string) =>
        `Restano ${free} liberi. Sotto i ${floor} Floppy libera da solo tutti i file scaricati, per non riempire il disco.`,
      action: "Libera spazio ora",
      confirmTitle: "Liberare tutte le copie locali?",
      confirmBody:
        "I file restano nel Finder e su Floppy: si riscaricano quando li apri, e fino ad allora ogni salto nel video aspetta circa un secondo.",
    },
    jobFailed: {
      title: (kind: JobKind, name: string | null) => {
        const what = name ?? "il disco";
        switch (kind) {
          case "warmup":
            return `Non sono riuscito a rendere disponibile ${what}`;
          case "free":
            return `Non sono riuscito a liberare spazio da ${what}`;
          case "start":
            return "Non sono riuscito ad accendere Floppy";
          case "stop":
            return "Non sono riuscito a spegnere Floppy";
          case "setup":
            return "La preparazione non è andata a buon fine";
        }
      },
    },
    engineDetail: "Dettaglio del comando",
  },

  space: {
    heading: "Su questo Mac",
    of: (used: string, limit: string) => `${used} di ${limit} GB`,
    local: "Copie locali dei file",
    free: "Ancora libero",
    macFree: "Liberi sul Mac",
    unknown: "Lo spazio si legge quando il disco è acceso.",
  },

  folders: {
    heading: "Progetti",
    loading: "Leggo le cartelle del disco…",
    empty: "Il disco è vuoto. Le cartelle che crei nel Finder compaiono qui.",
    timeout: "Il disco non ha risposto in tempo.",
    retry: "Riprova",
    actions: (name: string) => `Azioni per ${name}`,
    working: "IN CORSO",
    makeAvailable: "Rendi disponibile",
    freeSpace: "Libera spazio",
    showInFinder: "Mostra nel Finder",
    fullCache:
      "Per fare posto Floppy libererà i file aperti meno di recente su questo Mac. Restano su Floppy e si riscaricano quando li apri.",
    freeConfirm: {
      title: (name: string) => `Liberare lo spazio di ${name}?`,
      body: "I file restano nel Finder e su Floppy: si riscaricano quando li apri, e fino ad allora ogni salto nel video aspetta circa un secondo.",
      confirm: "Libera spazio",
    },
  },

  settings: {
    title: "Impostazioni",
    account: "Account",
    accountFake: "ESEMPIO",
    accountFakeNote:
      "L'accesso al team arriva con il pannello di controllo, che non c'è ancora: questi dati sono di esempio.",
    accountLine: (email: string, team: string) => `${email} · ${team}, membro`,
    signOut: "Esci",
    thisMac: "Questo Mac",
    thisMacUnknown: "Questo Mac",
    thisMacLine: "Chi gestisce il team lo vedrà con questo nome.",
    space: "Spazio",
    maxSpace: "Spazio massimo per Floppy",
    maxSpaceLine:
      "I file scaricati restano qui finché non serve posto. Il massimo lo calcola Floppy dallo spazio libero del Mac quando viene preparato.",
    inUse: "In uso",
    inUseLine: (reserve: string) => `Floppy lascia sempre almeno ${reserve} liberi sul Mac.`,
    macFree: (gb: string) => `Liberi sul Mac: ${gb}`,
    reserve: (gb: string) => `Riserva: ${gb}`,
    general: "Generale",
    login: "Avvia Floppy al login",
    loginLine: "L'icona torna nella barra dei menu ogni volta che accendi il Mac. Il disco torna comunque da solo.",
    version: "Versione",
    versionLine: (v: string) => `Floppy ${v}`,
    engine: "Comando",
    engineLine: "Il programma che fa funzionare il disco su questo Mac.",
    support: "Assistenza",
    diagnostics: "Invia diagnostica",
    diagnosticsLine: "Manderà a chi gestisce Floppy:",
    diagnosticsItems: [
      "lo stato del disco",
      "gli ultimi log di Floppy e della macchina virtuale",
      "la versione di macOS",
    ],
    diagnosticsPrivacy: "Nessun file dei progetti, nessuna password.",
    diagnosticsSoon: "Arriva con il pannello di controllo",
    send: "Invia",
    unknown: "non disponibile",
  },

  welcome: {
    title: "Il disco del team, nel Finder",
    body: "Floppy mette sul tuo Mac i progetti del team. Stanno nel cloud: quelli che apri restano anche qui, e scorrono in timeline senza attese.",
    signIn: "Accedi",
    signInNote: "Si aprirà il browser: entri con l'email di lavoro, senza password.",
    invite: "Serve un invito del tuo team",
    placeholder: {
      readout: "ACCESSO DI PROVA",
      title: "L'accesso al team non c'è ancora",
      body: "Il pannello di controllo, dove si entra con l'email, non è ancora costruito. Per ora Floppy usa il pacchetto installato su questo Mac.",
      as: (name: string, team: string) => `Dati di esempio: ${name}, ${team}.`,
      next: "Continua",
    },
    missing: {
      title: "Manca il pacchetto di Floppy",
      body: "Per ora Floppy si installa dal pacchetto che ti manda chi gestisce il team: aprilo e segui le istruzioni. Poi torna qui.",
      check: "Controlla di nuovo",
    },
    prepare: {
      title: "Preparo Floppy su questo Mac",
      body: "Ci vuole circa un minuto e mezzo. Puoi lasciare la finestra aperta e fare altro.",
      start: "Prepara il disco",
      steps: ["Preparo il disco virtuale", "Avvio", "Collego il disco"],
    },
    spaceError: {
      title: "Serve più spazio su questo Mac",
      body: "Floppy ha bisogno di spazio libero per la sua cache. Libera spazio, per esempio svuotando il Cestino, poi riprova.",
    },
    otherError: {
      title: "La preparazione si è fermata",
      body: "Riprova tra poco; se succede ancora, manda il dettaglio qui sotto a chi gestisce il team.",
    },
    retry: "Riprova",
    ready: {
      title: "Floppy è nel Finder",
      body: "Da ora lo trovi nella barra dei menu, in alto a destra: un clic per vedere lo stato e rendere disponibili i progetti.",
      open: "Apri nel Finder",
      close: "Chiudi",
    },
    unreachable: {
      title: "Floppy è installato ma non risponde",
      body: "Il disco è preparato ma ora non è raggiungibile. Il pannello nella barra dei menu prova a ricollegarlo.",
    },
    off: {
      title: "Floppy è installato ed è spento",
      body: "Accendilo dal pannello nella barra dei menu quando ti serve.",
    },
  },

  notifications: {
    reconnected: { title: "Floppy è di nuovo collegato", body: "I progetti sono tornati nel Finder." },
    warmupDone: (name: string) => ({
      title: `${name} è pronto`,
      body: "È su questo Mac: scorre in timeline senza attese.",
    }),
  },

  errors: {
    busy: "Floppy sta già facendo un'altra operazione: aspetta che finisca.",
    not_mounted: "Il disco non è montato.",
    not_on_disk: "Questa cartella non è su Floppy.",
    nothing_to_cancel: "Non c'è niente da interrompere.",
    list_timeout: "Il disco non ha risposto in tempo.",
    engine: "Il comando di Floppy non ha risposto.",
    io: "Qualcosa non ha funzionato sul Mac.",
  } satisfies Record<CommandError["code"], string>,

  /** Sizes with their unit, for sentences. */
  size: gbUnit,
} as const;
