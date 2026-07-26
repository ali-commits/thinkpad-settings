#!/usr/bin/env bash
# Build the RPM from a working tree. Produces an SRPM and a noarch RPM under
# ~/rpmbuild/, then lints them.
#
# VERSION is the only place the version is maintained. Everything else is
# derived here, at build time, into the staged copies:
#
#   * the spec's Version: — rpmbuild parses the spec before it unpacks Source0,
#     so the spec cannot read VERSION itself; this rewrites the copy it builds
#   * a %changelog entry, if the spec has none for this version
#   * the AppStream <release> entry GNOME Software shows
#
# Nothing derived is ever committed, so nothing can drift and there is nothing
# to keep in sync.
set -euo pipefail

NAME=thinkpad-settings
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$SRC/VERSION")"
RELEASE="$(sed -n 's/^Release:[[:space:]]*\([0-9]*\).*/\1/p' "$SRC/$NAME.spec")"
RELEASE="${RELEASE:-1}"

if [ -z "$VERSION" ]; then
    echo "VERSION is empty" >&2
    exit 1
fi
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "VERSION must look like X.Y.Z (got '$VERSION')" >&2
    exit 1
fi

for tool in rpmbuild rpmdev-setuptree; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "Missing build tooling. Install it with:" >&2
        echo "    sudo dnf install rpm-build rpmdevtools rpmlint python3-devel \\" >&2
        echo "        pyproject-rpm-macros desktop-file-utils libappstream-glib" >&2
        exit 1
    }
done

rpmdev-setuptree

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/$NAME-$VERSION"

# Ship the working tree rather than git HEAD, so an uncommitted fix can still
# be built and tested.
tar cf - --exclude=.git --exclude=.venv --exclude=__pycache__ \
    --exclude='*.pyc' --exclude='*.egg-info' -C "$SRC" . \
    | tar xf - -C "$STAGE/$NAME-$VERSION"

STAMP="$(LC_ALL=C date '+%a %b %d %Y')"
AUTHOR="$(git -C "$SRC" config user.name 2>/dev/null || echo Ali)"
EMAIL="$(git -C "$SRC" config user.email 2>/dev/null || echo ali@rabeei.com)"

# The AppStream release list travels inside the tarball, so patch the staged
# copy rather than the working tree.
python3 - "$STAGE/$NAME-$VERSION/data/com.rabeei.ThinkPadSettings.metainfo.xml" "$VERSION" <<'PY'
import datetime
import pathlib
import re
import sys

path, version = pathlib.Path(sys.argv[1]), sys.argv[2]
text = path.read_text()
if not re.search(rf'<release version="{re.escape(version)}"', text):
    entry = (
        f'    <release version="{version}" '
        f'date="{datetime.date.today().isoformat()}">\n'
        f"      <description>\n        <p>Release {version}.</p>\n"
        f"      </description>\n    </release>\n"
    )
    text = text.replace("  <releases>\n", "  <releases>\n" + entry, 1)
    path.write_text(text)
PY

tar czf "$HOME/rpmbuild/SOURCES/$NAME-$VERSION.tar.gz" -C "$STAGE" "$NAME-$VERSION"

# rpmbuild builds the spec in SPECS, not the one inside the tarball, so this is
# the copy that needs the derived version.
SPEC="$HOME/rpmbuild/SPECS/$NAME.spec"
sed -e "s/^Version:.*/Version:        $VERSION/" \
    -e "s/^Release:.*/Release:        $RELEASE%{?dist}/" \
    "$SRC/$NAME.spec" > "$SPEC"

if ! grep -q -- "- $VERSION-$RELEASE\$" "$SPEC"; then
    python3 - "$SPEC" "$VERSION" "$RELEASE" "$STAMP" "$AUTHOR" "$EMAIL" <<'PY'
import pathlib
import sys

spec, version, release, stamp, author, email = sys.argv[1:7]
path = pathlib.Path(spec)
text = path.read_text()
entry = f"* {stamp} {author} <{email}> - {version}-{release}\n- Release {version}\n"
head, sep, tail = text.partition("%changelog\n")
if not sep:
    raise SystemExit("no %changelog section in the spec")
path.write_text(head + sep + entry + ("\n" if tail.strip() else "") + tail.lstrip("\n"))
PY
fi

echo "Building $NAME-$VERSION-$RELEASE (version from VERSION)"
rpmbuild -ba "$SPEC"

RPM="$HOME/rpmbuild/RPMS/noarch/$NAME-$VERSION-$RELEASE$(rpm --eval '%{?dist}').noarch.rpm"

if command -v rpmlint >/dev/null 2>&1; then
    echo
    echo "== rpmlint =="
    # Known, harmless: 'fwupd'/'lmi' are not in the dictionary, and no upstream
    # URL is set until this is published somewhere.
    rpmlint "$RPM" || true
fi

echo
echo "Built: $RPM"
echo "Install with:  sudo dnf install $RPM"
