#!/usr/bin/env bash
# Build the RPM from a working tree. Produces an SRPM and a noarch RPM under
# ~/rpmbuild/, then lints them.
set -euo pipefail

NAME=thinkpad-settings
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(sed -n 's/^Version:[[:space:]]*//p' "$SRC/$NAME.spec")"

if [ -z "$VERSION" ]; then
    echo "Could not read Version from $NAME.spec" >&2
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
tar cf - --exclude=.git --exclude=__pycache__ --exclude='*.pyc' -C "$SRC" . \
    | tar xf - -C "$STAGE/$NAME-$VERSION"

tar czf "$HOME/rpmbuild/SOURCES/$NAME-$VERSION.tar.gz" -C "$STAGE" "$NAME-$VERSION"
cp "$SRC/$NAME.spec" "$HOME/rpmbuild/SPECS/"

rpmbuild -ba "$HOME/rpmbuild/SPECS/$NAME.spec"

RPM="$HOME/rpmbuild/RPMS/noarch/$NAME-$VERSION-1$(rpm --eval '%{?dist}').noarch.rpm"

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
