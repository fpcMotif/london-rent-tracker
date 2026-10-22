import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

CHALLENGE_TITLES = (
    "just a moment",
    "attention required",
    "access denied",
    "verify you are human",
)
LOAD_MORE = re.compile(r"^\s*(load|show|view|see)\s+more", re.IGNORECASE)
NEXT_PAGE = re.compile(r"^\s*(»|›|next( page)?)\s*$", re.IGNORECASE)
MAX_PAGES = 20


class Blocked(Exception):
    pass


@dataclass
class PageResult:
    url: str
    final_url: str
    status: int | None
    title: str
    raw_html: str
    text: str


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


class CdpChrome:
    def __init__(self, chrome: str, port: int, profile_dir: Path) -> None:
        self.chrome = chrome
        self.port = port
        self.profile_dir = profile_dir
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> Self:
        if not _port_open(self.port):
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._proc = subprocess.Popen(
                [
                    self.chrome,
                    f"--remote-debugging-port={self.port}",
                    f"--user-data-dir={self.profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 20
            while not _port_open(self.port):
                if time.monotonic() > deadline:
                    raise RuntimeError("Chrome did not open its debugging port")
                time.sleep(0.3)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{self.port}"
        )
        return self

    def __exit__(self, *exc: object) -> None:
        self._pw.stop()
        if self._proc:
            self._proc.terminate()

    def get(self, url: str, expand: bool = False) -> PageResult:
        page = self._browser.contexts[0].new_page()
        try:
            return _load(page, url, expand)
        finally:
            page.close()


def _load(page: Page, url: str, expand: bool) -> PageResult:
    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    for _ in range(12):
        page.wait_for_timeout(1500)
        if not page.title().lower().startswith(CHALLENGE_TITLES):
            break
    else:
        raise Blocked(f"bot challenge did not clear: {page.title()!r}")
    raw = ""
    if resp is not None:
        try:
            raw = resp.text()
        except PlaywrightError:
            raw = page.content()
    final_url = page.url
    title = page.title()
    text = _all_pages_text(page) if expand else page.inner_text("body")
    return PageResult(
        url=url,
        final_url=final_url,
        status=resp.status if resp else None,
        title=title,
        raw_html=raw,
        text=text,
    )


def _all_pages_text(page: Page) -> str:
    texts = []
    for n in range(1, MAX_PAGES + 1):
        _expand(page)
        texts.append(f"=== results page {n} ===\n{page.inner_text('body')}")
        nxt = page.get_by_role("link", name=NEXT_PAGE)
        if nxt.count() == 0 or not nxt.first.is_visible():
            break
        nxt.first.click()
        page.wait_for_timeout(3000)
    return "\n\n".join(texts)


def _expand(page: Page) -> None:
    for _ in range(25):
        for _ in range(4):
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(400)
        button = page.get_by_role("button", name=LOAD_MORE)
        if button.count() == 0 or not button.first.is_visible():
            return
        button.first.click()
        page.wait_for_timeout(2000)
