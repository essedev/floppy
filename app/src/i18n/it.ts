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
      title: "Floppy non è ancora su questo Mac",
      body: "Manca un passo per installarlo: lo fai dalla finestra di preparazione.",
    },
    notConfigured: {
      title: "Floppy è installato, manca il disco",
      body: "Il disco del team arriva dopo l'accesso. Se hai già fatto l'accesso, avvisa chi gestisce il team.",
    },
    noBundle: {
      title: "Floppy non è installato su questo Mac",
      body: "Questa copia dell'app non contiene il motore di Floppy: installala con Homebrew.",
    },
    update: {
      title: "Un aggiornamento di Floppy è pronto",
      body: "Si applica riavviando il disco, in circa un minuto: prima salva e chiudi i progetti aperti.",
      action: "Aggiorna ora",
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
          case "install":
            return "L'installazione non è andata a buon fine";
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

  account: {
    face: {
      signedOut: "NUOVO",
      waiting: "IN ATTESA",
      checking: "CONTROLLO",
      ended: "DA RIFARE",
      registering: "COLLEGO",
      signedIn: "COLLEGATO",
      revoked: "SCOLLEGATO",
      error: "ERRORE",
    },
    form: {
      title: "Accedi al tuo team",
      body: "Ti mandiamo un link e un codice via email: niente password da ricordare.",
      email: "Email di lavoro",
      emailPlaceholder: "nome@studio.it",
      send: "Mandami il codice",
      sending: "Mando il codice…",
      thisMac: (name: string) => `Questo Mac comparirà come «${name}».`,
      server: (host: string) => `Server: ${host}`,
      change: "Modifica",
      done: "Fatto",
      deviceName: "Nome di questo Mac",
      later: "Più tardi",
      invite: "Serve un invito del tuo team",
    },
    code: {
      state: "IN ATTESA DEL CODICE",
      title: "Controlla la posta",
      body: (email: string) => `Abbiamo mandato un codice e un link a ${email}. Valgono 15 minuti.`,
      lead: "Scrivi il codice, oppure apri il link su questo Mac.",
      label: "Codice di 6 cifre",
      digit: (n: number) => `Cifra ${n} di 6`,
      checking: "Controllo il codice…",
      linking: "Link aperto, finisco l'accesso…",
      resendIn: (time: string) => `Nuovo codice tra ${time}`,
      resend: "Mandane un altro",
      otherEmail: "Usa un'altra email",
    },
    ended: {
      expired: {
        title: "Questo codice non vale più",
        body: "Codice e link valgono 15 minuti e si usano una volta sola. Chiedine uno nuovo: arriva in pochi secondi.",
      },
      locked: {
        title: "Troppi tentativi",
        body: "Dopo 5 codici sbagliati codice e link si bloccano. Chiedine uno nuovo.",
      },
      elsewhere: {
        title: "Il link è stato aperto su un altro dispositivo",
        body: "Il link funziona solo sul Mac che ha chiesto l'accesso. Chiedine uno nuovo e aprilo qui, oppure scrivi il codice.",
      },
      again: "Mandami un codice nuovo",
    },
    registering: {
      title: "Collego questo Mac al team",
      body: "Ancora un momento: preparo le chiavi di questo Mac.",
      retry: "Riprova",
    },
    signedIn: {
      title: "Sei dentro",
      body: (email: string) => `Hai fatto l'accesso come ${email}.`,
      team: (org: string) => `Team: ${org}.`,
      next: "Continua",
    },
    revoked: {
      title: "Questo Mac è stato scollegato dal team",
      body: "Chi gestisce il team gli ha tolto l'accesso. Il disco e i file su questo Mac non sono stati toccati: per tornare, accedi di nuovo.",
      signIn: "Accedi di nuovo",
    },
    signedOutNote: "Sei uscito: questo Mac non è più collegato al team.",
    signOut: {
      action: "Esci",
      signingOut: "Esco…",
      title: "Uscire da Floppy su questo Mac?",
      body: "Il Mac viene scollegato dal team e le sue chiavi tolte dal portachiavi. Il disco e i file non vengono toccati. Per tornare serve un nuovo accesso.",
      confirm: "Esci",
      localOnly: "Esci solo da questo Mac",
      localOnlyNote:
        "Il Mac resterà nell'elenco di chi gestisce il team finché non lo toglie: diglielo.",
    },
    offline: "Il server non risponde: riprovo da solo. Il disco intanto funziona come prima.",
    errors: {
      invalid_email: "Scrivi un indirizzo email valido.",
      invalid_device_name: "Dai un nome a questo Mac.",
      not_resent: "Hai chiesto troppi codici in un'ora: usa l'ultimo che ti è arrivato, o riprova più tardi.",
      mail_failed: "Il server non è riuscito a mandare la mail. Riprova tra poco; se succede ancora, avvisa chi gestisce il team.",
      metadata_unavailable: "Il server del team non risponde ancora: riprovo da solo tra poco.",
      unreachable: "Il server non risponde. Controlla la connessione e riprova.",
      rate_limited: (time: string) => `Troppi tentativi da questa rete. Riprova tra ${time}.`,
      rateLimitedNow: "Troppi tentativi da questa rete. Ora puoi riprovare.",
      invalid_code: "Codice sbagliato.",
      attemptsLeft: (n: number) => (n === 1 ? "Codice sbagliato. Ti resta un tentativo." : `Codice sbagliato. Ti restano ${n} tentativi.`),
      sign_out_failed: "Il server non risponde, quindi il Mac è ancora collegato al team.",
      keychain: "Il portachiavi del Mac non ha accettato le chiavi di Floppy.",
      settings: "Non sono riuscito a salvare l'impostazione.",
      server: "Il server ha risposto in modo inatteso.",
      invalid_server: {
        not_an_address: "Scrivi un indirizzo web, per esempio floppy.studio.it.",
        insecure: "Serve un indirizzo https: senza, le chiavi di questo Mac viaggerebbero in chiaro.",
        extra: "Scrivi solo l'indirizzo del server, senza altro dopo.",
      },
    },
  },

  settings: {
    title: "Impostazioni",
    account: "Account",
    accountLine: (org: string | null, device: string) => (org ? `${org} · ${device}` : device),
    server: "Server",
    serverLine: "L'indirizzo del pannello di controllo del team. Si cambia solo da disconnessi.",
    serverDefault: "predefinito",
    serverSave: "Salva",
    serverReset: "Torna al predefinito",
    serverPlaceholder: "floppy.studio.it",
    thisMac: "Questo Mac",
    thisMacUnknown: "Questo Mac",
    thisMacLine: "Chi gestisce il team lo vedrà con questo nome.",
    thisMacAs: (name: string) => `Il team lo vede come «${name}».`,
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
    signInNote: "Entri con l'email di lavoro, senza password.",
    continue: "Continua",
    invite: "Serve un invito del tuo team",
    missing: {
      title: "Manca il motore di Floppy",
      body: "Questa copia dell'app non lo contiene. Installa Floppy con Homebrew, poi torna qui.",
      check: "Controlla di nuovo",
    },
    notConfigured: {
      title: "Floppy è installato",
      body: "Manca solo il disco del team: arriva dopo l'accesso, appena il team ti dà accesso a un disco.",
      check: "Controlla di nuovo",
    },
    prepare: {
      title: "Preparo Floppy su questo Mac",
      body: "Ci vuole circa un minuto e mezzo. Puoi lasciare la finestra aperta e fare altro.",
      start: "Prepara il disco",
      install: "Installa Floppy",
      update: "Aggiorna Floppy",
      updateTitle: "Aggiorno Floppy su questo Mac",
      updateBody: "Floppy è già qui: lo porto alla versione dell'app. I file e il disco restano come sono; il disco si riavvia, quindi chiudi i progetti aperti.",
      steps: ["Copio Floppy su questo Mac", "Preparo il disco virtuale", "Avvio", "Collego il disco"],
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
    no_bundle: "Questa copia dell'app non contiene il motore di Floppy.",
    engine: "Il comando di Floppy non ha risposto.",
    io: "Qualcosa non ha funzionato sul Mac.",
    auth_busy: "Floppy sta già facendo un passo dell'accesso: aspetta che finisca.",
    auth_state: "Questo passo dell'accesso non vale più: la finestra si è aggiornata.",
  } satisfies Record<CommandError["code"], string>,

  /** Sizes with their unit, for sentences. */
  size: gbUnit,
} as const;
