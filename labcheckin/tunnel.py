from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from labcheckin.config import DATA_DIR, PUBLIC_URL_FILE

log = logging.getLogger("labcheckin.tunnel")

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
CREATE_NO_WINDOW = 0x08000000


def cloudflared_executable() -> Path:
    found = shutil.which("cloudflared")
    if found:
        return Path(found)
    local = DATA_DIR / "cloudflared.exe"
    if local.is_file() and local.stat().st_size > 1_000_000:
        return local
    local.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading cloudflared")
    urllib.request.urlretrieve(DOWNLOAD_URL, local)
    return local


class QuickTunnel:
    def __init__(self, port: int) -> None:
        self.port = port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[str] | None = None

    def start(self) -> None:
        try:
            PUBLIC_URL_FILE.unlink(missing_ok=True)
        except OSError:
            log.warning("could not clear the previous public url; it will be replaced when the tunnel is ready")
        self._thread = threading.Thread(target=self._run, name="quick-tunnel", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _run(self) -> None:
        if not self._wait_for_port():
            log.error("local server did not accept connections; quick tunnel was not started")
            return
        try:
            exe = cloudflared_executable()
        except Exception:
            log.exception("cloudflared is not available")
            return
        log.info("starting quick tunnel with %s", exe)
        flags = CREATE_NO_WINDOW if os.name == "nt" else 0
        self._proc = subprocess.Popen(
            [str(exe), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            text = line.rstrip()
            if text:
                log.info("cloudflared: %s", text)
            match = URL_RE.search(text)
            if match:
                url = match.group(0)
                PUBLIC_URL_FILE.write_text(url + "\n", encoding="utf-8")
                log.info("public url %s", url)
            if self._stop.is_set():
                break

    def _wait_for_port(self) -> bool:
        deadline = time.time() + 20
        while time.time() < deadline and not self._stop.is_set():
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return True
            except OSError:
                time.sleep(0.2)
        return False
