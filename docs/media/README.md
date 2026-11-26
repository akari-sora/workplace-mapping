The README images (English and zh-TW) are rendered from the HTML sources in `src/`, which `src/build.py` generates.
Run `docs/media/src/render.sh` on macOS with Google Chrome and python3 to rebuild every PNG (1600 px wide at 2x).
Edit the copy and numbers in `src/build.py`, not the generated `.html` files.

The terminal demo is recorded with VHS from the repository root: `sed "s|REPO_PATH|$PWD|" docs/media/src/demo.tape > /tmp/demo.tape && vhs /tmp/demo.tape`.
It runs against a temporary copy of the invented example with a frozen test clock. `overview.mp4` is the hero image, the demo, and the two diagrams joined with ffmpeg.
