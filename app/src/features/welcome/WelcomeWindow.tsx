/**
 * The first-run window (concept-d-onboarding, DECISIONS #12). Sign-in is a
 * placeholder until the control plane exists; after it the window follows the
 * engine: no package, `floppy setup` in three steps, or the disk ready.
 */

import { type ComponentProps, type ReactNode, useState } from "react";
import { Faceplate } from "@/components/Faceplate";
import { Button, Led, Notice, Readout } from "@/components/ui";
import { faceFor } from "@/features/disk/face";
import { useAppInfo, useDiskAction, useSetup, useStatus } from "@/features/disk/hooks";
import { it } from "@/i18n/it";
import { backend } from "@/lib/backend";
import { gb } from "@/lib/format";
import type { DiskStatus, SetupProgress } from "@/lib/types";

type Stage = "welcome" | "signin" | "engine";

export function WelcomeWindow() {
  const [stage, setStage] = useState<Stage>(() =>
    new URLSearchParams(location.search).get("stage") === "engine" ? "engine" : "welcome",
  );
  const { data: status } = useStatus();
  const { data: setup } = useSetup();
  const info = useAppInfo();

  return (
    // Room at the top for the window's traffic lights (title bar overlay).
    <main className="flex min-h-screen flex-col bg-surface pt-10">
      {stage === "welcome" && (
        <>
          <Face led="off" readout={it.face.notInstalled} />
          <Body>
            <Heading title={it.welcome.title} body={it.welcome.body} />
            <Button variant="primary" size="lg" className="w-full" onClick={() => setStage("signin")}>
              {it.welcome.signIn}
            </Button>
            <p className="m-0 text-[13px] text-ink-2">{it.welcome.signInNote}</p>
            <Foot version={info.data?.version} right={it.welcome.invite} />
          </Body>
        </>
      )}
      {stage === "signin" && (
        <>
          <Face led="ok" readout="PROVA" />
          <Body>
            <span className="flex items-center gap-2">
              <Led state="ok" />
              <Readout>{it.welcome.placeholder.readout}</Readout>
            </span>
            <Heading title={it.welcome.placeholder.title} body={it.welcome.placeholder.body} />
            {info.data && (
              <Notice>{it.welcome.placeholder.as(info.data.account.name, info.data.account.team)}</Notice>
            )}
            <Button variant="primary" size="lg" className="w-full" onClick={() => setStage("engine")}>
              {it.welcome.placeholder.next}
            </Button>
            <Foot version={info.data?.version} />
          </Body>
        </>
      )}
      {stage === "engine" && status && setup && <EngineStage status={status} setup={setup} version={info.data?.version} />}
    </main>
  );
}

function EngineStage({ status, setup, version }: { status: DiskStatus; setup: SetupProgress; version?: string }) {
  const run = useDiskAction(backend.runSetup);
  const openFinder = useDiskAction<string | undefined>(backend.openInFinder);

  if (setup.running || (setup.error && !setup.done) || (status.state === "needs_setup" && !setup.done)) {
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
    return (
      <>
        <Face led={setup.running ? "dl" : "off"} blink={setup.running} readout={setup.running ? it.face.starting : it.face.needsSetup} fill={setup.running ? (setup.step + 0.5) / 3 : 0} />
        <Body>
          <Heading title={it.welcome.prepare.title} body={it.welcome.prepare.body} />
          <ol className="m-0 flex list-none flex-col rounded-[14px] bg-sunken p-1.5">
            {it.welcome.prepare.steps.map((label, i) => {
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
              {it.welcome.prepare.start}
            </Button>
          )}
          {run.error && <Notice led="err">{it.errors[run.error.code] ?? run.error.message}</Notice>}
          <Foot version={version} />
        </Body>
      </>
    );
  }

  if (status.state === "not_installed") {
    return (
      <>
        <Face led="off" readout={it.face.notInstalled} />
        <Body>
          <Heading title={it.welcome.missing.title} body={it.welcome.missing.body} />
          <Button variant="primary" size="lg" className="w-full" onClick={() => void backend.refresh()}>
            {it.welcome.missing.check}
          </Button>
          <Foot version={version} />
        </Body>
      </>
    );
  }

  const copy =
    status.state === "connected"
      ? it.welcome.ready
      : status.state === "off"
        ? { ...it.welcome.off, open: null, close: it.welcome.ready.close }
        : { ...it.welcome.unreachable, open: null, close: it.welcome.ready.close };
  return (
    <>
      <Face {...faceFor(status)} />
      <Body>
        <Heading title={copy.title} body={copy.body} />
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
