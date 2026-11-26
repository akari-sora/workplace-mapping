"""Poll a shell probe until it reports completion or reaches its deadline."""

from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import time
from pathlib import Path

from .actions import logged_payloads
from .config import Config
from .files import write_text
from .secrets import redact_output


def emit(config: Config, name: str, event: str, detail: str) -> None:
    cleaned = redact_output(config, detail, private=logged_payloads(config))
    message = " ".join(cleaned.splitlines())
    print(f"{config.now().isoformat(timespec='seconds')} {name} {event} {message}", flush=True)


def probe(config: Config, command: str, remaining: float) -> tuple[str, str]:
    try:
        with subprocess.Popen(command, cwd=config.root, shell=True, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=os.name == "posix",
                              text=True, encoding="utf-8", errors="replace") as process:
            try:
                stdout, _ = process.communicate(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                return "", "probe timed out"
    except OSError:
        return "", "probe could not run"
    if process.returncode:
        return "", f"exit {process.returncode}"
    output = stdout.strip()
    return (output, "") if output else ("", "empty stdout")


def watch(config: Config, name: str, command: str, every: float, timeout: float,
          done: re.Pattern[str] | None, warn_after: int, state: Path | None) -> int:
    last_digest = ""
    last_displayed = ""
    if state is not None:
        try:
            last_digest = state.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            pass
    successful = False
    failures = 0
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        output, failure = probe(config, command, remaining)
        if output:
            successful, failures = True, 0
            displayed = output[:400]
            current_digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
            changed = current_digest != last_digest
            if changed:
                if state is not None:
                    write_text(state, current_digest + "\n", permissions=0o600)
                last_digest = current_digest
            last_displayed = displayed
            if done is not None and done.search(output):
                emit(config, name, "DONE", displayed)
                return 0
            if changed:
                emit(config, name, "EVENT", displayed)
        else:
            failures += 1
            if failures == warn_after:
                emit(config, name, "WARN", f"probe failed {failures} times in a row ({failure})")
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(every, remaining))

    emit(config, name, "TIMEOUT", f"last={last_displayed or last_digest}")
    return 3 if successful else 4
