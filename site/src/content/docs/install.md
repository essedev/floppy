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

The app needs no administrator password and installs no kernel extension.

## First start

1. Open **%name%** from the Applications folder. Its icon appears in the menu bar.
2. Choose **Sign in**: the browser opens, you enter your email and click the link you receive. The app never asks for a password.
3. The app creates the Linux VM on this Mac and shows the progress. It takes about a minute and a half and downloads nothing: everything it needs is in the app.
4. The disk appears in the Finder as **%name%**, under Locations, at `%volume%`.

## Working with the disk

- Open files straight from the Finder or from your editor. A clip you have never opened starts in under a second; one in the cache, at once. Without a network you can still browse the disk and play what is in the cache, but not save.
- Before editing a large project, choose **Make available** on its folder in the menu bar panel: the whole project comes into the cache.
- When the Mac runs low on space, choose **Free up space** on projects you are not editing. The files stay on the disk and download again when opened.

## Remove

```sh
brew uninstall --cask %cask%
```

TODO: whether uninstalling the cask also removes the VM and the cache, or the app does it from its settings, is not decided yet.
