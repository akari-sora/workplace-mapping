#!/bin/zsh
# Regenerate the HTML sources and render every PNG (2x) into docs/media/.
set -e
SRC="${0:A:h}"; OUT="${SRC:h}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
python3 "$SRC/build.py"
render() { # name width height
  for suf in "" ".zh-TW"; do
    "$CHROME" --headless=new --disable-gpu --hide-scrollbars --default-background-color=00000000 \
      --force-device-scale-factor=2 --window-size=$2,$3 --screenshot="$OUT/$1$suf.png" "file://$SRC/$1$suf.html" >/dev/null 2>&1 || true   # headless Chrome may exit non-zero on teardown
    [ -s "$OUT/$1$suf.png" ] || { echo "failed: $1$suf" >&2; exit 1; }
  done
}
render hero 1600 640
render loop 1600 900
render savings 1600 760
ls -l "$OUT"/*.png
