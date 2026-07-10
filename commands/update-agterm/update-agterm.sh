#!/bin/bash
# update-agterm.sh — check for a new agterm release, show the changelog, then ask before upgrading.
# Meant to run inside an agterm overlay (gives a visible surface + a TTY for the prompts).
# Flow: compare installed vs latest → if behind, render release notes for every newer release
# (via glow) → ask y/N → only then `brew update && brew upgrade --cask agterm`.
set -uo pipefail

REPO="umputun/agterm"
PLIST="/Applications/agterm.app/Contents/Info.plist"

pause() { echo; echo "— press Enter to close —"; read -r; }

installed="$(defaults read "$PLIST" CFBundleShortVersionString 2>/dev/null || echo '?')"

echo "==> Checking $REPO for updates…"
releases="$(curl -fsS "https://api.github.com/repos/$REPO/releases?per_page=30" 2>/dev/null || true)"
if [ -z "$releases" ] || ! echo "$releases" | jq -e '.[0].tag_name' >/dev/null 2>&1; then
  echo "⚠️  Couldn't reach GitHub (no network, or rate-limited). Try again later."
  pause; exit 1
fi

latest="$(echo "$releases" | jq -r '.[0].tag_name' | sed 's/^v//')"
echo "agterm installed: $installed    latest on GitHub: ${latest:-unknown}"

# Already current (string match, or installed sorts >= latest) → nothing to do.
if [ "$installed" != '?' ] && [ -n "$latest" ]; then
  newest="$(printf '%s\n%s\n' "$installed" "$latest" | sort -V | tail -1)"
  if [ "$installed" = "$latest" ] || [ "$newest" = "$installed" ]; then
    echo "✅ Already on the latest release ($installed). Nothing to do."
    pause; exit 0
  fi
fi

# Build a changelog covering every release newer than the installed version (newest first).
changelog=""
behind=0
while IFS=$'\t' read -r tag name date; do
  ver="${tag#v}"
  if [ "$installed" != '?' ]; then
    newest="$(printf '%s\n%s\n' "$installed" "$ver" | sort -V | tail -1)"
    { [ "$ver" = "$installed" ] || [ "$newest" != "$ver" ]; } && continue
  fi
  body="$(echo "$releases" | jq -r --arg t "$tag" '.[] | select(.tag_name==$t) | .body // ""')"
  changelog+="## ${name:-$tag} · ${date%%T*}"$'\n\n'"${body}"$'\n\n---\n\n'
  behind=$((behind + 1))
done < <(echo "$releases" | jq -r '.[] | [.tag_name, (.name // ""), (.published_at // "")] | @tsv')

echo
echo "⬆  ${behind} newer release(s) available: $installed → $latest"
echo
if command -v glow >/dev/null 2>&1; then
  printf '%s' "$changelog" | glow -
else
  printf '%s' "$changelog"
fi
echo

# Ask before doing anything.
printf 'Update to %s now? [y/N] ' "$latest"
read -r ans
case "$ans" in
  y|Y|yes|YES)
    echo
    echo "==> brew update (refresh Homebrew + umputun/apps tap)…"
    brew update
    echo
    echo "==> brew upgrade --cask agterm…"
    brew upgrade --cask agterm
    status=$?
    echo
    if [ "$status" -eq 0 ]; then
      echo "✅ Done. Quit and relaunch agterm to run the new version."
    else
      echo "⚠️  brew upgrade exited $status."
      echo "    If it says agterm is running, quit the app and re-run (or add --force)."
    fi
    ;;
  *)
    echo "Cancelled — no changes made."
    ;;
esac
pause
