/**
 * The Settings window (concept-c-app, DECISIONS #12: its own window). Account
 * and diagnostics wait for the control plane: the account is the marked fake
 * from src-tauri/src/fake.rs, diagnostics are shown but not sendable.
 */

import type { ReactNode } from "react";
import { Button, Led, Notice, Readout, SlotMark, Toggle } from "@/components/ui";
import { useAppInfo, useLogin, useStatus } from "@/features/disk/hooks";
import { it } from "@/i18n/it";
import { fillOf, gb, gbUnit } from "@/lib/format";

export function SettingsWindow() {
  const info = useAppInfo();
  const { data: status } = useStatus();
  const { query: login, set: setLogin } = useLogin();
  const account = info.data?.account;
  const space = status?.space;
  const used = space?.cache_used_mb ?? null;
  const limit = space?.cache_limit_mb ?? null;

  return (
    <main className="flex min-h-screen flex-col gap-[18px] bg-surface px-[22px] pt-[18px] pb-6">
      <Group title={it.settings.account}>
        {account ? (
          <>
            <Row>
              <span className="grid size-9 shrink-0 place-items-center rounded-full bg-ink text-[13px] font-bold text-surface">
                {account.initials}
              </span>
              <Label title={account.name} line={it.settings.accountLine(account.email, account.team)} />
              {account.fake && <Readout>{it.settings.accountFake}</Readout>}
              <Button disabled>{it.settings.signOut}</Button>
            </Row>
            {account.fake && (
              <div className="px-3 pb-3">
                <Notice>{it.settings.accountFakeNote}</Notice>
              </div>
            )}
          </>
        ) : (
          <Row>
            <Label title="…" />
          </Row>
        )}
      </Group>

      <Group title={it.settings.thisMac}>
        <Row>
          <Label title={info.data?.computer_name ?? it.settings.thisMacUnknown} line={it.settings.thisMacLine} />
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
