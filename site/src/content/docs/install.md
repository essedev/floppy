---
title: Install on a Mac
description: Install the app with Homebrew, sign in, and find the disk in the Finder.
---

## Before you start

- A Mac with Apple Silicon and macOS 13 or later.
- [Homebrew](https://brew.sh) installed.
- An invite from your team's admin: it arrives by email.

## Install

```sh
brew install --cask %cask%
```

The app needs no administrator password and installs no kernel extension. Everything the disk needs (the Linux VM's image, JuiceFS, the packages) is inside the app, so nothing else is downloaded.

The app is not notarized by Apple: the cask clears the quarantine flag on it, and macOS opens it without a warning. Use the full name above: it also tells Homebrew to trust this one cask from the tap.

## First start

1. Open **%name%** from the Applications folder. Its icon appears in the menu bar.
2. If your team runs its own %name% server, enter its address in the sign-in window.
3. Choose **Sign in**: the browser opens, you enter your email and click the link you receive. The app never asks for a password.
4. Choose **Install %name%**: the app copies the disk's engine onto this Mac, creates the Linux VM and shows the progress. It takes about a minute and a half and downloads nothing.
5. The disk appears in the Finder as **%name%**, under Locations, at `%volume%`.

## Updates

```sh
brew upgrade --cask %cask%
```

Open the app again after the upgrade. When the new version brings a new engine, the panel says so and applies it when you choose **Update now**: the disk restarts, so save and close open projects first. Your files, the cache and the disk's settings stay as they are.

## Working with the disk

- Open files straight from the Finder or from your editor. A clip you have never opened starts in under a second; one in the cache, at once. Without a network you can still browse the disk and play what is in the cache, but not save.
- Before editing a large project, choose **Make available** on its folder in the menu bar panel: the whole project comes into the cache.
- When the Mac runs low on space, choose **Free up space** on projects you are not editing. The files stay on the disk and download again when opened.

## Remove

```sh
brew uninstall --cask %cask%
```

This removes the app only. The disk on this Mac, with its VM and cache in `~/.%slug%`, stays, and reinstalling the app finds it as it was. Removing it too, after your team's admin agrees, is `~/.%slug%/bin/%slug% uninstall`, which asks for confirmation.
