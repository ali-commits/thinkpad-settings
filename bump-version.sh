#!/usr/bin/env bash
# Set the version in every place that holds one, in a single step.
#
# The version lives in four files and the release workflow refuses to publish
# if they disagree, so editing them by hand is a reliable way to waste a CI run.
#
#   ./bump-version.sh 0.2.0
#   ./bump-version.sh 0.2.0 --release 2   # packaging-only rebuild, same version
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

CURRENT="$(sed -n 's/^Version:[[:space:]]*//p' thinkpad-settings.spec)"
echo "  $CURRENT  ->  $VERSION-$RELEASE"

# RPM changelog dates must be C-locale English, whatever the user's locale is.
STAMP="$(LC_ALL=C date '+%a %b %d %Y')"
AUTHOR="$(git config user.name || echo Ali) <$(git config user.email || echo ali@rabeei.com)>"

# 1. spec: Version, Release, and a new changelog entry at the top of the list
sed -i "s/^Version:.*/Version:        $VERSION/" thinkpad-settings.spec
sed -i "s/^Release:.*/Release:        $RELEASE%{?dist}/" thinkpad-settings.spec
python3 - "$VERSION" "$RELEASE" "$STAMP" "$AUTHOR" "$MESSAGE" <<'PY'
import sys, pathlib
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

# 2. pyproject.toml
sed -i "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml

# 3. the About dialog
sed -i "s/^VERSION = \".*\"/VERSION = \"$VERSION\"/" thinkpad_settings/app.py

# 4. AppStream release list — GNOME Software shows this
python3 - "$VERSION" <<'PY'
import datetime, pathlib, re, sys
version = sys.argv[1]
path = pathlib.Path("data/com.rabeei.ThinkPadSettings.metainfo.xml")
text = path.read_text()
today = datetime.date.today().isoformat()
if re.search(rf'<release version="{re.escape(version)}"', text):
    text = re.sub(rf'(<release version="{re.escape(version)}" date=")[^"]*(")',
                  rf'\g<1>{today}\g<2>', text)
else:
    entry = (f'    <release version="{version}" date="{today}">\n'
             f'      <description>\n        <p>Release {version}.</p>\n'
             f'      </description>\n    </release>\n')
    text = text.replace("  <releases>\n", "  <releases>\n" + entry, 1)
path.write_text(text)
PY

# Same gate the release workflow applies, run now rather than in CI.
fail=0
grep -q "^Version:        $VERSION\$" thinkpad-settings.spec || { echo "  spec mismatch" >&2; fail=1; }
grep -q "^version = \"$VERSION\"\$" pyproject.toml || { echo "  pyproject.toml mismatch" >&2; fail=1; }
grep -q "^VERSION = \"$VERSION\"\$" thinkpad_settings/app.py || { echo "  app.py mismatch" >&2; fail=1; }
grep -q "release version=\"$VERSION\"" data/com.rabeei.ThinkPadSettings.metainfo.xml \
    || { echo "  metainfo.xml mismatch" >&2; fail=1; }
[ "$fail" -eq 0 ] || { echo "Version files disagree — not committing." >&2; exit 1; }

command -v appstream-util >/dev/null 2>&1 && \
    appstream-util validate-relax --nonet data/com.rabeei.ThinkPadSettings.metainfo.xml >/dev/null

echo "  all four files agree"
echo
git --no-pager diff --stat
cat <<EOF

Next:
  git commit -am "Release $VERSION"
  git push origin dev
  # then open PRs dev -> beta -> main; merging to main publishes v$VERSION
EOF
