#!/usr/bin/env bash
# agterm-migrate.sh — helper for the `agterm-check-update` skill.
#
# Tracks which agterm version the user's custom configs were last reviewed
# against, so a post-update review only has to look at the DELTA changelog.
# State lives in ~/.config/agterm-projects/agterm-migration as one line:
#     <version>\t<YYYY-MM-DD>
#
# Subcommands:
#   status              installed vs last-validated; whether a review is needed
#   changelog [FROM]    print agterm release notes newer than FROM (default: last
#                       validated) up to and including the installed version
#   record [VERSION]    mark VERSION (default: installed) as validated -> state file
set -uo pipefail

REPO="umputun/agterm"
PLIST="/Applications/agterm.app/Contents/Info.plist"
MIGRATION="$HOME/.config/agterm-projects/agterm-migration"

installed_version() { defaults read "$PLIST" CFBundleShortVersionString 2>/dev/null || echo '?'; }
last_validated()    { [ -f "$MIGRATION" ] && awk 'NF{print $1; exit}' "$MIGRATION" || true; }

# strictly-greater-than on semver-ish version strings ("0.13.0" > "0.12.1")
ver_gt() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]; }

cmd="${1:-status}"
installed="$(installed_version)"
from="$(last_validated)"

case "$cmd" in
  status)
    echo "installed:      $installed"
    echo "last validated: ${from:-<none — first run>}"
    if [ "$installed" = "$from" ]; then
      echo "=> up to date; no migration review needed."
    else
      echo "=> review needed: ${from:-<baseline unknown>} -> $installed"
    fi
    ;;

  changelog)
    from="${2:-$from}"   # allow explicit FROM (e.g. first run: the version upgraded from)
    in_range() {
      local t="$1"
      [ -n "$from" ] && ! ver_gt "$t" "$from" && return 1
      [ "$installed" != '?' ] && ver_gt "$t" "$installed" && return 1
      return 0
    }
    tags="$(gh release list --repo "$REPO" --limit 60 --json tagName -q '.[].tagName' 2>/dev/null)" \
      || { echo "gh unavailable — 'gh auth login' or fetch https://github.com/$REPO/releases manually"; exit 1; }
    printed=0
    # oldest -> newest so the changelog reads forward in time
    for tag in $(printf '%s\n' $tags | sed 's/^v//' | sort -V); do
      in_range "$tag" || continue
      body="$(gh release view "v$tag" --repo "$REPO" --json body -q .body 2>/dev/null)"
      printf '########## v%s ##########\n%s\n\n' "$tag" "$body"
      printed=1
    done
    [ "$printed" = 1 ] || echo "(no releases between '${from:-<none>}' and '$installed')"
    ;;

  record)
    v="${2:-$installed}"
    [ "$v" != '?' ] || { echo "refusing to record unknown version"; exit 1; }
    printf '%s\t%s\n' "$v" "$(date +%F)" > "$MIGRATION"
    echo "recorded validated version: $v ($(date +%F)) -> $MIGRATION"
    ;;

  *)
    echo "usage: agterm-migrate.sh [status | changelog [FROM] | record [VERSION]]" >&2
    exit 2
    ;;
esac
