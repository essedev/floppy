/**
 * The panel's row menus (concept-g-menus, variant A): one floating menu at a
 * time, anchored to its row's "…" button, closed by a click outside, Esc or a
 * choice. The window is as tall as the panel, so a menu that has no room
 * below opens upwards, and one with no room either way grows the panel.
 */

import { createContext, type ReactNode, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

interface MenuState {
  open: string | null;
  setOpen: (key: string | null) => void;
  setExtra: (px: number) => void;
}

const MenuContext = createContext<MenuState | null>(null);

/** Holds which menu is open, and the room a menu needs below the panel. */
export function MenuProvider({ children }: { children: ReactNode }) {
  const [open, setOpenState] = useState<string | null>(null);
  const [extra, setExtra] = useState(0);
  const setOpen = useCallback((key: string | null) => {
    setOpenState(key);
    if (key === null) setExtra(0);
  }, []);
  const value = useMemo(() => ({ open, setOpen, setExtra }), [open, setOpen]);
  return (
    <MenuContext.Provider value={value}>
      {children}
      {extra > 0 && <div aria-hidden="true" style={{ height: extra }} />}
    </MenuContext.Provider>
  );
}

/** One menu's state: open or not, and how to toggle or close it. */
export function useMenu(key: string) {
  const ctx = useContext(MenuContext);
  if (!ctx) throw new Error("useMenu outside MenuProvider");
  const { open, setOpen } = ctx;
  return {
    open: open === key,
    toggle: () => setOpen(open === key ? null : key),
    close: () => setOpen(null),
  };
}

const GAP = 4;

/**
 * The floating menu; its parent must be `relative` and hold the trigger. As a
 * `dialog` it carries text instead of items and spans its parent's width.
 */
export function RowMenu({
  label,
  onClose,
  role = "menu",
  children,
}: {
  label: string;
  onClose: () => void;
  role?: "menu" | "dialog";
  children: ReactNode;
}) {
  const ctx = useContext(MenuContext);
  const ref = useRef<HTMLDivElement>(null);
  const [up, setUp] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    const panel = el?.closest(".panel");
    const anchor = el?.parentElement;
    if (!el || !panel || !anchor) return;
    const p = panel.getBoundingClientRect();
    const a = anchor.getBoundingClientRect();
    const h = el.offsetHeight + GAP;
    const below = p.bottom - a.bottom;
    const above = a.top - p.top;
    if (h <= below) setUp(false);
    else if (h <= above) setUp(true);
    else {
      setUp(false);
      ctx?.setExtra(h - below + 8);
    }
  }, [ctx]);

  useEffect(() => {
    const el = ref.current;
    el?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus();
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      // The trigger toggles by itself: closing here would reopen it.
      if (el?.contains(t) || el?.parentElement?.querySelector("[data-menu-trigger]")?.contains(t)) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const items = [...(el?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? [])];
      if (items.length === 0) return;
      e.preventDefault();
      const i = items.indexOf(document.activeElement as HTMLElement);
      const next = e.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
      items[next]?.focus();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      role={role}
      aria-label={label}
      style={up ? { bottom: `calc(100% + ${GAP}px)` } : { top: `calc(100% + ${GAP}px)` }}
      className={`menu absolute z-20 flex flex-col rounded-xl ${role === "dialog" ? "inset-x-0 px-4 py-3.5" : "right-1 w-[256px] p-[5px]"}`}
    >
      {children}
    </div>
  );
}

/** A menu entry: its icon on the left, and an optional line saying what it does. */
export function MenuItem({
  icon,
  note,
  disabled,
  onClick,
  children,
}: {
  icon: ReactNode;
  note?: string;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onClick}
      className="menu-item grid cursor-pointer grid-cols-[18px_1fr] items-center gap-x-2.5 rounded-lg border-0 px-[9px] py-1.5 text-left text-[13px] text-ink outline-none disabled:cursor-default disabled:opacity-45"
    >
      <span className="flex text-ink-2" aria-hidden="true">
        {icon}
      </span>
      <span>{children}</span>
      {note && <small className="col-start-2 text-[11.5px] leading-[1.35] text-ink-2">{note}</small>}
    </button>
  );
}

export function MenuSeparator() {
  return <div role="separator" className="mx-2 my-1 h-px bg-[var(--menu-line)]" />;
}

/** The name of what the menu acts on, when the row does not say it near the menu. */
export function MenuHeading({ children }: { children: ReactNode }) {
  return <div className="truncate px-[9px] pt-1.5 pb-1 text-[11px] font-semibold tracking-[0.08em] text-ink-3 uppercase">{children}</div>;
}
