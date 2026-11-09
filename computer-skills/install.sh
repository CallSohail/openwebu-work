#!/usr/bin/env sh
# Install the computer-skills pack for Open Terminal / Tether on macOS or Linux.
#   ./install.sh                     copy skills to ~/.agents/skills
#   ./install.sh --with-agents-md    also copy AGENTS.md to ~ (skipped if one exists)
#   ./install.sh --uninstall         remove only the skills this pack installed
# Existing skills with the same name are backed up first. Nothing outside ~/.agents is touched.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
src="$here/skills"
dest="$HOME/.agents/skills"
mode=install; agents=no
for a in "$@"; do
  case "$a" in
    --uninstall) mode=uninstall ;;
    --with-agents-md) agents=yes ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
if [ "$mode" = uninstall ]; then
  for d in "$src"/*/; do n=$(basename "$d"); [ -d "$dest/$n" ] && rm -rf "${dest:?}/$n" && echo "removed  $n"; done
  exit 0
fi
mkdir -p "$dest"
backup="$HOME/.agents/skills-backup-$(date +%Y%m%d-%H%M%S)"
for d in "$src"/*/; do
  n=$(basename "$d")
  if [ -e "$dest/$n" ]; then mkdir -p "$backup"; mv "$dest/$n" "$backup/$n"; echo "backup   $n -> $backup"; fi
  cp -R "$d" "$dest/$n"
  find "$dest/$n" -name __pycache__ -type d -prune -exec rm -rf {} +
  echo "installed $n"
done
if [ "$agents" = yes ]; then
  if [ -e "$HOME/AGENTS.md" ]; then echo "AGENTS.md already exists, left unchanged"
  else cp "$here/AGENTS.md" "$HOME/AGENTS.md"; echo "installed AGENTS.md (edit the [bracketed] lines)"; fi
fi
echo "Done. In Open WebUI, select this terminal and type \$ to see the skills."
