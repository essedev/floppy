---
paths:
  - "app/**"
---
# The menu bar app

- **The app never remounts the disk by itself**: it reports the state and runs `floppy start` only when the user asks (CLAUDE.md, "Never unmount or remount a mounted disk automatically").
- **Base CSS rules go in `@layer base`**: outside a layer they beat Tailwind's utilities (a primary button rendered invisible).
- **Tray images are drawn 18 pt high**: the template PNGs are padded to 24x18 for that.
- **The engine is reached only through `engine.rs`**, with the binary from `FLOPPY_BIN` or `$FLOPPY_HOME/bin/floppy` (default `~/.floppy`) and a fixed PATH; tests use fake `floppy` scripts and fake bundles in temp folders, never the real install.
- **The engine is installed only by the bundled `install.sh`** (`installer.rs`), never copied by the app itself; an update over a working disk waits for the user, because `floppy setup` restarts the drive.
