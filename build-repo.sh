#!/usr/bin/env bash
# Assemble the dnf repository that gets published to GitHub Pages.
#
#   ./build-repo.sh <rpm-dir> <output-dir> [base-url]
#
# <rpm-dir>     directory containing signed .rpm files
# <output-dir>  site root to create (wiped and rebuilt)
# [base-url]    public URL of the site root; defaults to the Pages URL
#
# Signing is applied when a key is available: set GPG_KEY_ID, and either have
# the secret key already in GNUPGHOME or point GPG_PASSPHRASE_FILE at its
# passphrase. Without a key the repo is still built, but unsigned — dnf will
# then need gpgcheck=0, so CI treats a missing key as a failure.
set -euo pipefail

RPM_DIR="${1:?usage: build-repo.sh <rpm-dir> <output-dir> [base-url]}"
OUT="${2:?usage: build-repo.sh <rpm-dir> <output-dir> [base-url]}"
BASE_URL="${3:-https://ali-commits.github.io/thinkpad-settings}"

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBKEY="$SRC/data/RPM-GPG-KEY-thinkpad-settings"
KEY_NAME="RPM-GPG-KEY-thinkpad-settings"

command -v createrepo_c >/dev/null 2>&1 || {
    echo "createrepo_c is required (sudo dnf install createrepo_c)" >&2
    exit 1
}
[ -f "$PUBKEY" ] || { echo "Missing public key: $PUBKEY" >&2; exit 1; }

rpms=("$RPM_DIR"/*.noarch.rpm)
[ -e "${rpms[0]}" ] || { echo "No .noarch.rpm files in $RPM_DIR" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT/fedora"
cp "${rpms[@]}" "$OUT/fedora/"
cp "$PUBKEY" "$OUT/$KEY_NAME"

echo "Packages:"
for rpm in "$OUT"/fedora/*.rpm; do
    printf '  %s\n' "$(basename "$rpm")"
done

createrepo_c --quiet --update "$OUT/fedora"

# Detached signature over repomd.xml, which is what repo_gpgcheck=1 verifies.
# Without it dnf refuses the repo rather than warning, so a signing failure
# must be loud.
if [ -n "${GPG_KEY_ID:-}" ]; then
    gpg_args=(--batch --yes --armor --detach-sign --local-user "$GPG_KEY_ID")
    if [ -n "${GPG_PASSPHRASE_FILE:-}" ]; then
        gpg_args+=(--pinentry-mode loopback --passphrase-file "$GPG_PASSPHRASE_FILE")
    fi
    gpg "${gpg_args[@]}" --output "$OUT/fedora/repodata/repomd.xml.asc" \
        "$OUT/fedora/repodata/repomd.xml"
    echo "Signed repomd.xml"
    SIGNED=1
else
    echo "WARNING: GPG_KEY_ID unset — repository metadata is NOT signed" >&2
    SIGNED=0
fi

# The .repo file users drop into /etc/yum.repos.d/
cat > "$OUT/thinkpad-settings.repo" <<EOF
[thinkpad-settings]
name=ThinkPad BIOS Settings
baseurl=$BASE_URL/fedora/
enabled=1
gpgcheck=1
repo_gpgcheck=$SIGNED
gpgkey=$BASE_URL/$KEY_NAME
metadata_expire=6h
EOF

FINGERPRINT="$(gpg --show-keys --with-colons "$PUBKEY" 2>/dev/null \
    | awk -F: '/^fpr:/{print $10; exit}')"
mapfile -t sorted_rpms < <(printf '%s\n' "$OUT"/fedora/*.noarch.rpm | sort -V)
LATEST="$(basename "${sorted_rpms[-1]}")"
VERSION="$(sed -E 's/^thinkpad-settings-([0-9.]+)-.*/\1/' <<<"$LATEST")"

cat > "$OUT/index.html" <<EOF
<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ThinkPad BIOS Settings — package repository</title>
<style>
  :root { color-scheme: light dark; }
  body {
    font: 16px/1.6 system-ui, sans-serif;
    max-width: 46rem; margin: 0 auto; padding: 2.5rem 1.25rem;
  }
  h1 { font-size: 1.7rem; margin-bottom: .25rem; }
  .sub { opacity: .7; margin-top: 0; }
  pre {
    background: color-mix(in srgb, currentColor 8%, transparent);
    padding: .9rem 1rem; border-radius: .5rem; overflow-x: auto;
  }
  code { font-family: ui-monospace, monospace; font-size: .9em; }
  .fp { font-family: ui-monospace, monospace; font-size: .8rem; word-break: break-all; }
  footer { margin-top: 3rem; font-size: .9rem; opacity: .7; }
</style>

<h1>ThinkPad BIOS Settings</h1>
<p class="sub">dnf repository for Fedora — currently serving <strong>$VERSION</strong>.</p>

<h2>Install</h2>
<pre><code>sudo dnf config-manager addrepo --from-repofile=$BASE_URL/thinkpad-settings.repo
sudo dnf install thinkpad-settings</code></pre>

<p>Updates then arrive through <code>dnf upgrade</code> like any other package.</p>

<h2>Signing key</h2>
<p>
  Packages and repository metadata are signed. dnf imports the key from the
  repo definition on first install; to inspect it first, fetch
  <a href="$BASE_URL/$KEY_NAME">$KEY_NAME</a>.
</p>
<p class="fp">$FINGERPRINT</p>

<h2>Without the repository</h2>
<p>
  Individual packages are attached to each
  <a href="https://github.com/ali-commits/thinkpad-settings/releases">GitHub release</a>.
</p>

<footer>
  <a href="https://github.com/ali-commits/thinkpad-settings">Source on GitHub</a> · MIT
</footer>
EOF

# Jekyll would otherwise skip directories it considers special.
touch "$OUT/.nojekyll"

echo
echo "Site built at $OUT"
echo "  base URL: $BASE_URL"
echo "  signed  : $([ "$SIGNED" = 1 ] && echo yes || echo NO)"
