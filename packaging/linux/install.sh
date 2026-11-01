#!/bin/sh
# Installs Shardloom for this user: the program in ~/.local/opt/Shardloom, the
# `shardloom` command in ~/.local/bin and an entry in the applications menu.
# Run it from the unpacked folder: ./install.sh   (./install.sh --remove to uninstall)
set -e
here=$(cd "$(dirname "$0")" && pwd)
opt="$HOME/.local/opt/Shardloom"
bin="$HOME/.local/bin"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

rm -rf "$opt"
rm -f "$bin/shardloom" "$apps/shardloom.desktop"
if [ "$1" = "--remove" ]; then
    echo "Shardloom removed."
    exit 0
fi

mkdir -p "$opt" "$bin" "$apps"
cp -a "$here/." "$opt/"
ln -s "$opt/Shardloom" "$bin/shardloom"
sed "s|@EXEC@|$opt/Shardloom|" "$here/shardloom.desktop" > "$apps/shardloom.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$apps" 2>/dev/null || true

echo "Shardloom installed in $opt"
echo "Start it from the applications menu, or type: shardloom"
case ":$PATH:" in *":$bin:"*) ;; *) echo "(add $bin to your PATH to use the shardloom command)";; esac
