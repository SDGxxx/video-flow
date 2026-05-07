from __future__ import annotations

import json
import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright


VIDEO_URL = os.getenv("DOUYIN_PROBE_URL", "https://www.douyin.com/video/example")
COOKIE_FILE = Path(os.getenv("DOUYIN_COOKIE_FILE", "cookies.txt"))
CHROME_EXE = Path(os.getenv("CHROME_EXE", "chrome"))


def load_netscape_cookies(path: Path) -> list[dict[str, object]]:
    cookies: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, include_subdomains, cookie_path, secure, expires, name, value = parts
        cookie: dict[str, object] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": cookie_path,
            "secure": secure.upper() == "TRUE",
            "httpOnly": False,
        }
        if expires.isdigit() and int(expires) > 0:
            cookie["expires"] = int(expires)
        cookies.append(cookie)
    return cookies


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=str(CHROME_EXE),
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            viewport={"width": 1440, "height": 1600},
        )
        context.add_cookies(load_netscape_cookies(COOKIE_FILE))
        page = context.new_page()

        responses: list[tuple[str, int, str]] = []

        def on_response(response):
            url = response.url
            if "douyin" in url or "aweme" in url or "webcast" in url:
                try:
                    body = response.text()
                except Exception:
                    body = ""
                responses.append((url, response.status, body[:1000]))

        page.on("response", on_response)
        page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(12000)
        html = page.content()
        text = page.locator("body").inner_text(timeout=10000)

        print("TITLE:", page.title())
        print("URL:", page.url)
        print("BODY_LEN:", len(text))
        print("HTML_LEN:", len(html))

        for key in ["description", "subtitle", "video", "aweme", "author", "challenge", "share", "content"]:
            if key in html.lower() or key in text.lower():
                print("KEYWORD:", key)

        patterns = [
            r'"desc"\s*:\s*"([^"]{0,2000})"',
            r'"description"\s*:\s*"([^"]{0,2000})"',
            r'"nickname"\s*:\s*"([^"]{0,200})"',
            r'"author"\s*:\s*\{',
            r'"itemList"\s*:\s*\[',
            r'"video"\s*:\s*\{',
            r'"subtitle"\s*:\s*\{',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, html)
            if matches:
                print("PATTERN:", pattern, "COUNT:", len(matches))
                for match in matches[:3]:
                    print("MATCH:", match[:500])

        print("RESPONSES:", len(responses))
        for url, status, body in responses[:20]:
            print("RESPONSE:", status, url)
            if body:
                print(body[:500].replace("\n", " ")[:500])

        browser.close()


if __name__ == "__main__":
    main()
