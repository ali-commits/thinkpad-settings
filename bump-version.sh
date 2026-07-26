#!/usr/bin/env bash
# Set the release version.
#
#   ./bump-version.sh 0.2.0
#   ./bump-version.sh 0.2.0 --message "Add support for X"
#   ./bump-version.sh 0.1.0 --release 2   # packaging-only rebuild
#
# VERSION is the single source of truth. pyproject.toml and the About dialog
# derive from it and are never edited here — they cannot drift.
#
# Two things still have to be written:
#   * the spec's Version:, because rpmbuild parses the spec before it unpacks
#     Source0 and so cannot read VERSION out of the tarball
#   * the two changelogs (%changelog and the AppStream <releases> list), which
#     accumulate entries rather than holding a single value
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SRC"

usage() {
    cat >&2 <<'EOF'
Usage: ./bump-version.sh <version> [--release N] [--message "changelog line"]

  <version>     e.g. 0.2.0 — the upstream version
  --release N   RPM Release field (default 1). Bump this instead of the
                version when only packaging changed and the software did not.
  --message     changelog entry text (default: "Release <version>")
EOF
    exit 1
}

[ $# -ge 1 ] || usage
VERSION="$1"; shift
RELEASE=1
MESSAGE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --release) RELEASE="${2:?--release needs a number}"; shift 2 ;;
        --message) MESSAGE="${2:?--message needs text}"; shift 2 ;;
        *) usage ;;
    esac
done

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Version must look like X.Y.Z (got '$VERSION')" >&2
    exit 1
fi
if ! [[ "$RELEASE" =~ ^[0-9]+$ ]]; then
    echo "Release must be a number (got '$RELEASE')" >&2
    exit 1
fi
[ -n "$MESSAGE" ] || MESSAGE="Release $VERSION"

CURRENT="$(cat VERSION)"
echo "  $CURRENT  ->  $VERSION-$RELEASE"

# 1. the source of truth
echo "$VERSION" > VERSION

# 2. the spec, plus a new changelog entry at the top of the list
STAMP="$(LC_ALL=C date '+%a %b %d %Y')"
AUTHOR="$(git config user.name || echo Ali) <$(git config user.email || echo ali@rabeei.com)>"
sed -i "s/^Version:.*/Version:        $VERSION/" thinkpad-settings.spec
sed -i "s/^Release:.*/Release:        $RELEASE%{?dist}/" thinkpad-settings.spec
python3 - "$VERSION" "$RELEASE" "$STAMP" "$AUTHOR" "$MESSAGE" <<'PY'
import pathlib
import sys

version, release, stamp, author, message = sys.argv[1:6]
path = pathlib.Path("thinkpad-settings.spec")
text = path.read_text()
entry = f"* {stamp} {author} - {version}-{release}\n- {message}\n"
marker = "%changelog\n"
head, sep, tail = text.partition(marker)
if not sep:
    raise SystemExit("no %changelog section in the spec")
path.write_text(head + sep + entry + ("\n" if tail.strip() else "") + tail.lstrip("\n"))
PY

# 3. the AppStream release list, which GNOME Software shows
python3 - "$VERSION" "$MESSAGE" <<'PY'
import datetime
import pathlib
import re
import sys

version, message = sys.argv[1:3]
path = pathlib.Path("data/com.rabeei.ThinkPadSettings.metainfo.xml")
text = path.read_text()
today = datetime.date.today().isoformat()
if re.search(rf'<release version="{re.escape(version)}"', text):
    text = re.sub(
        rf'(<release version="{re.escape(version)}" date=")[^"]*(")',
        rf"\g<1>{today}\g<2>",
        text,
    )
else:
    entry = (
        f'    <release version="{version}" date="{today}">\n'
        f"      <description>\n        <p>{message}</p>\n"
        f"      </description>\n    </release>\n"
    )
    text = text.replace("  <releases>\n", "  <releases>\n" + entry, 1)
path.write_text(text)
PY

# The only pair that can disagree, so the only one worth checking.
spec_version="$(sed -n 's/^Version:[[:space:]]*//p' thinkpad-settings.spec)"
if [ "$spec_version" != "$VERSION" ]; then
    echo "  spec says '$spec_version', VERSION says '$VERSION'" >&2
    exit 1
fi

command -v appstream-util >/dev/null 2>&1 &&
    appstream-util validate-relax --nonet data/com.rabeei.ThinkPadSettings.metainfo.xml >/dev/null

echo "  VERSION and the spec agree; pyproject and the About dialog derive"
echo
git --no-pager diff --stat
cat <<EOF

Next:
  git commit -am "Release $VERSION"
  git push origin dev
  # then open PRs dev -> beta -> main; merging to main publishes v$VERSION
EOF
