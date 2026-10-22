import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from playwright.sync_api import Error as PlaywrightError

from rent_tracker.browser import Blocked
from rent_tracker.llm import LlmError

COLLECT_ERRORS = (
    httpx.HTTPError,
    PlaywrightError,
    Blocked,
    LlmError,
    subprocess.SubprocessError,
    OSError,
    RuntimeError,
    ValueError,
    KeyError,
    TypeError,
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def http_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
    )


Fetch = Callable[[str], str]


def http_fetch(client: httpx.Client) -> Fetch:
    def fetch(url: str) -> str:
        resp = client.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} at {url}")
        return resp.text

    return fetch


@dataclass
class RawPages:
    raw_dir: Path
    fetch: Fetch | None

    def get(self, name: str, url: str) -> str:
        path = self.raw_dir / name
        if self.fetch is None:
            return path.read_text()
        text = self.fetch(url)
        path.write_text(text)
        return text
