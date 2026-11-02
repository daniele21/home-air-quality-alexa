#!/usr/bin/env python3
"""Local-only Chrome DevTools mobile layout probe for Airwise.

Runs solely against localhost, saves private screenshots on this Mac and
prints safe layout counts to stdout. It never uploads data or screenshots.
Not a substitute for human visual inspection of saved screenshots.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import aiohttp


CHROME = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
)
ROOT = Path.home() / "Library/Application Support/Airwise/mobile-qa"
WIDTHS = (320, 375, 390, 768, 1024, 1440)
PAGES = (("/", "overview"), ("/pollution", "pollution"), ("/context", "context"))
PORT = 8766


class VisualAuditError(Exception):
    pass


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.sequence = 0

    async def call(self, method, params=None):
        self.sequence += 1
        req_id = self.sequence
        await self.ws.send_json({"id": req_id, "method": method, "params": params or {}})
        while True:
            msg = await self.ws.receive(timeout=20)
            if msg.type != aiohttp.WSMsgType.TEXT:
                raise VisualAuditError("CDP connection lost")
            value = json.loads(msg.data)
            if value.get("id") != req_id:
                continue
            if "error" in value:
                raise VisualAuditError("CDP command was rejected")
            return value.get("result", {})


METRIC_JS = r"""(() => {
  const doc=document.documentElement;
  const nav=document.querySelector('.aw-nav');
  const navbox=nav?.getBoundingClientRect();
  const visible=e=>e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const actions=[...document.querySelectorAll('button,input,select,.aw-nav a')].filter(visible);
  const tooSmall=actions.filter(e=>!e.disabled && (e.getBoundingClientRect().height<43.5 || e.getBoundingClientRect().width<43.5));
  const dataTables=[...document.querySelectorAll('.aw-data-details table')];
  return {
    viewport:Math.round(window.innerWidth),
    scrollWidth:Math.round(doc.scrollWidth),
    navLinks:nav?.querySelectorAll('a').length || 0,
    navVisible:!!navbox && navbox.width>0 && navbox.height>0,
    navFixed:nav ? getComputedStyle(nav).position==='fixed' : false,
    smallActions:tooSmall.length,
    dataTables:dataTables.length,
    screenshotReady:document.readyState==='complete',
    scriptRendered:!!document.querySelector('canvas'),
    title:document.title
  };
})()"""


async def screenshot_suite(chrome_path: Path) -> dict:
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ROOT, 0o700)
    runs = []
    with tempfile.TemporaryDirectory(prefix="chrome-qa-", dir=ROOT) as profile:
        p = Path(profile)
        proc = subprocess.Popen([
            str(chrome_path), "--headless=new", "--disable-gpu",
            "--disable-background-networking", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions",
            "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        try:
            port_file = p / "DevToolsActivePort"
            for _ in range(80):
                if port_file.is_file():
                    break
                if proc.poll() is not None:
                    raise VisualAuditError("Chrome failed to launch")
                await asyncio.sleep(.25)
            if not port_file.exists():
                raise VisualAuditError("No local Chrome debugging endpoint")
            port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
            # Only the local CDP debugging port and local Airwise data source.
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=24), trust_env=False) as client:
                async with client.get(f"http://127.0.0.1:{port}/json/list") as response:
                    pages = await response.json()
                urls = [page["webSocketDebuggerUrl"] for page in pages
                        if page.get("type") == "page" and "webSocketDebuggerUrl" in page]
                if not urls:
                    raise VisualAuditError("No browser page target")
                async with client.ws_connect(urls[0], heartbeat=10) as ws:
                    cdp = CDP(ws)
                    await cdp.call("Page.enable")
                    await cdp.call("Runtime.enable")
                    for route, label in PAGES:
                        for width in WIDTHS:
                            height = 844 if width <= 390 else 900
                            await cdp.call("Emulation.setDeviceMetricsOverride", {
                                "width":width,"height":height,"deviceScaleFactor":1,
                                "mobile":width < 768,
                            })
                            await cdp.call("Page.navigate", {"url":f"http://127.0.0.1:{PORT}{route}"})
                            await asyncio.sleep(.9)
                            result = await cdp.call("Runtime.evaluate", {
                                "expression":METRIC_JS,"returnByValue":True,
                                "awaitPromise":True,
                            })
                            metrics = result.get("result",{}).get("value")
                            if not isinstance(metrics,dict):
                                raise VisualAuditError("Browser did not return layout metrics")
                            shot = await cdp.call("Page.captureScreenshot", {
                                "format":"png","fromSurface":True,"captureBeyondViewport":False,
                            })
                            data = base64.b64decode(shot.get("data", ""), validate=True)
                            path = ROOT / f"{label}-{width}px.png"
                            with path.open("wb") as out:
                                out.write(data)
                            os.chmod(path, 0o600)
                            runs.append({"screen":label,"width":width,"metrics":metrics})
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    return {"screenshots":len(runs),"runs":runs}


async def main() -> int:
    chrome = next((x for x in CHROME if x.is_file() and os.access(x,os.X_OK)), None)
    if not chrome:
        print("visual_audit_status=SKIPPED; Chrome/Chromium absent")
        return 3
    try:
        result = await screenshot_suite(chrome)
    except Exception as exc:
        print(f"visual_audit_status=FAILED; reason_class={type(exc).__name__}")
        return 2
    failures = []
    for row in result["runs"]:
        m=row["metrics"]
        overflow=m["scrollWidth"]-m["viewport"]
        mobile=row["width"] <= 390
        if overflow>2 or m["navLinks"]!=3 or not m["navVisible"] or (mobile and not m["navFixed"]):
            failures.append(f"{row['screen']}:{row['width']}:layout")
        if m["smallActions"] > 0:
            failures.append(f"{row['screen']}:{row['width']}:hit_targets({m['smallActions']})")
        if m["dataTables"] < 1 or not m["scriptRendered"]:
            failures.append(f"{row['screen']}:{row['width']}:chart_alternative")
        print(
            f"screen={row['screen']} width={row['width']} "
            f"body_overflow_px={overflow} small_actions={m['smallActions']} "
            f"nav_fixed={m['navFixed']} tables={m['dataTables']}"
        )
    print(f"private_screenshots_saved={result['screenshots']}")
    print("screen_images_location=~/Library/Application Support/Airwise/mobile-qa (Mac only, 0600)")
    if failures:
        print("visual_audit_status=FAIL")
        for issue in failures:
            print("visual_issue="+issue)
        return 1
    print("visual_audit_status=PASS_LAYOUT; human screenshot review still required")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
