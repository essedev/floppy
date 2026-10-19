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
    noDisks: "NESSUN DISCO",
    problem: "PROBLEMA",
    error: "ERRORE",
    ready: "PRONTO",
    usage: (used: string, limit: string) => `${used} / ${limit} GB`,
    freeOnMac: (gb: string) => `${gb} GB LIBERI`,
  },

  panel: {
    label: "Pannello di Floppy",
    connected: { title: "Floppy è collegato", body: "I progetti sono nel Finder, sotto Floppy." },
    connectedTeam: { title: "Floppy è collegato", body: "I dischi del team sono nel Finder, dentro Floppy." },
    failing: {
      title: (n: number) => (n === 1 ? "1 disco con un problema" : `${n} dischi con un problema`),
      body: "Gli altri sono nel Finder. Riprovo da solo: il dettaglio è sulla riga qui sotto.",
    },
    noDisks: {
      signed_out: {
        title: "Accedi per vedere i dischi",
        body: "Floppy è pronto su questo Mac: i dischi del team arrivano dopo l'accesso.",
      },
      registering: { title: "Collego questo Mac al team", body: "Ancora un momento: poi arrivano i dischi." },
      none: {
        title: "Nessun disco per te, per ora",
        body: "Quando chi gestisce il team ti dà un disco, compare qui da solo.",
      },
      all_off: { title: "Hai spento tutti i dischi", body: "Riaccendine uno qui sotto per ritrovarlo nel Finder." },
      signIn: "Accedi",
    },
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
          case "restart":
            return "Non sono riuscito a riavviare Floppy";
          case "setup":
            return "La preparazione non è andata a buon fine";
          case "install":
            return "L'installazione non è andata a buon fine";
        }
      },
    },
    engineDetail: "Dettaglio del comando",
  },

  team: {
    restart: {
      title: "Floppy va riavviato",
      body: "Con i nuovi dischi gli serve più memoria. Riavvia quando non stai lavorando.",
      action: "Riavvia",
      confirmTitle: "Riavviare Floppy adesso?",
      confirmBody: "Salva e chiudi i progetti aperti: per qualche secondo i dischi spariscono dal Finder.",
      confirm: "Riavvia",
    },
    single: {
      title: "Questo Mac usa ancora il disco di prova",
      body: "I dischi del team non possono stare accanto al disco di prova. Chiedi a chi ti ha installato Floppy di toglierlo: dopo arrivano da soli.",
    },
    error: { title: "Non riesco ad aggiornare i dischi", body: "Riprovo da solo tra un minuto." },
    rejected: (name: string) => `«${name}» non si può collegare su questo Mac: avvisa chi gestisce il team.`,
    applying: "Aggiorno i dischi…",
  },

  disks: {
    heading: "Dischi del team",
    readout: {
      mounted: "COLLEGATO",
      readOnly: "SOLA LETTURA",
      offline: "OFFLINE",
      waiting: "IN ATTESA",
      starting: "AVVIO",
      failed: "ERRORE",
      off: "SPENTO",
      leaving: "IN USO",
      removed: "TOLTO",
      unknown: "IN VERIFICA",
    },
    line: {
      mounted: "Pronto",
      readOnly: "Puoi aprire i file, non modificarli",
      offline: "Il team non risponde: puoi aprire i file, non salvarli",
      offlineRead: "Il team non risponde: i file si aprono lo stesso",
      becomingReadOnly: "Diventa di sola lettura appena chiudi i file aperti",
      becomingWritable: "Potrai modificarlo appena chiudi i file aperti",
      updating: "Si aggiorna appena chiudi i file aperti",
      starting: "Lo sto collegando…",
      failed: "Non si è collegato: riprovo da solo",
      stopped: "Non è collegato: riprovo da solo",
      off: "Spento su questo Mac",
      leaving: "Si spegne appena chiudi i file aperti",
      removed: "Il team te l'ha tolto: sparisce appena chiudi i file aperti",
    },
    cache: (size: string) => `${size} su questo Mac`,
    actions: (name: string) => `Azioni per ${name}`,
    projects: (name: string) => `Progetti di ${name}`,
    showInFinder: "Mostra nel Finder",
    freeSpace: "Libera spazio",
    turnOff: "Spegni su questo Mac",
    turnOn: "Accendi su questo Mac",
    offConfirm: {
      title: (name: string) => `Spegnere ${name} su questo Mac?`,
      body: "Sparisce dal Finder e le sue copie locali vengono cancellate. I file restano sul cloud del team: lo riaccendi quando vuoi.",
      confirm: "Spegni",
    },
    empty: "Il disco è vuoto.",
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
      clearing: "TOLGO I DISCHI",
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
      body: "Chi gestisce il team gli ha tolto l'accesso: i dischi del team e le loro copie su questo Mac sono stati tolti. I file restano sul cloud del team. Per tornare, accedi di nuovo.",
      signIn: "Accedi di nuovo",
    },
    unknown: {
      title: "Il server non riconosce questo Mac",
      body: "Forse è stato tolto dal team, o all'indirizzo risponde un altro server. I dischi del team sono stati tolti da questo Mac: per tornare, accedi di nuovo.",
    },
    alreadySignedOut: {
      title: "Questo Mac era già uscito dal team",
      body: "I dischi del team sono stati tolti da questo Mac. Per tornare, accedi di nuovo.",
    },
    signedOutNote: "Sei uscito: questo Mac non è più collegato al team e i suoi dischi sono stati tolti.",
    signOut: {
      action: "Esci",
      signingOut: "Esco…",
      title: "Uscire da Floppy su questo Mac?",
      body: "Il Mac viene scollegato dal team: i dischi spariscono dal Finder e le loro copie locali vengono cancellate. I file restano sul cloud del team. Prima salva e chiudi i progetti aperti.",
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
      disks_not_cleared: "Non sono riuscito a togliere i dischi del team da questo Mac: riprovo da solo.",
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
    accountLine: (org: string | null, email: string) => (org ? `${org} · ${email}` : email),
    disks: "Dischi",
    disksLine: "Spegni un disco che non ti serve: sparisce dal Finder e libera spazio su questo Mac.",
    disksNone: "Nessun disco del team su questo Mac.",
    diskToggle: (name: string) => `Usa ${name} su questo Mac`,
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
    starting: {
      title: "Collego i dischi del team",
      body: "Pochi secondi e li trovi nel Finder, dentro Floppy.",
    },
    noDisks: {
      signedIn: {
        title: "Floppy è pronto",
        body: "I dischi del team arrivano nel Finder appena chi gestisce il team te ne dà uno.",
      },
      signedOut: { title: "Floppy è installato", body: "Accedi al tuo team per vedere i suoi dischi nel Finder." },
    },
  },

  notifications: {
    reconnected: { title: "Floppy è di nuovo collegato", body: "I progetti sono tornati nel Finder." },
    warmupDone: (name: string) => ({
      title: `${name} è pronto`,
      body: "È su questo Mac: scorre in timeline senza attese.",
    }),
  },

  trash: {
    title: "Cestino",
    disks: "Dischi",
    readOnly: "Sola lettura",
    thisDisk: "Floppy",
    loading: "Leggo il cestino…",
    refresh: "Aggiorna",
    retry: "Riprova",
    noDisks: {
      title: "Nessun disco collegato",
      body: "Il cestino si legge da un disco collegato a questo Mac: accendi Floppy o un disco dal pannello.",
    },
    search: "Cerca nome o cartella",
    searchLabel: "Cerca nel cestino",
    kinds: { all: "Tutto", deleted: "Cancellati", replaced: "Sostituiti" },
    kindLabel: "Tipo",
    count: (n: number, size: string) => `${n === 1 ? "1 elemento" : `${n} elementi`} · ${size}`,
    empty: {
      title: (disk: string) => `Il cestino di ${disk} è vuoto`,
      body: (disk: string, days: number) =>
        `Quello che si cancella da ${disk} resta qui ${days === 1 ? "un giorno" : `${days} giorni`}, poi se ne va da solo. Il Finder lo cancella subito, Floppy no.`,
      noTrash: (disk: string) => `${disk} non tiene un cestino: quello che si cancella se ne va subito.`,
    },
    noMatch: {
      title: "Nessun risultato",
      body: (disk: string, q: string) => (q ? `Niente nel cestino di ${disk} con «${q}».` : `Niente nel cestino di ${disk} con questo filtro.`),
      clear: "Togli i filtri",
    },
    readOnlyNote: (disk: string) =>
      `${disk} è in sola lettura per te: vedi cosa è stato cancellato, ma per ripristinarlo serve chi può modificare il disco.`,
    today: "Oggi",
    yesterday: "Ieri",
    left: (days: number) => (days <= 0 ? "si svuota oggi" : days === 1 ? "si svuota domani" : `si svuota tra ${days} giorni`),
    folderFrom: (n: number, from: string) => `Cartella con ${n === 1 ? "1 file" : `${n} file`} · da ${from}`,
    replacedFrom: (from: string) => `Versione precedente · era in ${from}`,
    from: (from: string) => `Da ${from}`,
    gone: "La sua cartella non c'è più: torna in cima al disco",
    you: "Tu",
    unknown: "non registrato",
    unknownTitle: "Chi l'ha cancellato non risulta: è successo prima che Floppy lo registrasse, o il server del team non risponde",
    restore: "Ripristina",
    restoreCopy: "Ripristina come copia",
    restoreFolder: "Ripristina la cartella",
    restoring: "Ripristino…",
    restored: "Ripristinato",
    restoredAs: (name: string) => `Ripristinato come «${name}»`,
    restoredTop: "Ripristinato in cima al disco",
    showInFinder: "Mostra nel Finder",
    notInTrash: "Non è più nel cestino: forse l'ha già ripristinato qualcun altro, o è scaduto.",
    failed: "Non sono riuscito a ripristinarlo.",
    foot: (days: number) =>
      `Dopo ${days === 1 ? "un giorno" : `${days} giorni`} un elemento lascia il cestino da solo. Finché c'è, occupa spazio nello storage del team.`,
    open: "Cestino",
  },

  /** The disk window around the two tabs (DECISIONS #27). */
  disk: {
    tabs: "Schede",
    activity: "Attività",
    trash: "Cestino",
  },

  activity: {
    open: "Attività",
    loading: "Leggo l'attività…",
    refresh: "Aggiorna",
    retry: "Riprova",
    peopleLabel: "Persona",
    everyone: "Tutti",
    typeLabel: "Tipo",
    types: { all: "Tutto", add: "Aggiunti", ren: "Rinominati e spostati", del: "Cancellati e sostituiti" },
    now: "Adesso",
    you: "Tu",
    someone: "Qualcuno",
    /** The verb's person: "Tu hai", "Marco ha". */
    has: (you: boolean) => (you ? "hai" : "ha"),
    created: (has: string, name: string) => `${has} creato la cartella ${name}`,
    added: (has: string, n: number, name: string) => `${has} aggiunto ${n === 1 ? name : `${n} file`}`,
    written: (has: string, n: number, name: string) => `${has} modificato ${n === 1 ? name : `${n} file`}`,
    replaced: (has: string, n: number, name: string) => `${has} sostituito ${n === 1 ? name : `${n} file`}`,
    deletedFolder: (has: string, name: string) => `${has} cancellato la cartella ${name}`,
    deleted: (has: string, n: number, name: string) => `${has} cancellato ${n === 1 ? name : `${n} file`}`,
    renamed: (has: string, from: string, to: string) => `${has} rinominato ${from} in ${to}`,
    moved: (has: string, name: string, where: string) => `${has} spostato ${name} in ${where}`,
    inFolder: (folder: string) => `in ${folder}`,
    fromFolder: (folder: string) => `da ${folder}`,
    files: (n: number, size: string | null) => [n === 1 ? "1 file" : `${n} file`, size].filter(Boolean).join(" · "),
    previousInTrash: "La versione precedente è nel cestino",
    showFiles: "Mostra i file",
    hideFiles: "Nascondi i file",
    andMore: (n: number) => `e altri ${n}`,
    restore: "Ripristina",
    restoreFolder: "Ripristina la cartella",
    restoreFiles: (n: number) => `Ripristina i ${n} file`,
    restoring: "Ripristino…",
    restored: (n: number) => (n === 1 ? "Ripristinato" : `Ripristinati ${n} elementi`),
    inTrash: "Nel cestino",
    undoRename: "Annulla rinomina",
    undoing: "Rimetto il nome…",
    renamedBack: "Nome di prima rimesso",
    showInFinder: "Mostra nel Finder",
    confirm: {
      title: "Rimettere il nome di prima?",
      body: (by: string, name: string) =>
        `La cartella torna ${name} e ${by} riceve un avviso. Se nel frattempo ha già ricollegato i suoi progetti, a lui si scollegano di nuovo.`,
      confirm: "Rimetti il nome",
      cancel: "Annulla",
    },
    refusal: {
      taken: (name: string) => `«${name}» è già usato da un'altra cartella: rinominala prima, poi riprova.`,
      gone: "La cartella non ha più quel nome: qualcuno l'ha già cambiato di nuovo.",
      no_folder: "La cartella in cui stava non c'è più.",
      not_in_trash: "Non è più nel cestino: forse l'ha già ripristinato qualcun altro, o è scaduto.",
    },
    failed: "Non è riuscito.",
    copying: (who: string, you: boolean, name: string) => `${who} ${you ? "stai" : "sta"} copiando ${name}`,
    writing: (who: string, you: boolean, name: string) => `${who} ${you ? "stai" : "sta"} scrivendo ${name}`,
    soFar: (size: string, speed: string | null) => (speed ? `${size} finora, circa ${speed}/s` : `${size} finora`),
    offline: (time: string) =>
      `Il server del team non risponde dalle ${time}: qui ci sono solo le operazioni di questo Mac. I colleghi tornano appena risponde.`,
    local: "Qui ci sono le operazioni di questo Mac.",
    empty: {
      title: (disk: string) => `Ancora niente su ${disk}`,
      body: "Qui compare chi aggiunge, rinomina, sposta e cancella file, per 30 giorni.",
    },
    noMatch: { title: "Niente con questi filtri", clear: "Togli i filtri" },
    more: "Mostra le precedenti",
    foot: "Le operazioni vicine della stessa persona nella stessa cartella sono un evento solo: copiare 48 clip è una riga, non 48. Chi ha il disco in sola lettura vede l'attività ma non ripristina e non annulla. L'attività resta 30 giorni.",
  },

  /** Colleagues' files on the projects' rows of the panel. */
  live: {
    arriving: "In arrivo",
    inUse: "In uso",
    from: (by: string, line: string) => `Da ${by}: ${line}`,
    files: (n: number, by: string) => `${n} file in arrivo da ${by}`,
    writing: (by: string, name: string) => `${by} sta scrivendo ${name}`,
    disk: (n: number, by: string) => `${n === 1 ? "1 file" : `${n} file`} in arrivo da ${by}`,
  },

  /** The activity's notices in the panel, each with its undo. */
  activityNotices: {
    massDelete: {
      title: (name: string | null, files: number, disk: string) =>
        name ? `Hai cancellato ${name} da ${disk}` : `Hai cancellato ${files} file da ${disk}`,
      body: (name: string | null, files: number) =>
        name
          ? `La cartella, con ${files === 1 ? "1 file" : `${files} file`}, resta nel cestino: puoi rimetterla com'era.`
          : "Restano nel cestino: puoi rimetterli com'erano.",
      undo: (name: string | null, files: number) => (name ? "Ripristina la cartella" : `Ripristina i ${files} file`),
      done: (name: string | null, files: number) => (name ? `${name} è di nuovo al suo posto` : `Ripristinati ${files} file`),
    },
    renamed: {
      title: (by: string, name: string) => `${by} ha rinominato ${name}`,
      movedTitle: (by: string, name: string) => `${by} ha spostato ${name}`,
      body: (to: string) => `Ora è ${to}. I progetti CapCut che usano quei video li cercano ancora al nome di prima: vanno ricollegati.`,
      movedBody: (where: string) => `Ora è in ${where}. I progetti CapCut che usano quei video li cercano ancora dov'erano: vanno ricollegati.`,
      mineTitle: (from: string, to: string) => `Hai rinominato ${from} in ${to}`,
      mineMovedTitle: (name: string, where: string) => `Hai spostato ${name} in ${where}`,
      mineBody: "Chi usa quei video in CapCut dovrà ricollegarli. Se è stato un errore, rimetti com'era.",
      undo: "Annulla rinomina",
      undoMine: "Annulla",
      ok: "Ho capito",
      done: "Nome di prima rimesso",
      doneBody: (name: string, by: string | null) => (by ? `${name} ha di nuovo il suo nome e ${by} lo sa.` : `${name} ha di nuovo il suo nome.`),
    },
    renamedBack: {
      title: (by: string) => `${by} ha rimesso il nome di prima`,
      body: (from: string, to: string) => `${from} è tornata ${to}: i progetti CapCut la ritrovano come prima.`,
    },
    working: "Un momento…",
    fine: "Va bene",
    close: "Chiudi",
    showInFinder: "Mostra nel Finder",
    failed: "Non è riuscito:",
  },

  /** macOS notifications about the disks' activity: they inform, the undo is in the panel. */
  alerts: {
    massDelete: (name: string | null, files: number, disk: string) => ({
      title: name ? `Hai cancellato ${name} da ${disk}` : `Hai cancellato ${files} file da ${disk}`,
      body: "Resta tutto nel cestino: lo ripristini dal pannello di Floppy.",
    }),
    renamed: (by: string, from: string, to: string) => ({
      title: `${by} ha rinominato ${from}`,
      body: `Ora è ${to}: i progetti CapCut che usano quei video vanno ricollegati. Apri Floppy per annullare.`,
    }),
    renamedMine: (from: string, to: string) => ({
      title: `Hai rinominato ${from} in ${to}`,
      body: "Chi usa quei video in CapCut dovrà ricollegarli. Puoi annullare dal pannello di Floppy.",
    }),
    renamedBack: (by: string, from: string, to: string) => ({
      title: `${by} ha rimesso il nome di prima`,
      body: `${from} è tornata ${to}.`,
    }),
    arriving: (by: string, file: string, folder: string, size: string | null) => ({
      title: `${file} non è ancora completo`,
      body: `${by} lo sta copiando in ${folder}${size ? `: per ora ci sono ${size}` : ""}. Se lo importi adesso in CapCut si ferma lì. Ti avviso quando ha finito.`,
    }),
    arrived: (by: string, folder: string) => ({
      title: `${by} ha finito di copiare in ${folder}`,
      body: "I file sono completi: se ne avevi importato uno a metà in CapCut, importalo di nuovo.",
    }),
  },

  errors: {
    busy: "Floppy sta già facendo un'altra operazione: aspetta che finisca.",
    not_mounted: "Il disco non è montato.",
    not_on_disk: "Questa cartella non è su Floppy.",
    nothing_to_cancel: "Non c'è niente da interrompere.",
    list_timeout: "Il disco non ha risposto in tempo.",
    no_bundle: "Questa copia dell'app non contiene il motore di Floppy.",
    not_team: "Questo Mac non usa ancora i dischi del team.",
    no_such_disk: "Questo disco non c'è più su questo Mac.",
    not_in_trash: "Non è più nel cestino: forse l'ha già ripristinato qualcun altro, o è scaduto.",
    no_such_notice: "Questo avviso non c'è più.",
    server: "Il server del team non ha risposto.",
    engine: "Il comando di Floppy non ha risposto.",
    io: "Qualcosa non ha funzionato sul Mac.",
    auth_busy: "Floppy sta già facendo un passo dell'accesso: aspetta che finisca.",
    auth_state: "Questo passo dell'accesso non vale più: la finestra si è aggiornata.",
  } satisfies Record<CommandError["code"], string>,

  /** Sizes with their unit, for sentences. */
  size: gbUnit,
} as const;
