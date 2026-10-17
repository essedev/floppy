import { useId } from "react";

/** LED colours on the dark Grafite body, the same in both themes (DECISIONS #11). */
const LED = { ok: "#3fdc6c", dl: "#ff6a2e", err: "#ff4b33", off: "#3a3834" } as const;

interface FaceplateProps {
  /** The LED: green connected, orange activity, red unreachable, off. */
  led: keyof typeof LED;
  /** Blink the LED: only for real activity or a start in progress. */
  blink?: boolean;
  /** The state readout under the LED, uppercase. */
  readout: string;
  /** How full the slot is, 0 to 1: the cache in use. */
  fill: number;
  /** The readout at the end of the slot (usage, free space). */
  right?: string;
  /** The fill's leading block blinks with the LED while data moves; colour stays in the LED. */
  activity?: boolean;
  /** Dims the fill when the disk is off. */
  dim?: boolean;
}

/**
 * The Grafite faceplate, geometry and colours from "Marchio definitivo" in
 * docs/concepts/concept-c-app.html (design/mark/faceplate.svg): the slot fills
 * with the cache, the LED sits on the slot's axis.
 */
export function Faceplate({ led, blink, readout, fill, right = "", activity, dim }: FaceplateProps) {
  const gradient = useId();
  const w = fill > 0 ? Math.max(6, Math.min(1, fill) * 206) : 0;
  const lit = led !== "off";
  return (
    <svg viewBox="0 0 336 112" className="block h-auto w-full" role="img" aria-label={readout}>
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#3a3835" />
          <stop offset="1" stopColor="#1c1b19" />
        </linearGradient>
      </defs>
      <rect x=".5" y=".5" width="335" height="111" rx="12" fill={`url(#${gradient})`} stroke="#0f0f0e" />
      <rect x="1.5" y="1.5" width="333" height="109" rx="11" fill="none" stroke="#fff" strokeOpacity=".08" />
      <rect x="24" y="34" width="212" height="12" rx="6" fill="#0b0b0a" />
      {w > 0 && <rect x="27" y="37" width={w} height="6" rx="3" fill="#d9d4c8" opacity={dim ? 0.45 : 1} />}
      {activity && w > 14 && (
        <rect x={27 + w - 14} y="37" width="14" height="6" rx="3" fill="#d9d4c8" className="face-blink" />
      )}
      <text x="24" y="76" fontFamily="Schibsted Grotesk" fontWeight="800" fontSize="15" letterSpacing="-.4" fill="#8f8b83">
        floppy
      </text>
      <text x="236" y="76" textAnchor="end" fontFamily="Departure Mono" fontSize="11" fill="#b3aea5">
        {right}
      </text>
      <circle cx="290" cy="40" r="10" fill="#0b0b0a" />
      <g className={blink ? "face-blink" : undefined}>
        <circle cx="290" cy="40" r="5.5" fill={LED[led]} />
        {lit && <ellipse cx="290" cy="37.6" rx="2.6" ry="1.4" fill="#fff" opacity=".5" />}
      </g>
      <text x="290" y="76" textAnchor="middle" fontFamily="Departure Mono" fontSize="10" letterSpacing=".5" fill="#b3aea5">
        {readout}
      </text>
    </svg>
  );
}
