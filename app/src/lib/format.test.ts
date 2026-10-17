import { describe, expect, it } from "vitest";
import { fillOf, gb } from "@/lib/format";

describe("gb", () => {
  it("writes Italian decimals and drops a zero decimal", () => {
    expect(gb(48.2 * 1024)).toBe("48,2");
    expect(gb(20 * 1024)).toBe("20");
    expect(gb(214 * 1024)).toBe("214");
    expect(gb(0)).toBe("0");
  });
});

describe("fillOf", () => {
  it("is 0 when a value is unknown and never leaves 0..1", () => {
    expect(fillOf(null, 100)).toBe(0);
    expect(fillOf(50, 0)).toBe(0);
    expect(fillOf(150, 100)).toBe(1);
    expect(fillOf(25, 100)).toBe(0.25);
  });
});
