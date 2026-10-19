/** Picks the view for this window: the panel, Settings, the trash or the first run. */

import { Panel } from "@/features/panel/Panel";
import { SettingsWindow } from "@/features/settings/SettingsWindow";
import { TrashWindow } from "@/features/trash/TrashWindow";
import { WelcomeWindow } from "@/features/welcome/WelcomeWindow";

export function App({ view }: { view: string }) {
  if (view === "settings") return <SettingsWindow />;
  if (view === "welcome") return <WelcomeWindow />;
  if (view === "trash") return <TrashWindow />;
  return <Panel />;
}
