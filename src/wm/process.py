"""Run shell commands in a separate process group."""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Execution:
    exit: int | None
    stdout: bytes = b""
    stderr: bytes = b""


def kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def shell(root: Path, command: str, timeout: float, *, payload: bytes | None = None,
          env: dict[str, str] | None = None, capture: bool = False, quiet: bool = False) -> Execution:
    stdin = subprocess.PIPE if payload is not None else subprocess.DEVNULL if quiet else None
    outputs = subprocess.PIPE if capture else subprocess.DEVNULL if quiet else None
    with subprocess.Popen(command, shell=True, cwd=root, env=env, stdin=stdin,
                          stdout=outputs, stderr=outputs, start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(input=payload, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            kill_group(process)
            return Execution(None, exc.output or b"", exc.stderr or b"")
        except BaseException:
            kill_group(process)
            raise
    return Execution(process.returncode, stdout or b"", stderr or b"")
