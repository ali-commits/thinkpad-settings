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

## Linting, formatting and types

The project is checked with **ruff** (lint + format), **mypy** in `strict` mode
and **pyrefly**, all three clean with zero suppressions in the application code.

Tooling is managed with [uv](https://docs.astral.sh/uv/) and pinned in
`uv.lock`, so everyone — and CI — runs identical versions.

```
uv venv --system-site-packages
uv sync
uv run pre-commit install
```

`--system-site-packages` matters: the test suite imports the distro's PyGObject,
which is not practically pip-installable. `uv sync` preserves the flag on an
existing venv.

```
uv run ruff check thinkpad_settings tests
uv run ruff format thinkpad_settings tests
uv run mypy thinkpad_settings tests
uv run pyrefly check
```

Two uv-specific notes:

`pygobject-stubs` declares a runtime dependency on PyGObject, which drags in
`pycairo` and tries to compile it from source. `[tool.uv] override-dependencies`
drops it — the stubs are type-only and the real PyGObject comes from the distro.
Without that override `uv sync` fails with a meson `Dependency "cairo" not
found` error.

`PYGOBJECT_STUB_CONFIG=Gtk4,Gdk4` is set in CI as documentation of intent, but
it is **not** required: pygobject-stubs installs "the most recent version of
each library" when unset, which is already Gtk4. Set it if a future release
changes that default.

The type checkers run through `uv run` in pre-commit as `system` hooks so they
use the locked versions; letting pre-commit resolve its own mypy would drift
from CI.

`pre-commit run --all-files` runs all of the above plus shellcheck, the catalog
integrity check and the version-consistency check. CI runs the same commands.

Strict typing on a PyGObject app is only meaningful because of the stubs, and it
earns its keep: it was mypy that caught the code attaching Python attributes
(`row.category`, `row.staged_badge`) directly to GObject widgets. That works at
runtime but is invisible to the checker and breaks silently when a widget is
recreated — the fix was a `CategoryRow` subclass and a badge dictionary keyed by
setting name.

Where a `# type: ignore` is unavoidable it carries the specific error code and a
reason. There are three, all in the test suite, all because substituting a fake
D-Bus client is the point of the test.

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

## Versioning

**`VERSION` at the repository root is the only place the version is written.**

Everything else is derived by `build-rpm.sh` at build time, into staged copies —
never into the working tree, so nothing can drift and there is nothing to keep
in sync:

| | |
|---|---|
| `VERSION` | edit this. That is the whole procedure. |
| `pyproject.toml` | `dynamic = ["version"]`, read from `VERSION` |
| `thinkpad_settings.__version__` | reads `VERSION`, falling back to installed metadata |
| spec `Version:` | rewritten at build time |
| spec `%changelog` | an entry is synthesised if none exists for this version |
| metainfo `<release>` | an entry is added if none exists for this version |

The `Version:` in the committed spec is a deliberate `0.0.0` placeholder.
rpmbuild parses the spec *before* it unpacks `Source0`, so the spec cannot read
`VERSION` itself — `Version: %(cat VERSION)` resolves to nothing. `build-rpm.sh`
rewrites the copy it hands to rpmbuild.

To cut a release:

```
echo 0.2.0 > VERSION
git commit -am "Release 0.2.0"
```

If you want a real changelog entry rather than the synthesised "Release X.Y.Z",
add one to `%changelog` in the spec yourself; `build-rpm.sh` leaves it alone
when the version already appears there. Same for the metainfo `<release>` block
if you want release notes in GNOME Software.

### Which number to bump

While the project is `0.x`, treat the **minor** as the breaking-change signal:

- `0.1.0 → 0.1.1` — bug fixes, catalog wording, packaging that changes behaviour
- `0.1.0 → 0.2.0` — new features, or anything that changes how existing settings
  behave or are presented
- `0.x → 1.0.0` — when the write path has been exercised on real hardware by
  more than one person and the catalog is trusted

### `Version` vs `Release`

`Version` is the software, `Release` (the `-1` in `0.1.0-1.fc44`) is the
packaging of it. For a spec-only fix, bump `Release:` in the spec directly. Note
the release workflow keys on `v<VERSION>`, so a `Release`-only bump publishes no
new GitHub Release — the tag already exists.

## Releasing

1. `echo X.Y.Z > VERSION` on `dev`, commit, push.
2. PR `dev` → `beta`, let it soak.
3. PR `beta` → `main`. `main` is protected: the PR cannot merge until
   **Build and test on Fedora** passes, and direct pushes are rejected.
4. Merging publishes automatically — the workflow builds the RPM and SRPM,
   creates the `v<version>` tag, and attaches both plus `SHA256SUMS`.

Never create the `v*` tag by hand; the workflow owns it. If the tag already
exists the workflow skips publishing, so re-merging to `main` without a version
bump is a harmless no-op rather than a failed run.

Publishing a release also triggers the **Publish dnf repository** workflow,
which collects the RPMs from *every* release, rebuilds the repository metadata
and deploys it to GitHub Pages. Older versions stay installable.

### Signing

Packages and repository metadata are signed with a dedicated key
(`8B6BA518D0BDF62B4DAD665565BED405FED3678F`). Three repository secrets drive it:

| Secret | |
|---|---|
| `GPG_PRIVATE_KEY` | ASCII-armoured private key |
| `GPG_PASSPHRASE` | its passphrase |
| `GPG_KEY_ID` | fingerprint passed to `rpmsign` |

Both workflows **fail rather than publish unsigned artifacts** if the key is
missing — an unsigned package that looks signed is worse than a failed release.
The public half is committed at `data/RPM-GPG-KEY-thinkpad-settings` and served
from the Pages site; if it is ever rotated, both must change together or every
existing user's `dnf` will reject the repository.

To test the repository locally without publishing:

```
sudo dnf install createrepo_c rpm-sign
export GPG_KEY_ID=... GPG_PASSPHRASE_FILE=...
./build-repo.sh ~/rpmbuild/RPMS/noarch /tmp/site "file:///tmp/site"
```

then point a `.repo` file at `file:///tmp/site/fedora/`.

## Style

Match what is already there: real sentences in comments, explaining *why* rather
than restating the code. Where a line exists because of a specific misbehaviour,
name it — those comments are the reason the traps above are not reintroduced.
