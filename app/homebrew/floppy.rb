# Homebrew cask template for the menu bar app (DECISIONS #10), modelled on
# Portsage's. The published copy lives in its own tap; version, sha256 and the
# release URL are filled at release time. The app is not signed or notarized
# yet: postflight clears the quarantine flag, as Portsage does.
cask "floppy" do
  version "0.1.0"
  sha256 :no_check

  url "https://github.com/essedev/floppy/releases/download/v#{version}/Floppy_#{version}_aarch64.dmg"
  name "Floppy"
  desc "Shared remote disk for video teams, in the menu bar"
  homepage "https://github.com/essedev/floppy"

  depends_on macos: ">= :ventura"
  depends_on arch: :arm64

  app "Floppy.app"

  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-cr", "#{appdir}/Floppy.app"]
  end

  # The app's own files only. The disk (~/.floppy, its VM and launchd agent)
  # is removed by `floppy uninstall`, which asks before deleting anything.
  zap trash: [
    "~/Library/Application Support/dev.esse.floppy.menubar",
    "~/Library/Caches/dev.esse.floppy.menubar",
    "~/Library/LaunchAgents/dev.esse.floppy.menubar.plist",
    "~/Library/WebKit/dev.esse.floppy.menubar",
  ]
end
