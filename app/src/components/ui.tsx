/**
 * The small pieces every view shares: detail set A from design/tokens.css
 * (LED in its bezel, readout, slot mark, notice) and the buttons of the concept.
 */

import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from "react";

interface LedProps {
  /** Lit colour; none for an unlit lens. */
  state?: "ok" | "dl" | "err";
  /** Blink only for real activity. */
  blink?: boolean;
}

export function Led({ state, blink }: LedProps) {
  return <span className="led" data-state={state} data-blink={blink ? "" : undefined} aria-hidden="true" />;
}

export function Readout({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`readout ${className}`}>{children}</span>;
}

/** The slot mark before list items and notes; `color` tints it in legends. */
export function SlotMark({ color }: { color?: string }) {
  const style: CSSProperties | undefined = color ? { background: color } : undefined;
  return <span className="slot-mark" style={style} aria-hidden="true" />;
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** primary: the one large action; soft: secondary; ghost: quiet. */
  variant?: "primary" | "soft" | "ghost";
  size?: "lg" | "md" | "sm";
}

const VARIANT = {
  primary: "bg-ink text-surface font-semibold",
  soft: "bg-sunken text-ink font-semibold",
  ghost: "bg-transparent text-ink-2 font-medium hover:bg-sunken",
};
const SIZE = {
  lg: "h-11 px-4 text-[14px] rounded-xl",
  md: "h-9 px-3.5 text-[13px] rounded-[10px]",
  sm: "h-8 px-3 text-[13px] rounded-[9px]",
};

export function Button({ variant = "soft", size = "md", className = "", ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-2 whitespace-nowrap border-0 disabled:opacity-45 ${VARIANT[variant]} ${SIZE[size]} ${className}`}
      {...rest}
    />
  );
}

interface NoticeProps {
  /** A state notice carries a LED; a plain note carries the slot mark. */
  led?: "ok" | "dl" | "err";
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
}

/** A sunken surface, never a coloured background. */
export function Notice({ led, title, children, className = "" }: NoticeProps) {
  return (
    // The slot mark carries its own 10px gap; the LED gets the same from the flex gap.
    <div className={`notice flex items-start px-3.5 py-3 text-[13px] leading-[1.45] text-ink ${led ? "gap-2.5" : ""} ${className}`}>
      <span className={`flex shrink-0 ${led ? "mt-[2px]" : "mt-[7px]"}`}>{led ? <Led state={led} /> : <SlotMark />}</span>
      <div className="min-w-0 flex-1">
        {title && <b className="block font-bold">{title}</b>}
        {children}
      </div>
    </div>
  );
}

interface ConfirmProps {
  title: ReactNode;
  body: ReactNode;
  confirm: string;
  cancel: string;
  onConfirm: () => void;
  onCancel: () => void;
  className?: string;
}

/** An inline confirmation that says what happens, not only "are you sure". */
export function Confirm({ title, body, confirm, cancel, onConfirm, onCancel, className = "" }: ConfirmProps) {
  return (
    <div
      role="alertdialog"
      className={`rounded-xl bg-raised p-3.5 shadow-[0_0_0_0.5px_rgba(0,0,0,0.12),0_12px_28px_-12px_rgba(40,30,20,0.35)] ${className}`}
    >
      <b className="mb-1 block text-[14px]">{title}</b>
      <p className="m-0 mb-3 text-[13px] text-ink-2">{body}</p>
      <div className="flex gap-2">
        <Button variant="primary" size="md" className="flex-1" onClick={onConfirm}>
          {confirm}
        </Button>
        <Button variant="soft" size="md" className="flex-1" onClick={onCancel}>
          {cancel}
        </Button>
      </div>
    </div>
  );
}

interface ToggleProps {
  checked: boolean;
  label: string;
  onChange: (on: boolean) => void;
  disabled?: boolean;
}

export function Toggle({ checked, label, onChange, disabled }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-[22px] w-[38px] shrink-0 rounded-full border-0 transition-colors ${checked ? "bg-[var(--good)]" : "bg-track"}`}
    >
      <span
        className={`absolute top-[2px] left-[2px] size-[18px] rounded-full bg-white shadow-[0_1px_2px_rgba(0,0,0,0.3)] transition-transform ${checked ? "translate-x-4" : ""}`}
      />
    </button>
  );
}
