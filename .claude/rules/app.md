---
paths:
  - "app/**"
---
# The menu bar app

- **The app never remounts the disk by itself**: it reports the state and runs `floppy start` only when the user asks (CLAUDE.md, "Never unmount or remount a mounted disk automatically").
- **Base CSS rules go in `@layer base`**: outside a layer they beat Tailwind's utilities (a primary button rendered invisible).
- **Tray images are drawn 18 pt high**: the template PNGs are padded to 24x18 for that.
- **The engine is reached only through `engine.rs`**, with the binary from `FLOPPY_BIN` or `~/.floppy/bin/floppy` and a fixed PATH; tests use fake `floppy` scripts in temp folders, never the real install.
