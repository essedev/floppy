/** Number formats for the Italian UI. */

const one = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 0 });

/** A size in GB from MB: at most one decimal under 100 GB, whole above. */
export function gb(mb: number): string {
  const g = mb / 1024;
  return g >= 100 || g === 0 ? whole.format(g) : one.format(g);
}

/** The same with its unit. */
export function gbUnit(mb: number): string {
  return `${gb(mb)} GB`;
}

/** The share of the cache in use, 0 to 1; 0 when unknown. */
export function fillOf(used: number | null, limit: number | null): number {
  if (used == null || limit == null || limit <= 0) return 0;
  return Math.min(1, Math.max(0, used / limit));
}

/** A size in bytes with its unit: KB, MB or GB, at most one decimal under 100. */
export function bytes(n: number): string {
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  if (n > 0 && v < 1) v = 1;
  return `${v >= 100 ? whole.format(v) : one.format(v)} ${units[u]}`;
}
