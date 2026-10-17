import { describe, expect, it as test } from "vitest";
import { authFace, cleanCode, countdown, errorText, hasDevice, initialsOf, secondsUntil, serverEditable } from "@/features/account/view";
import type { AuthView } from "@/lib/types";

const base = {
  server: "https://x.it",
  server_display: "x.it",
  default_server: "https://x.it",
  busy: null,
  error: null,
  reachable: true,
} as const;
const out: AuthView = { ...base, phase: "signed_out", notice: null };
const waiting: AuthView = {
  ...base,
  phase: "code_sent",
  email: "g@x.it",
  device_name: "Mac",
  expires_at: "2026-10-17T20:30:00.123456Z",
  resend_at: "2026-10-17T20:16:00Z",
};
const inside: AuthView = { ...base, phase: "signed_in", email: "g@x.it", device_name: "Mac", organization: null, user_name: null };

describe("cleanCode", () => {
  test("keeps six digits from what was typed or pasted", () => {
    expect(cleanCode("123 456")).toBe("123456");
    expect(cleanCode("12-34-56-78")).toBe("123456");
    expect(cleanCode("abc")).toBe("");
  });
});

describe("initialsOf", () => {
  test("takes the first letters of the address parts", () => {
    expect(initialsOf("giulia.ferri@studio.it")).toBe("GF");
    expect(initialsOf("giulia@studio.it")).toBe("G");
    expect(initialsOf("@x")).toBe("?");
  });
});

describe("secondsUntil and countdown", () => {
  test("count down to server dates with microseconds", () => {
    const now = Date.parse("2026-10-17T20:15:18Z");
    expect(secondsUntil("2026-10-17T20:16:00Z", now)).toBe(42);
    expect(secondsUntil("2026-10-17T20:10:00Z", now)).toBe(0);
    expect(secondsUntil("not a date", now)).toBe(0);
    expect(secondsUntil(waiting.expires_at, now)).toBe(883);
    expect(countdown(42)).toBe("0:42");
    expect(countdown(75)).toBe("1:15");
  });
});

describe("errorText", () => {
  test("says how many attempts are left and how long to wait", () => {
    expect(errorText({ code: "invalid_code", attempts_left: 1 }, 0)).toContain("un tentativo");
    expect(errorText({ code: "invalid_code", attempts_left: 3 }, 0)).toContain("3 tentativi");
    expect(errorText({ code: "rate_limited", until_ms: 42_000 }, 0)).toContain("0:42");
    expect(errorText({ code: "rate_limited", until_ms: 1_000 }, 5_000)).toContain("Ora puoi");
    expect(errorText({ code: "invalid_server", reason: "insecure" }, 0)).toContain("https");
    expect(errorText({ code: "unreachable", detail: "x" }, 0)).toContain("non risponde");
  });
});

describe("authFace", () => {
  test("lights red only for what needs the person", () => {
    expect(authFace(out).led).toBe("off");
    expect(authFace({ ...out, notice: "revoked" }).led).toBe("err");
    expect(authFace(waiting)).toMatchObject({ led: "dl", blink: false });
    expect(authFace({ ...waiting, busy: "checking" })).toMatchObject({ led: "dl", blink: true });
    expect(authFace({ ...waiting, error: { code: "invalid_code", attempts_left: 2 } }).led).toBe("err");
    expect(authFace({ ...base, phase: "ended", reason: "locked", email: "g@x.it", device_name: "Mac" }).led).toBe("err");
    expect(authFace(inside).led).toBe("ok");
    expect(authFace({ ...inside, busy: "clearing" })).toMatchObject({ led: "dl", blink: true });
    for (const notice of ["unknown", "already_signed_out"] as const) {
      expect(authFace({ ...out, notice }).led).toBe("err");
    }
    expect(authFace({ ...out, notice: "signed_out" }).led).toBe("off");
  });
});

describe("hasDevice and serverEditable", () => {
  test("lock the server while a token exists or a request waits", () => {
    expect(hasDevice(inside)).toBe(true);
    expect(hasDevice(out)).toBe(false);
    expect(serverEditable(out)).toBe(true);
    expect(serverEditable(waiting)).toBe(false);
    expect(serverEditable(inside)).toBe(false);
    expect(serverEditable({ ...out, busy: "sending" })).toBe(false);
  });
});
