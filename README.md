# ThinkPad BIOS Settings

A GTK4 desktop app for reading and changing Lenovo ThinkPad firmware settings
from inside Linux, without rebooting into BIOS setup.

Built because Lenovo ships no GUI for this on Linux. The kernel's `think_lmi`
driver has exposed these attributes for years, but the only ways to reach them
were raw `sysfs` writes or `fwupdmgr` on the command line — both of which
require knowing the exact attribute name and the exact spelling of the value.

## What it does

- Lists every firmware attribute your machine exposes, grouped into readable
  categories, with a plain-English description of what each one actually does
  on Linux.
- **Stages changes and writes them in one batch**, so you authenticate once no
  matter how many settings you edit.
- Warns before anything that can lock you out — BIOS passwords, `LockBIOSSetting`,
  `BootOrderLock`, disabling the TPM on a machine that may use it to unlock an
  encrypted disk, and the settings whose `PermanentlyDisable` value cannot be
  undone.
- Gives `BootOrder` a real reorder editor instead of a combo box. The firmware
  stores it as a colon-separated ordered list, not a single choice, so a combo
  box would silently reduce your boot order to one device.
- Tells you when changes are staged but not yet applied, and offers to restart.

## Requirements

- A Lenovo ThinkPad whose firmware exposes settings through `think_lmi`
  (check: `ls /sys/class/firmware-attributes/thinklmi/attributes/`)
- `fwupd` 1.8+ (this was developed against 2.1.6)
- GTK 4, libadwaita 1.4+, PyGObject

On Fedora:

```
sudo dnf install python3-gobject gtk4 libadwaita fwupd
```

## Install

### RPM (recommended on Fedora)

```
sudo dnf install ./thinkpad-settings-0.1.0-1.fc44.noarch.rpm
```

Dependencies are resolved by dnf, the launcher appears in Activities, and
`man thinkpad-settings` works. To remove it:

```
sudo dnf remove thinkpad-settings
```

To build the RPM yourself:

```
sudo dnf install rpm-build rpmdevtools rpmlint python3-devel \
    pyproject-rpm-macros desktop-file-utils libappstream-glib
./build-rpm.sh
```

### Without a package manager

```
./install.sh
```

Installs entirely under `$HOME` — no root, nothing in `/usr`. Undo with
`./uninstall.sh`.

Do not use both at once: the two install the same desktop ID, and the copy in
`~/.local/share/applications` wins over the packaged one.

Either way, your firmware settings are never touched by installing or removing.

## How it works

Everything goes through fwupd's D-Bus API on the system bus:

| | |
|---|---|
| Read | `org.freedesktop.fwupd.GetBiosSettings` → polkit `auth_admin_keep` |
| Write | `org.freedesktop.fwupd.SetBiosSettings` → polkit `auth_admin` |

The app itself runs unprivileged. Nothing is setuid, and it never invokes
`sudo` or `pkexec` — polkit prompts through GNOME's own authentication dialog.

Going through fwupd rather than writing `sysfs` directly means the value is
validated against the firmware's allowed list before it is written, and fwupd
handles committing via `save_settings`. It also means this should work on any
vendor with a `firmware-attributes` driver, though only Lenovo/`think_lmi` has
been tested.

`SetBiosSettings` takes a dictionary, which is what makes single-prompt batching
possible.

## Tests

```
python3 tests/smoke.py
```

Builds the real window against a captured attribute dump from a T14 Gen 3, so
it needs no Lenovo hardware, no fwupd and no authentication — only a display.
It covers parsing, every category rendering, search, staging and unstaging,
the batched apply payload, revert, boot-order round-tripping, danger-ranking,
D-Bus error classification, and that merely *building* the UI never stages a
change (libadwaita's `Adw.ComboRow` fires `notify::selected` when its model is
set, indistinguishable from a click).

## Caveats

- **Almost every setting needs a reboot** to take effect. The app shows a banner
  when changes are staged.
- **`fwupd` caches settings at daemon startup.** If you change a setting outside
  this app — in BIOS setup, or by writing `sysfs` directly — fwupd keeps
  reporting the old value until the daemon restarts, and this app can only show
  what fwupd tells it. Refresh re-reads fwupd, but it cannot defeat fwupd's own
  cache. If a value looks wrong, run `sudo systemctl restart fwupd` and then
  Refresh.
- Settings marked read-only by the firmware (such as `SecureBoot`) are shown but
  cannot be edited here. Change those in BIOS setup with F1 at boot.
- If a supervisor password is set in BIOS, writes may be rejected. This app
  cannot supply that password.
- Some settings are genuinely irreversible from Linux. Read the warnings.
