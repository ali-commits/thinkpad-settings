# ThinkPad BIOS Settings

A GTK4 desktop app for reading and changing Lenovo ThinkPad firmware settings
from inside Linux, without rebooting into BIOS setup.

[![CI](https://github.com/ali-commits/thinkpad-settings/actions/workflows/ci.yml/badge.svg?branch=dev)](https://github.com/ali-commits/thinkpad-settings/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ali-commits/thinkpad-settings?include_prereleases)](https://github.com/ali-commits/thinkpad-settings/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Lenovo ships no GUI for this on Linux. The kernel's `think_lmi` driver has
exposed these attributes for years, but the only ways to reach them were raw
`sysfs` writes or `fwupdmgr` on the command line — both requiring you to know
the exact attribute name *and* the exact spelling of the value.

## What it does

- Lists every firmware attribute your machine exposes, grouped into readable
  categories, each with a plain-English description of what it actually does
  **on Linux** — not a restatement of its name.
- **Stages changes and writes them in one batch**, so you authenticate once no
  matter how many settings you edit.
- **Warns before anything that can lock you out**: BIOS passwords,
  `LockBIOSSetting`, `BootOrderLock`, disabling the TPM on a machine that may
  use it to unlock an encrypted disk, and every value whose
  `PermanentlyDisable` cannot be undone.
- Search across all settings.
- Tells you when changes are staged but not yet active, and offers to restart.

## Requirements

- A Lenovo ThinkPad whose firmware exposes settings through `think_lmi`
  — check with `ls /sys/class/firmware-attributes/thinklmi/attributes/`
- `fwupd` 1.8+ (developed against 2.1.6)
- GTK 4, libadwaita 1.4+, PyGObject

## Install

### RPM (Fedora)

Download the `.rpm` from [Releases](https://github.com/ali-commits/thinkpad-settings/releases), then:

```
sudo dnf install ./thinkpad-settings-*.noarch.rpm
```

Dependencies resolve through dnf, the launcher appears in Activities, and
`man thinkpad-settings` works. Remove it with `sudo dnf remove thinkpad-settings`.

### Build the RPM yourself

```
sudo dnf install rpm-build rpmdevtools rpmlint python3-devel \
    pyproject-rpm-macros desktop-file-utils libappstream-glib
./build-rpm.sh
```

### Without a package manager

```
./install.sh      # installs under $HOME only, no root
./uninstall.sh    # undoes it
```

Don't use both at once — they install the same desktop ID, and the copy in
`~/.local/share/applications` wins over the packaged one.

Installing or removing never touches your firmware settings.

## How it works

Everything goes through fwupd's D-Bus API on the system bus:

| | |
|---|---|
| Read | `org.freedesktop.fwupd.GetBiosSettings` → polkit `auth_admin_keep` |
| Write | `org.freedesktop.fwupd.SetBiosSettings` → polkit `auth_admin` |

The app runs unprivileged. Nothing is setuid and it never invokes `sudo` or
`pkexec` — polkit prompts through GNOME's own dialog.

Going through fwupd rather than writing `sysfs` directly means values are
validated against the firmware's allowed list before being written, and fwupd
handles committing via `save_settings`.

`SetBiosSettings` takes a *dictionary*, which is what makes single-prompt
batching possible.

### The one non-obvious part

fwupd will not consult polkit **at all** unless the client first declares
`FwupdFeatureFlags.ALLOW_AUTHENTICATION` (`256`) via `SetFeatureFlags` on the
same connection. A client that skips it gets an immediate *successful* reply
with every value silently stripped out — no prompt, no error. Rendering that
reply naively shows a window full of settings that all read "Disabled", which
is fiction. This app declares the flag, and independently treats a
values-withheld reply as an authentication failure rather than as data.

## Caveats

- **Almost every setting needs a reboot.** A banner shows while changes are
  staged.
- **The boot order cannot be changed here.** fwupd validates a written value
  against the attribute's permitted values; `BootOrder` holds a colon-separated
  *sequence* while its permitted values are single device codes, so every
  reordering is rejected — and one rejected entry aborts the whole batch. It is
  shown read-only; change it in BIOS setup (F1).
- **fwupd caches settings at daemon startup.** If you change something in BIOS
  setup or via `sysfs`, fwupd reports the old value until restarted. Refresh
  re-reads fwupd but cannot defeat fwupd's own cache — run
  `sudo systemctl restart fwupd`, then Refresh.
- Settings the firmware marks read-only (such as `SecureBoot`) are shown but
  not editable here.
- If a supervisor password is set in BIOS, writes may be rejected; this app
  cannot supply that password.

## Other ThinkPads, other vendors

The **code** is generic: it renders whatever fwupd reports and hardcodes no
attribute names, vendor or model. It will run on any machine with a kernel
`firmware-attributes` driver, including Dell (`dell-wmi-sysman`) and HP
(`hp-bioscfg`).

The **catalog** — the friendly labels, Linux-specific descriptions, categories
and risk ratings — was captured on a ThinkPad T14 Gen 3 and covers its 104
attributes. Anything outside it still renders, with a name derived from the raw
attribute and marked as uncatalogued. Since Lenovo reuses attribute names
across the ThinkPad line, coverage on other ThinkPads should be high; on a Dell
it would be near zero and you'd get a working editor with no explanations.

Uncatalogued attributes whose names suggest passwords, locking, TPM or secure
wipe are treated as dangerous regardless, and any value containing "permanent"
warns on any hardware.

Want it tuned for your model? Open an issue with the output of
`sudo fwupdmgr get-bios-settings --json`.

## Development

```
python3 tests/smoke.py
```

90 checks against a captured attribute dump — no Lenovo hardware, no fwupd and
no authentication needed, only a display. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
