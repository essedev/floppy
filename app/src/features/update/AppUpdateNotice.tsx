/**
 * A newer release of the app, as the panel and Settings show it: offered,
 * installing with the download's progress, or failed with the command that
 * does it from the Terminal (updates.rs).
 */

import { Button, Notice } from "@/components/ui";
import { useAppUpdate, useDiskAction } from "@/features/disk/hooks";
import { it } from "@/i18n/it";
import { backend } from "@/lib/backend";
import { bytes } from "@/lib/format";
import type { AppUpdate } from "@/lib/types";

/** A newer release of the app: offered, installing with its progress, or failed with the command to run. */
export function AppUpdateNotice() {
  const update = useAppUpdate().data;
  const install = useDiskAction(backend.installAppUpdate);
  if (!update) return null;
  const t = it.appUpdate;
  const failed = update.state === "failed" || install.isError;
  const installing = update.state === "installing" && !install.isError;
  return (
    <Notice led={failed ? "err" : "dl"} title={failed ? t.failedTitle : installing ? t.installingTitle : t.title(update.version)}>
      {failed ? (
        <>
          <span className="block">{t.failedBody}</span>
          <span className="mt-1 block font-ro text-[11.5px] break-all text-ink select-text">{t.command}</span>
          {(update.error || install.error) && (
            <span className="mt-1 block font-ro text-[11.5px] text-ink-2">
              {update.error ?? it.errors[install.error!.code] ?? install.error!.message}
            </span>
          )}
        </>
      ) : installing ? (
        <InstallProgress update={update} />
      ) : (
        <span className="block">{t.body}</span>
      )}
      {!installing && (
        <Button
          size="sm"
          className="mt-2 bg-raised shadow-[0_0_0_0.5px_rgba(0,0,0,0.12)]"
          disabled={install.isPending}
          onClick={() => install.mutate()}
        >
          {failed ? t.retry : t.action}
        </Button>
      )}
    </Notice>
  );
}

/** Where an app update stands: the DMG is about 430 MB, so its download shows. */
function InstallProgress({ update }: { update: AppUpdate }) {
  const t = it.appUpdate;
  const share = update.step === "downloading" && update.total && update.downloaded != null ? Math.min(1, update.downloaded / update.total) : null;
  const line =
    update.step === "downloading" && update.downloaded != null
      ? t.downloading(bytes(update.downloaded), update.total ? bytes(update.total) : null)
      : update.step === "installing"
        ? t.installing
        : update.step === "preparing"
          ? t.preparing
          : t.installingBody;
  return (
    <>
      <span className="block">{line}</span>
      <span
        className="mt-2 block h-1 overflow-hidden rounded-sm bg-track"
        role="progressbar"
        aria-label={t.installingTitle}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={share != null ? Math.round(share * 100) : undefined}
      >
        <span
          className={`block h-full rounded-sm bg-ink-3 ${share == null ? "w-1/3 animate-pulse" : "transition-[width] duration-700"}`}
          style={share != null ? { width: `${(share * 100).toFixed(1)}%` } : undefined}
        />
      </span>
      <span className="mt-2 block text-ink-2">{t.keepWorking}</span>
    </>
  );
}
