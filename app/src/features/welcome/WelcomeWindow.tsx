/**
 * The first-run window (concept-d-onboarding, DECISIONS #12): sign-in to the
 * team (the flow in features/account), then the engine: installed from the
 * app's bundle and set up in four steps (or updated, or only set up), or the
 * disk ready.
 */

import { type ComponentProps, type ReactNode, useState } from "react";
import { Faceplate } from "@/components/Faceplate";
import { Button, Led, Notice } from "@/components/ui";
import { useAuth } from "@/features/account/hooks";
import { SignIn } from "@/features/account/SignIn";
import { authFace } from "@/features/account/view";
import { faceFor } from "@/features/disk/face";
import { useAppInfo, useDiskAction, useSetup, useStatus } from "@/features/disk/hooks";
import { it } from "@/i18n/it";
import { backend } from "@/lib/backend";
import { gb } from "@/lib/format";
import type { AuthView, DiskStatus, SetupProgress } from "@/lib/types";

type Stage = "welcome" | "signin" | "engine";

function initialStage(): Stage {
  const asked = new URLSearchParams(location.search).get("stage");
  return asked === "engine" || asked === "signin" ? asked : "welcome";
}

export function WelcomeWindow() {
  const [stage, setStage] = useState<Stage>(initialStage);
  const { data: status } = useStatus();
  const { data: setup } = useSetup();
  const { data: auth } = useAuth();
  const info = useAppInfo();
  const signedIn = auth?.phase === "signed_in";

  return (
    // Room at the top for the window's traffic lights (title bar overlay).
    <main className="flex min-h-screen flex-col bg-surface pt-10">
      {stage === "welcome" && (
        <>
          <Face led="off" readout={it.face.notInstalled} />
          <Body>
            <Heading title={it.welcome.title} body={it.welcome.body} />
            <Button variant="primary" size="lg" className="w-full" disabled={!auth} onClick={() => setStage(signedIn ? "engine" : "signin")}>
              {signedIn ? it.welcome.continue : it.welcome.signIn}
            </Button>
            <p className="m-0 text-[13px] text-ink-2">{it.welcome.signInNote}</p>
            <Foot version={info.data?.version} right={it.welcome.invite} />
          </Body>
        </>
      )}
      {stage === "signin" && auth && (
        <SignInStage view={auth} computerName={info.data?.computer_name ?? null} version={info.data?.version} onNext={() => setStage("engine")} />
      )}
      {stage === "engine" && status && setup && (
        <EngineStage status={status} setup={setup} signedIn={signedIn} version={info.data?.version} />
      )}
    </main>
  );
}

function SignInStage({ view, computerName, version, onNext }: { view: AuthView; computerName: string | null; version?: string; onNext: () => void }) {
  const t = it.account;
  return (
    <>
      <Face {...authFace(view)} />
      <Body>
        {view.phase === "signed_in" ? (
          <>
            <Heading
              title={t.signedIn.title}
              body={view.organization ? `${t.signedIn.body(view.email)} ${t.signedIn.team(view.organization)}` : t.signedIn.body(view.email)}
            />
            <Notice>{it.settings.thisMacAs(view.device_name)}</Notice>
            <Button variant="primary" size="lg" className="w-full" onClick={onNext}>
              {t.signedIn.next}
            </Button>
          </>
        ) : (
          <SignIn view={view} computerName={computerName} showServer size="lg" onLater={view.phase === "signed_out" ? onNext : undefined} />
        )}
        <Foot version={version} right={view.phase === "signed_out" ? t.form.invite : undefined} />
      </Body>
    </>
  );
}

function EngineStage({
  status,
  setup,
  signedIn,
  version,
}: {
  status: DiskStatus;
  setup: SetupProgress;
  signedIn: boolean;
  version?: string;
}) {
  const install = useDiskAction(backend.installEngine);
  const setupOnly = useDiskAction(backend.runSetup);
  const openFinder = useDiskAction<string | undefined>(backend.openInFinder);
  // The bundle's install runs setup too; a plain setup is for an engine that
  // is already this app's but never finished preparing.
  const installing = status.install !== null || status.job?.kind === "install";
  const run = installing ? install : setupOnly;

  if (setup.running || (setup.error && !setup.done) || (status.state === "needs_setup" && !setup.done) || status.install !== null) {
    if (setup.error && !setup.running) {
      const space = setup.error.kind === "space";
      const free = status.space?.mac_free_mb;
      return (
        <>
          <Face
            led="err"
            readout={it.face.error}
            right={space && free != null ? it.face.freeOnMac(gb(free)) : ""}
          />
          <Body>
            <Heading
              title={space ? it.welcome.spaceError.title : it.welcome.otherError.title}
              body={space ? it.welcome.spaceError.body : it.welcome.otherError.body}
            />
            <span className="block font-ro text-[11.5px] text-ink-2">{setup.error.message}</span>
            <Button variant="primary" size="lg" className="w-full" onClick={() => run.mutate()}>
              {it.welcome.retry}
            </Button>
            <Foot version={version} />
          </Body>
        </>
      );
    }
    const p = it.welcome.prepare;
    const update = status.install === "update";
    // A setup alone starts at the second step: the copy is not part of it.
    const first = installing ? 0 : 1;
    const steps = p.steps.slice(first);
    return (
      <>
        <Face
          led={setup.running ? "dl" : "off"}
          blink={setup.running}
          readout={setup.running ? it.face.starting : it.face.needsSetup}
          fill={setup.running ? (setup.step - first + 0.5) / steps.length : 0}
        />
        <Body>
          <Heading title={update ? p.updateTitle : p.title} body={update ? p.updateBody : p.body} />
          <ol className="m-0 flex list-none flex-col rounded-[14px] bg-sunken p-1.5">
            {steps.map((label, at) => {
              const i = at + first;
              const done = setup.step > i;
              const now = setup.running && setup.step === i;
              return (
                <li
                  key={label}
                  className={`grid min-h-11 grid-cols-[22px_minmax(0,1fr)] items-center gap-2.5 rounded-[9px] px-3 text-[14px] ${now ? "bg-raised" : ""} ${!done && !now ? "text-ink-2" : ""}`}
                >
                  <span className="justify-self-center">
                    <Led state={done ? "ok" : now ? "dl" : undefined} blink={now} />
                  </span>
                  <span>{label}</span>
                </li>
              );
            })}
          </ol>
          {!setup.running && (
            <Button variant="primary" size="lg" className="w-full" disabled={run.isPending} onClick={() => run.mutate()}>
              {status.install === "fresh" ? p.install : update ? p.update : p.start}
            </Button>
          )}
          {run.error && <Notice led="err">{it.errors[run.error.code] ?? run.error.message}</Notice>}
          <Foot version={version} />
        </Body>
      </>
    );
  }

  if (status.state === "not_installed") {
    // Installed from the bundle but with no disk configured yet, or a build
    // without the engine at all.
    const copy = status.bundled ? it.welcome.notConfigured : it.welcome.missing;
    return (
      <>
        <Face led="off" readout={it.face.notInstalled} />
        <Body>
          <Heading title={copy.title} body={copy.body} />
          <Button variant="primary" size="lg" className="w-full" onClick={() => void backend.refresh()}>
            {copy.check}
          </Button>
          <Foot version={version} />
        </Body>
      </>
    );
  }

  const close = { open: null, close: it.welcome.ready.close };
  const noDisks = signedIn ? it.welcome.noDisks.signedIn : it.welcome.noDisks.signedOut;
  const copy =
    status.state === "connected"
      ? it.welcome.ready
      : status.state === "off"
        ? { ...it.welcome.off, ...close }
        : status.state === "no_disks"
          ? { ...noDisks, ...close }
          : status.state === "starting"
            ? { ...it.welcome.starting, ...close }
            : { ...it.welcome.unreachable, ...close };
  return (
    <>
      <Face {...faceFor(status)} />
      <Body>
        <Heading title={copy.title} body={copy.body} />
        {status.team?.refused_single && (
          <Notice led="err" title={it.team.single.title}>
            {it.team.single.body}
          </Notice>
        )}
        <div className="flex gap-2">
          {copy.open && (
            <Button variant="primary" size="lg" className="flex-1" onClick={() => openFinder.mutate(undefined)}>
              {copy.open}
            </Button>
          )}
          <Button variant={copy.open ? "ghost" : "primary"} size="lg" className={copy.open ? "" : "flex-1"} onClick={() => void backend.finishWelcome()}>
            {copy.close}
          </Button>
        </div>
        <Foot version={version} />
      </Body>
    </>
  );
}

function Face(props: Partial<ComponentProps<typeof Faceplate>> & { led: "ok" | "dl" | "err" | "off"; readout: string }) {
  return (
    <div className="mx-2">
      <Faceplate fill={0} {...props} />
    </div>
  );
}

function Body({ children }: { children: ReactNode }) {
  return <div className="flex flex-col gap-[18px] px-7 pt-[22px] pb-7">{children}</div>;
}

function Heading({ title, body }: { title: string; body: string }) {
  return (
    <div>
      <h1 className="m-0 text-[22px] leading-[1.2] font-bold tracking-[-0.02em]">{title}</h1>
      <p className="mt-1.5 mb-0 text-[14px] text-ink-2">{body}</p>
    </div>
  );
}

function Foot({ version, right }: { version?: string; right?: string }) {
  return (
    <div className="flex items-center justify-between pt-1 text-[13px] text-ink-3">
      <span className="font-ro text-[12px]">{version ? it.version(version) : it.product}</span>
      {right && <span>{right}</span>}
    </div>
  );
}
