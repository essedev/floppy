/**
 * The Settings window (concept-c-app, DECISIONS #12: its own window): the
 * account (sign-in, or who is signed in with sign-out), the server for a
 * self-hosted team, this Mac, space, general and support. Diagnostics are
 * shown but not sendable yet.
 */

import { type ReactNode, useState } from "react";
import { Button, Confirm, Led, Notice, Readout, SlotMark, TextInput, Toggle } from "@/components/ui";
import { useAuth, useAuthStep } from "@/features/account/hooks";
import { AuthErrorNotice, RefusedNotice, SignIn } from "@/features/account/SignIn";
import { initialsOf, serverEditable } from "@/features/account/view";
import { useAppInfo, useLogin, useStatus } from "@/features/disk/hooks";
import { it } from "@/i18n/it";
import { backend } from "@/lib/backend";
import { fillOf, gb, gbUnit } from "@/lib/format";
import type { AuthView } from "@/lib/types";

export function SettingsWindow() {
  const info = useAppInfo();
  const auth = useAuth();
  const { data: status } = useStatus();
  const { query: login, set: setLogin } = useLogin();
  const space = status?.space;
  const used = space?.cache_used_mb ?? null;
  const limit = space?.cache_limit_mb ?? null;
  const view = auth.data;
  const deviceName = view && (view.phase === "signed_in" || view.phase === "registering") ? view.device_name : null;

  return (
    <main className="flex min-h-screen flex-col gap-[18px] bg-surface px-[22px] pt-[18px] pb-6">
      <Group title={it.settings.account}>
        {view ? (
          <AccountRows view={view} computerName={info.data?.computer_name ?? null} />
        ) : auth.isError ? (
          <div className="p-3">
            <Notice led="err">{it.errors.io}</Notice>
          </div>
        ) : (
          <Row>
            <Label title="…" />
          </Row>
        )}
      </Group>

      {view && view.phase !== "signed_in" && view.phase !== "registering" && <ServerGroup view={view} />}

      <Group title={it.settings.thisMac}>
        <Row>
          <Label
            title={info.data?.computer_name ?? it.settings.thisMacUnknown}
            line={
              deviceName && deviceName !== info.data?.computer_name ? it.settings.thisMacAs(deviceName) : it.settings.thisMacLine
            }
          />
        </Row>
      </Group>

      <Group title={it.settings.space}>
        <Row column>
          <div className="flex items-baseline justify-between gap-3">
            <Label title={it.settings.maxSpace} line={it.settings.maxSpaceLine} />
            <span className="font-ro text-[15px] text-ink">{limit != null ? gbUnit(limit) : it.settings.unknown}</span>
          </div>
        </Row>
        <Row column>
          <div className="flex items-baseline justify-between gap-3">
            <Label title={it.settings.inUse} line={it.settings.inUseLine(gbUnit(space?.reserve_mb ?? 20480))} />
            <span className="font-ro text-[12.5px] text-ink">
              {used != null && limit != null ? it.space.of(gb(used), gb(limit)) : it.settings.unknown}
            </span>
          </div>
          <div className="flex h-2 overflow-hidden rounded bg-track" aria-hidden="true">
            <i className="block h-full bg-ink" style={{ width: `${fillOf(used, limit) * 100}%` }} />
          </div>
          <div className="flex justify-between font-ro text-[11.5px] text-ink-2">
            <span>{it.settings.macFree(space?.mac_free_mb != null ? gbUnit(space.mac_free_mb) : it.settings.unknown)}</span>
            <span>{it.settings.reserve(gbUnit(space?.reserve_mb ?? 20480))}</span>
          </div>
        </Row>
      </Group>

      <Group title={it.settings.general}>
        <Row>
          <Label title={it.settings.login} line={it.settings.loginLine} />
          <Toggle
            checked={login.data ?? false}
            disabled={login.isPending || setLogin.isPending}
            label={it.settings.login}
            onChange={(on) => setLogin.mutate(on)}
          />
        </Row>
        <Row>
          <Label title={it.settings.version} line={info.data ? it.settings.versionLine(info.data.version) : "…"} />
        </Row>
        <Row>
          <Label title={it.settings.engine} line={it.settings.engineLine} />
          <span className="max-w-[45%] truncate font-ro text-[11.5px] text-ink-2" title={info.data?.engine_path}>
            {info.data?.engine_path}
          </span>
        </Row>
      </Group>

      <Group title={it.settings.support}>
        <Row>
          <span className="min-w-0 flex-1">
            <b className="block text-[13.5px] font-semibold">{it.settings.diagnostics}</b>
            <span className="block text-[13px] text-ink-2">{it.settings.diagnosticsLine}</span>
            <ul className="m-0 mt-1.5 grid list-none gap-1 p-0 text-[13px] text-ink-2">
              {it.settings.diagnosticsItems.map((t) => (
                <li key={t} className="flex items-baseline">
                  <span className="-translate-y-0.5">
                    <SlotMark />
                  </span>
                  {t}
                </li>
              ))}
            </ul>
            <span className="mt-1.5 block text-[13px] text-ink-2">{it.settings.diagnosticsPrivacy}</span>
            <span className="mt-2 flex items-center gap-2">
              <Led />
              <Readout>{it.settings.diagnosticsSoon}</Readout>
            </span>
          </span>
          <Button disabled>{it.settings.send}</Button>
        </Row>
      </Group>
    </main>
  );
}

/** Who is signed in, with sign-out; the sign-in flow otherwise. */
function AccountRows({ view, computerName }: { view: AuthView; computerName: string | null }) {
  const signOut = useAuthStep<boolean>(backend.authSignOut);
  const [confirming, setConfirming] = useState(false);
  const t = it.account;

  if (view.phase !== "signed_in") {
    return (
      <div className="px-4 py-4">
        <SignIn view={view} computerName={computerName} showServer={false} size="md" />
      </div>
    );
  }
  const busy = view.busy === "signing_out";
  const failed = view.error?.code === "sign_out_failed";
  const notes = confirming || view.error != null || signOut.error != null || !view.reachable;
  return (
    <>
      <Row>
        <span className="grid size-9 shrink-0 place-items-center rounded-full bg-ink text-[13px] font-bold text-surface" aria-hidden="true">
          {initialsOf(view.email)}
        </span>
        <Label title={view.email} line={it.settings.accountLine(view.organization, view.device_name)} />
        <Button disabled={busy || confirming} onClick={() => setConfirming(true)}>
          {busy ? t.signOut.signingOut : t.signOut.action}
        </Button>
      </Row>
      {notes && (
        <div className="flex flex-col gap-2.5 px-3 py-3">
          {!view.reachable && <Notice led="dl">{t.offline}</Notice>}
          {confirming && !failed && (
            <Confirm
              title={t.signOut.title}
              body={t.signOut.body}
              confirm={t.signOut.confirm}
              cancel={it.panel.cancel}
              onConfirm={() => {
                setConfirming(false);
                signOut.mutate(false);
              }}
              onCancel={() => setConfirming(false)}
            />
          )}
          <AuthErrorNotice error={view.error}>
            {failed && (
              <span className="mt-2.5 flex flex-col items-start gap-2">
                <span className="text-[13px] text-ink-2">{t.signOut.localOnlyNote}</span>
                <span className="flex flex-wrap gap-2">
                  <Button size="sm" variant="primary" disabled={busy} onClick={() => signOut.mutate(false)}>
                    {t.registering.retry}
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => signOut.mutate(true)}>
                    {t.signOut.localOnly}
                  </Button>
                </span>
              </span>
            )}
          </AuthErrorNotice>
          <RefusedNotice error={signOut.error} />
        </div>
      )}
      <Row>
        <Label title={it.settings.server} line={it.settings.serverLine} />
        <span className="max-w-[45%] truncate font-ro text-[11.5px] text-ink-2" title={view.server}>
          {view.server_display}
        </span>
      </Row>
    </>
  );
}

/** The server for the next sign-in: the build's default or a self-hosted one. */
function ServerGroup({ view }: { view: AuthView }) {
  const setServer = useAuthStep<string | null>(backend.authSetServer);
  const [text, setText] = useState(view.server);
  const isDefault = view.server === view.default_server;
  const editable = serverEditable(view);
  const changed = text.trim() !== "" && text.trim() !== view.server;
  const serverError = view.error?.code === "invalid_server" || view.error?.code === "settings" ? view.error : null;
  return (
    <Group title={it.settings.server}>
      <Row column>
        <span className="text-[13px] text-ink-2">{it.settings.serverLine}</span>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (changed) setServer.mutate(text.trim(), { onSuccess: (v) => !v.error && setText(v.server) });
          }}
        >
          <TextInput
            aria-label={it.settings.server}
            value={text}
            spellCheck={false}
            autoComplete="url"
            placeholder={it.settings.serverPlaceholder}
            invalid={serverError != null}
            disabled={!editable || setServer.isPending}
            onChange={(e) => setText(e.target.value)}
            className="font-ro text-[12.5px]"
          />
          <Button type="submit" size="lg" disabled={!editable || !changed || setServer.isPending}>
            {it.settings.serverSave}
          </Button>
        </form>
        <div className="flex min-h-8 items-center justify-between gap-3">
          <Readout>{isDefault ? it.settings.serverDefault : view.server_display}</Readout>
          {!isDefault && (
            <Button
              size="sm"
              variant="ghost"
              disabled={!editable || setServer.isPending}
              onClick={() => setServer.mutate(null, { onSuccess: (v) => setText(v.server) })}
            >
              {it.settings.serverReset}
            </Button>
          )}
        </div>
        <AuthErrorNotice error={serverError} />
        <RefusedNotice error={setServer.error} />
      </Row>
    </Group>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="m-0 mb-2 ml-1 text-[11px] font-semibold tracking-[0.1em] text-ink-3 uppercase">{title}</h2>
      <div className="well rounded-xl bg-raised [&>*+*]:border-t [&>*+*]:border-line">{children}</div>
    </section>
  );
}

function Row({ children, column }: { children: ReactNode; column?: boolean }) {
  return (
    <div className={`flex min-h-[52px] gap-3.5 px-4 py-3 ${column ? "flex-col items-stretch gap-2.5" : "items-center"}`}>
      {children}
    </div>
  );
}

function Label({ title, line }: { title: string; line?: string }) {
  return (
    <span className="min-w-0 flex-1">
      <b className="block text-[13.5px] font-semibold">{title}</b>
      {line && <span className="block text-[13px] text-ink-2">{line}</span>}
    </span>
  );
}
