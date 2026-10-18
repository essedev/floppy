# Homebrew cask for the menu bar app (DECISIONS #10). The published copy lives
# in the essedev/homebrew-floppy tap: scripts/release-app.sh fills version and
# sha256 from the DMG that scripts/build-app.sh builds and writes it there.
cask "floppy" do
  version "0.1.0"
  sha256 :no_check

  url "https://github.com/essedev/floppy/releases/download/v#{version}/Floppy_#{version}_aarch64.dmg"
  name "Floppy"
  desc "Shared remote disk for video teams, in the menu bar"
  homepage "https://github.com/essedev/floppy"

  depends_on arch: :arm64
  depends_on macos: :ventura

  app "Floppy.app"

  # The app is signed ad hoc, not with a Developer ID, and not notarized:
  # Homebrew quarantines the download and Gatekeeper would refuse it. Clearing
  # the flag on this app only lets it open; the engine it installs into
  # ~/.floppy comes out of this bundle and inherits no flag.
  postflight_steps do
    run "/usr/bin/xattr", args: ["-cr", "{{appdir}}/Floppy.app"]
  end

  # Quit before the bundle is replaced, on uninstall and upgrade alike. The
  # login item goes only with zap: uninstall also runs on every upgrade.
  uninstall quit: "dev.esse.floppy.menubar"

  # The app's own files. The disk (~/.floppy with the VM, the engine's launchd
  # agent, the nsmb.conf block) is left alone even here: its metadata live in
  # the VM, and only `floppy uninstall`, which asks first, deletes them.
  zap launchctl: "dev.esse.floppy.menubar",
      trash:     [
        "~/Library/Application Support/dev.esse.floppy.menubar",
        "~/Library/Caches/dev.esse.floppy.menubar",
        "~/Library/LaunchAgents/dev.esse.floppy.menubar.plist",
        "~/Library/WebKit/dev.esse.floppy.menubar",
      ]
end
