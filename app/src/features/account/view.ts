/**
 * What the sign-in views show for an `AuthView`: the faceplate, the error
 * sentence and the small formatting the views share. Pure, so the state
 * machine's every state is checked in tests without a browser.
 */

import { it } from "@/i18n/it";
import type { AuthError, AuthView } from "@/lib/types";

/** The six digits of a typed or pasted code. */
export function cleanCode(value: string): string {
  return value.replace(/\D/g, "").slice(0, 6);
}

/** Initials for the account's round mark, from the address (giulia.ferri@ → GF). */
export function initialsOf(email: string): string {
  const parts = (email.split("@")[0] ?? "").split(/[._-]+/).filter(Boolean);
  const letters = parts.slice(0, 2).map((p) => p[0]!.toUpperCase());
  return letters.join("") || "?";
}

/** Seconds from `now` to an ISO date or a millisecond time, never below zero. */
export function secondsUntil(when: string | number, now: number): number {
  const at = typeof when === "number" ? when : Date.parse(when);
  if (Number.isNaN(at)) return 0;
  return Math.max(0, Math.ceil((at - now) / 1000));
}

/** A countdown as m:ss. */
export function countdown(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** The sentence for an error, in the dictionary's words. */
export function errorText(error: AuthError, now: number): string {
  const t = it.account.errors;
  switch (error.code) {
    case "invalid_code":
      return error.attempts_left != null ? t.attemptsLeft(error.attempts_left) : t.invalid_code;
    case "rate_limited": {
      const left = secondsUntil(error.until_ms, now);
      return left > 0 ? t.rate_limited(countdown(left)) : t.rateLimitedNow;
    }
    case "invalid_server":
      return t.invalid_server[error.reason];
    default:
      return t[error.code];
  }
}

/** The technical detail under an error, when there is one. */
export function errorDetail(error: AuthError): string | null {
  return "detail" in error ? error.detail : null;
}

/** The faceplate for the first-run window while signing in. */
export function authFace(view: AuthView): { led: "ok" | "dl" | "err" | "off"; blink: boolean; readout: string } {
  const f = it.account.face;
  const working = view.busy != null;
  switch (view.phase) {
    case "signed_out":
      if (view.notice === "revoked") return { led: "err", blink: false, readout: f.revoked };
      if (view.error && !working) return { led: "err", blink: false, readout: f.error };
      return { led: working ? "dl" : "off", blink: working, readout: working ? f.checking : f.signedOut };
    case "code_sent":
      if (working) return { led: "dl", blink: true, readout: f.checking };
      return { led: view.error ? "err" : "dl", blink: false, readout: view.error ? f.error : f.waiting };
    case "ended":
      return { led: "err", blink: false, readout: f.ended };
    case "registering":
      return working
        ? { led: "dl", blink: true, readout: f.registering }
        : { led: view.error ? "err" : "dl", blink: false, readout: view.error ? f.error : f.registering };
    case "signed_in":
      return { led: "ok", blink: false, readout: f.signedIn };
  }
}

/** The account is signed in or on its way (a token exists). */
export function hasDevice(view: AuthView): boolean {
  return view.phase === "signed_in" || view.phase === "registering";
}

/** The server address can be changed: signed out with nothing waiting. */
export function serverEditable(view: AuthView): boolean {
  return (view.phase === "signed_out" || view.phase === "ended") && view.busy == null;
}
