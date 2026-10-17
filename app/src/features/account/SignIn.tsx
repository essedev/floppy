/**
 * Signing this Mac in, as one flow for the first-run window and Settings:
 * the email (with the Mac's name and the server, changeable), the 6-digit
 * code or the link, the dead ends (expired, locked, opened elsewhere) and the
 * registration that follows. The state lives on the native side
 * (src-tauri/src/account): this view only renders it and sends the steps.
 */

import { type FormEvent, type ReactNode, useEffect, useId, useState } from "react";
import { Mail } from "lucide-react";
import { Button, CodeInput, Led, Notice, Readout, TextInput } from "@/components/ui";
import { useAuthStep } from "@/features/account/hooks";
import { countdown, errorDetail, errorText, secondsUntil, serverEditable } from "@/features/account/view";
import { it } from "@/i18n/it";
import { backend } from "@/lib/backend";
import type { AuthError, AuthView, CommandError } from "@/lib/types";

const t = it.account;

/** The current time, ticking each second while a countdown is shown. */
export function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

interface SignInProps {
  view: AuthView;
  /** The Mac's name from Sharing settings, the default device name. */
  computerName: string | null;
  /** Settings shows the server in its own row; the first run here. */
  showServer: boolean;
  /** Headings sized for the first-run window or for a Settings group. */
  size: "lg" | "md";
  /** A quiet way past the sign-in (first run only). */
  onLater?: () => void;
}

/** The steps before the Mac is registered: form, code, dead end, registering. */
export function SignIn({ view, computerName, showServer, size, onLater }: SignInProps) {
  switch (view.phase) {
    case "signed_out":
      return <EmailStep view={view} computerName={computerName} showServer={showServer} size={size} onLater={onLater} />;
    case "code_sent":
      return <CodeStep view={view} size={size} />;
    case "ended":
      return <EndedStep view={view} size={size} />;
    case "registering":
      return <RegisteringStep view={view} size={size} />;
    case "signed_in":
      return null;
  }
}

export function Heading({ title, body, size }: { title: string; body?: ReactNode; size: "lg" | "md" }) {
  return (
    <div>
      <h1 className={`m-0 font-bold tracking-[-0.02em] ${size === "lg" ? "text-[22px] leading-[1.2]" : "text-[17px] leading-[1.25]"}`}>{title}</h1>
      {body && <p className={`mt-1.5 mb-0 text-ink-2 ${size === "lg" ? "text-[14px]" : "text-[13px]"}`}>{body}</p>}
    </div>
  );
}

/** The last step's error as a notice with its LED; the detail in the readout font. */
export function AuthErrorNotice({ error, children }: { error: AuthError | null; children?: ReactNode }) {
  const now = useNow();
  if (!error) return null;
  const detail = errorDetail(error);
  return (
    <Notice led="err">
      <span role="alert">{errorText(error, now)}</span>
      {detail && <span className="mt-1 block font-ro text-[11.5px] text-ink-2">{detail}</span>}
      {children}
    </Notice>
  );
}

/** A refused step (another one running, a stale window). */
export function RefusedNotice({ error }: { error: CommandError | null }) {
  if (!error) return null;
  return <Notice led="err">{it.errors[error.code] ?? error.message}</Notice>;
}

function EmailStep({
  view,
  computerName,
  showServer,
  size,
  onLater,
}: {
  view: Extract<AuthView, { phase: "signed_out" }>;
  computerName: string | null;
  showServer: boolean;
  size: "lg" | "md";
  onLater?: () => void;
}) {
  const start = useAuthStep<{ email: string; deviceName: string }>(({ email, deviceName }) =>
    backend.authStart(email, deviceName),
  );
  const setServer = useAuthStep<string | null>(backend.authSetServer);
  const [email, setEmail] = useState("");
  const [deviceName, setDeviceName] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [server, setServerText] = useState(view.server);
  const emailId = useId();
  const nameId = useId();
  const serverId = useId();
  const name = deviceName ?? computerName ?? "Mac";
  const sending = view.busy === "sending" || start.isPending;
  const error = view.error;
  const serverChanged = server.trim() !== "" && server.trim() !== view.server;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (sending) return;
    // A server typed but not saved yet is saved first: it is the one signed in to.
    if (showServer && editing && serverChanged) {
      const saved = await setServer.mutateAsync(server.trim()).catch(() => null);
      if (!saved || saved.error) return;
    }
    start.mutate({ email: email.trim(), deviceName: name.trim() });
  };

  return (
    <form className="flex flex-col gap-3.5" onSubmit={(e) => void submit(e)} noValidate>
      {view.notice === "revoked" && (
        <Notice led="err" title={t.revoked.title}>
          {t.revoked.body}
        </Notice>
      )}
      {view.notice === "signed_out" && <Notice>{t.signedOutNote}</Notice>}
      {/* After a revocation the notice says what happened; the heading would repeat it. */}
      {view.notice !== "revoked" && <Heading title={t.form.title} body={t.form.body} size={size} />}
      <label htmlFor={emailId} className="flex flex-col gap-1.5">
        <span className="text-[13px] font-semibold">{t.form.email}</span>
        <TextInput
          id={emailId}
          type="email"
          autoComplete="email"
          autoFocus={size === "lg"}
          placeholder={t.form.emailPlaceholder}
          value={email}
          invalid={error?.code === "invalid_email"}
          disabled={sending}
          onChange={(e) => setEmail(e.target.value)}
        />
      </label>

      {editing ? (
        <div className="flex flex-col gap-3 rounded-xl bg-sunken p-3">
          <label htmlFor={nameId} className="flex flex-col gap-1.5">
            <span className="text-[13px] font-semibold">{t.form.deviceName}</span>
            <TextInput
              id={nameId}
              value={name}
              maxLength={100}
              invalid={error?.code === "invalid_device_name"}
              onChange={(e) => setDeviceName(e.target.value)}
            />
          </label>
          {showServer && (
            <label htmlFor={serverId} className="flex flex-col gap-1.5">
              <span className="text-[13px] font-semibold">{it.settings.server}</span>
              <TextInput
                id={serverId}
                value={server}
                autoComplete="url"
                spellCheck={false}
                placeholder={it.settings.serverPlaceholder}
                invalid={error?.code === "invalid_server"}
                disabled={!serverEditable(view)}
                onChange={(e) => setServerText(e.target.value)}
              />
            </label>
          )}
          <div>
            <Button
              size="sm"
              variant="primary"
              disabled={setServer.isPending}
              onClick={() => {
                if (showServer && serverChanged) {
                  setServer.mutate(server.trim(), { onSuccess: (v) => !v.error && setEditing(false) });
                } else {
                  setEditing(false);
                }
              }}
            >
              {t.form.done}
            </Button>
          </div>
        </div>
      ) : (
        <p className="m-0 flex flex-wrap items-baseline gap-x-2 text-[13px] text-ink-2">
          <span>{t.form.thisMac(name)}</span>
          {showServer && <span className="font-ro text-[11.5px]">{t.form.server(view.server_display)}</span>}
          <button type="button" className="border-0 bg-transparent p-0 text-[13px] font-semibold text-ink underline underline-offset-2" onClick={() => setEditing(true)}>
            {t.form.change}
          </button>
        </p>
      )}

      <AuthErrorNotice error={error} />
      <RefusedNotice error={start.error ?? setServer.error} />

      <div className="flex gap-2">
        <Button type="submit" variant="primary" size={size} className="flex-1" disabled={sending}>
          <Mail size={16} strokeWidth={1.7} aria-hidden="true" />
          {sending ? t.form.sending : t.form.send}
        </Button>
        {onLater && (
          <Button variant="ghost" size={size} onClick={onLater}>
            {t.form.later}
          </Button>
        )}
      </div>
    </form>
  );
}

function CodeStep({ view, size }: { view: Extract<AuthView, { phase: "code_sent" }>; size: "lg" | "md" }) {
  const submit = useAuthStep<string>(backend.authSubmitCode);
  const resend = useAuthStep(backend.authResend);
  const back = useAuthStep(backend.authBack);
  const [code, setCode] = useState("");
  const labelId = useId();
  const now = useNow();
  const busy = view.busy;
  const wrong = view.error?.code === "invalid_code";

  // A wrong code clears the boxes for the next try.
  useEffect(() => {
    if (wrong) setCode("");
  }, [wrong, view.error]);

  const send = (value: string) => {
    if (value.length !== 6 || busy) return;
    submit.mutate(value);
  };
  const resendIn = secondsUntil(view.resend_at, now);

  return (
    <div className="flex flex-col gap-4">
      {/* The first-run window has the faceplate for this. */}
      {size === "md" && (
        <span className="flex items-center gap-2">
          <Led state={view.error ? "err" : "dl"} blink={busy === "checking" || busy === "linking"} />
          <Readout>{t.code.state}</Readout>
        </span>
      )}
      <Heading title={t.code.title} body={t.code.body(view.email)} size={size} />
      <form
        className="notice flex flex-col gap-3 px-3.5 py-3.5"
        onSubmit={(e) => {
          e.preventDefault();
          send(code);
        }}
      >
        <p id={labelId} className="m-0 text-[13px] text-ink-2">
          {t.code.lead}
        </p>
        <CodeInput
          value={code}
          onChange={setCode}
          onComplete={send}
          disabled={busy != null}
          invalid={wrong}
          labelledBy={labelId}
          digitLabel={t.code.digit}
        />
        {busy === "checking" && <span className="text-[13px] text-ink-2">{t.code.checking}</span>}
        {busy === "linking" && <span className="text-[13px] text-ink-2">{t.code.linking}</span>}
        {wrong && view.error && !busy && (
          <span className="flex items-center gap-2 text-[13px]" role="alert">
            <Led state="err" />
            {errorText(view.error, now)}
          </span>
        )}
      </form>
      {!wrong && <AuthErrorNotice error={view.error} />}
      <RefusedNotice error={submit.error ?? resend.error ?? back.error} />
      <div className="flex flex-wrap gap-2">
        <Button className="flex-1" disabled={resendIn > 0 || busy != null || resend.isPending} onClick={() => resend.mutate()}>
          {resendIn > 0 ? t.code.resendIn(countdown(resendIn)) : t.code.resend}
        </Button>
        <Button variant="ghost" disabled={busy != null} onClick={() => back.mutate()}>
          {t.code.otherEmail}
        </Button>
      </div>
    </div>
  );
}

function EndedStep({ view, size }: { view: Extract<AuthView, { phase: "ended" }>; size: "lg" | "md" }) {
  const again = useAuthStep(backend.authResend);
  const back = useAuthStep(backend.authBack);
  const copy = t.ended[view.reason];
  const sending = view.busy === "sending" || again.isPending;
  return (
    <div className="flex flex-col gap-4">
      <Heading title={copy.title} body={copy.body} size={size} />
      <AuthErrorNotice error={view.error} />
      <RefusedNotice error={again.error ?? back.error} />
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" size={size} className="flex-1" disabled={sending} onClick={() => again.mutate()}>
          <Mail size={16} strokeWidth={1.7} aria-hidden="true" />
          {sending ? t.form.sending : t.ended.again}
        </Button>
        <Button variant="ghost" size={size} disabled={sending} onClick={() => back.mutate()}>
          {t.code.otherEmail}
        </Button>
      </div>
    </div>
  );
}

function RegisteringStep({ view, size }: { view: Extract<AuthView, { phase: "registering" }>; size: "lg" | "md" }) {
  const retry = useAuthStep(backend.authRetryRegister);
  const working = view.busy === "registering";
  return (
    <div className="flex flex-col gap-4">
      <span className="flex items-center gap-2">
        <Led state={view.error && !working ? "err" : "dl"} blink={working} />
        <Readout>{t.face.registering}</Readout>
      </span>
      <Heading title={t.registering.title} body={t.registering.body} size={size} />
      <AuthErrorNotice error={view.error} />
      <RefusedNotice error={retry.error} />
      {view.error && (
        <div>
          <Button variant="primary" size={size} disabled={working || retry.isPending} onClick={() => retry.mutate()}>
            {t.registering.retry}
          </Button>
        </div>
      )}
    </div>
  );
}
