/** Picks the view for this window: the panel, Settings, the disk window or the first run. */

import { DiskWindow } from "@/features/activity/DiskWindow";
import { Panel } from "@/features/panel/Panel";
import { SettingsWindow } from "@/features/settings/SettingsWindow";
import { WelcomeWindow } from "@/features/welcome/WelcomeWindow";

export function App({ view }: { view: string }) {
  if (view === "settings") return <SettingsWindow />;
  if (view === "welcome") return <WelcomeWindow />;
  if (view === "disk") return <DiskWindow />;
  return <Panel />;
}
