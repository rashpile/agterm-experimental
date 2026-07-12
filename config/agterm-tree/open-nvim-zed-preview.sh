#!/bin/bash
# open-nvim-zed-preview.sh — quick-look ONE file in a 90% agterm overlay running nvim (nvim-zed
# config, so keys match the editor). agterm overlays are ephemeral, so each call respawns the
# overlay = "new file overrides old". Called by the nvim-zed tree pane's open action.
#   open-nvim-zed-preview.sh <file>
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"
AGT=/opt/homebrew/bin/agtermctl

file="${1:?usage: open-nvim-zed-preview.sh <file>}"
sid="${AGTERM_SESSION_ID:-active}"

# Drop any overlay still open (idempotent), then open the file at 90% and pull focus into it.
# `-n` = no swapfile: this is a throwaway quick-look, and it stops the previous preview's swap
# (its nvim may still be exiting) from triggering a W325 "swapfile from Nvim process ..." prompt.
"$AGT" session overlay close --target "$sid" >/dev/null 2>&1 || true
# Images → kitty-graphics viewer (img-view.sh, arrow-key gallery); everything else → nvim (`-n`).
case "${file##*.}" in
  png|PNG|jpg|JPG|jpeg|JPEG|gif|GIF|webp|WEBP|bmp|BMP|tif|TIF|tiff|TIFF|heic|HEIC|heif|HEIF)
    inner="exec bash $HOME/.config/agterm-projects/img-view.sh $(printf %q "$file")" ;;
  *)
    inner="exec env NVIM_APPNAME=nvim-zed NVIM_ZED_ROLE=preview nvim -n $(printf %q "$file")" ;;
esac
"$AGT" session overlay open "zsh -lc $(printf %q "$inner")" \
  --size-percent 90 --target "$sid" --follow >/dev/null 2>&1
