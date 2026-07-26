# Contributing

Thanks for taking a look. This project touches **firmware settings on real
laptops**, so the bar for correctness is higher than the size of the codebase
suggests: a bug here can leave someone unable to boot or locked out of their own
BIOS. Most of the guidance below follows from that.

## Ground rules

**Never display a value you did not actually read.** The single worst bug found
during development was defaulting a missing value to `""`, which rendered 103
settings as "Disabled" as though that were the machine's real state. If a value
is unknown, say unknown.

**Never widen a warning into noise.** Confirmation dialogs only fire for
genuinely irreversible or lock-you-out changes. Gating on "caution" too would
put a modal in front of 62 of 104 settings, and a dialog that always appears
stops being read.

**Verify against the machine, not from memory.** fwupd's behaviour here is full
of surprises that are not in any documentation — see "Known traps" below. If you
are unsure how an API behaves, write a five-line script and find out.

**Do not test writes on hardware you cannot afford to lose.** The test suite
never touches firmware. Keep it that way.

## Branches

| Branch | Purpose |
|---|---|
| `dev` | Default. All work lands here first. |
| `beta` | Pre-release soak. Merge `dev` → `beta` when a change set feels ready. |
| `main` | Released. **A push here builds an RPM and publishes a GitHub Release.** |

Open pull requests against `dev`. Only merge to `main` when you intend to cut a
release, and bump the version first (see below).

## Development setup

```
sudo dnf install python3-gobject gtk4 libadwaita fwupd
git clone https://github.com/ali-commits/thinkpad-settings
cd thinkpad-settings
python3 -m thinkpad_settings          # run from the source tree
```

To build and install the package locally:

```
sudo dnf install rpm-build rpmdevtools rpmlint python3-devel \
    pyproject-rpm-macros desktop-file-utils libappstream-glib
./build-rpm.sh
sudo dnf install ~/rpmbuild/RPMS/noarch/thinkpad-settings-*.noarch.rpm
```

If you also have the `./install.sh` copy in `~/.local`, remove it first with
`./uninstall.sh` — it shares a desktop ID with the package and shadows it.

## Tests

```
python3 tests/smoke.py
```

Runs the real `MainWindow` against `tests/fixture-t14gen3.json`, a captured
attribute dump. It needs a display but no Lenovo hardware, no fwupd and no
authentication, and it never writes anything. CI runs it under `xvfb-run`.

Add a check for anything you fix. The suite is a plain script, not pytest — a
`check(label, condition, detail)` call is all a test is.

## Extending the catalog

`thinkpad_settings/metadata.json` maps raw attribute names to labels,
descriptions, categories, risk levels and per-value wording. One object per
attribute:

```json
{
  "name": "VirtualizationTechnology",
  "category": "CPU & Virtualization",
  "label": "Intel VT-x (CPU virtualization)",
  "description": "What it does, and the practical effect on Linux.",
  "risk": "caution",
  "risk_note": "Concrete consequence, or \"\" when risk is safe.",
  "value_labels": [
    {"value": "Disable", "label": "Disabled"},
    {"value": "Enable",  "label": "Enabled"}
  ]
}
```

- `category` must be one of the ten in `metadata.CATEGORY_ORDER`.
- `risk` is `safe`, `caution` or `danger`. Use `danger` only for things that can
  lock the user out, destroy data access, or cannot be undone.
- `value_labels` must cover **every** value in the firmware's
  `BiosSettingPossibleValues`, in the same order.
- Descriptions must be **state-neutral**. Do not write "as it is now" or
  "currently disabled" — the reader's machine differs from yours, and the
  sentence can end up contradicting the value shown in the same row.
- If you are not certain what an attribute does, describe only what its name and
  permitted values make certain, and pick the more conservative risk. Never
  invent behaviour. This text is why someone will click Apply.

Adding support for a model you own? Attach
`sudo fwupdmgr get-bios-settings --json` to the issue or PR.

## Known traps

Hard-won, all verified on real hardware. Please don't undo these.

**fwupd hides values unless you ask for authentication.** `GetBiosSettings`
returns success with every `BiosSettingCurrentValue` key *absent* unless the
client first sends `SetFeatureFlags(ALLOW_AUTHENTICATION)` — flag `256` — on the
same connection. No prompt, no error. See `fwupd.FEATURE_ALLOW_AUTHENTICATION`.

**A cancelled polkit prompt is indistinguishable from a wrong password.** fwupd
never reads polkit's "dismissed" bit; both arrive as
`org.freedesktop.fwupd.AuthFailed`. Don't branch on the message text.

**`Gio.dbus_error_strip_remote_error()` returns `True` but does nothing** in
PyGObject. Strip the `GDBus.Error:` prefix by hand or users see raw D-Bus text.

**`Adw.ComboRow` fires `notify::selected` when its model is set,**
indistinguishable from a user click. Every programmatic population must happen
inside the `_building` guard, or merely opening a page stages changes.

**libadwaita parses row titles as Pango markup.** Every category name contains
`&`. Rows, toasts and banners support `set_use_markup(False)`;
`Adw.PreferencesGroup` and `Adw.StatusPage` do not, so their text must be
escaped.

**One bad entry aborts the whole `SetBiosSettings` batch.** Validate
client-side first so a single unwritable value can't silently kill every other
staged change.

## Releasing

1. Bump the version in `thinkpad-settings.spec` (`Version:` and `%changelog`),
   `pyproject.toml`, `thinkpad_settings/app.py` and the `<release>` block in
   `data/com.rabeei.ThinkPadSettings.metainfo.xml`. All four must agree.
2. Merge to `main`.
3. The release workflow builds the RPM and SRPM, creates the `v<version>` tag
   and publishes a GitHub Release with both attached plus SHA-256 sums.

If the tag already exists the workflow skips publishing, so re-pushing `main`
without a version bump is harmless.

## Style

Match what is already there: real sentences in comments, explaining *why* rather
than restating the code. Where a line exists because of a specific misbehaviour,
name it — those comments are the reason the traps above are not reintroduced.
