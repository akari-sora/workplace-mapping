# Hidden setup for the recording: a fresh copy of the invented example, a frozen test clock, a short prompt.
REPO="${REPO:-$(cd "$(dirname "$0")/../../.." && pwd)}"
setopt interactive_comments   # so the caption lines typed in the recording are comments
export WM_TEST_CLOCK=1 PYTHONDONTWRITEBYTECODE=1 GIT_CEILING_DIRECTORIES=/tmp
mkdir -p /tmp/wm-demo && rsync -a --delete "$REPO/examples/tidepool/" /tmp/wm-demo/tidepool/
cd /tmp/wm-demo/tidepool
python3 - <<'PY'
from pathlib import Path
p = Path('worklog/2026-03-12.md'); t = p.read_text()
p.write_text(t[:t.index('## Start here')].rstrip() + '\n')
PY
wm() { command "$REPO/bin/wm" --now 2026-03-12T18:30:00+00:00 "$@"; }
PROMPT='%F{114}tidepool%f $ '
PS1="$PROMPT"
clear
