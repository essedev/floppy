/** How each state reads on the faceplate: LED, readout, slot. */

import type { ComponentProps } from "react";
import type { Faceplate } from "@/components/Faceplate";
import { it } from "@/i18n/it";
import { fillOf, gb } from "@/lib/format";
import type { DiskStatus } from "@/lib/types";

type FaceProps = ComponentProps<typeof Faceplate>;

/** True while the app moves data: a warmup or a free it started. */
export function isBusy(s: DiskStatus): boolean {
  return s.state === "connected" && (s.job?.kind === "warmup" || s.job?.kind === "free");
}

/** The faceplate for a status. */
export function faceFor(s: DiskStatus): FaceProps {
  const used = s.space?.cache_used_mb ?? null;
  const limit = s.space?.cache_limit_mb ?? null;
  const fill = fillOf(used, limit);
  const right = used != null && limit != null ? it.face.usage(gb(used), gb(limit)) : "";
  switch (s.state) {
    case "connected":
      return isBusy(s)
        ? { led: "dl", blink: true, readout: it.face.activity, fill, right, activity: true }
        : { led: "ok", readout: it.face.connected, fill, right };
    case "starting":
      return { led: "dl", blink: true, readout: it.face.starting, fill, right };
    case "stopping":
      return { led: "dl", blink: true, readout: it.face.stopping, fill, right };
    case "unreachable":
      return { led: "err", readout: it.face.unreachable, fill, right };
    case "off":
      return { led: "off", readout: it.face.off, fill, right, dim: true };
    case "needs_setup":
      return { led: "off", readout: it.face.needsSetup, fill: 0 };
    case "not_installed":
      return { led: "off", readout: it.face.notInstalled, fill: 0 };
  }
}
