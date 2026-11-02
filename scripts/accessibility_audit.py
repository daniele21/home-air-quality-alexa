#!/usr/bin/env python3
"""Synthetic-only Chrome accessibility/reflow smoke audit using CDP.

This is *not* real iPhone, Safari or VoiceOver testing. It verifies:
- semantic browser accessibility tree (headings/nav/tables/named controls)
- keyboard Tab traversal + focus-visibility
- equivalent 200% CSS zoom/reflow at desktop/tablet/mobile widths
- accessible mobile navigation and real data after JS hydration.
No real sensor data is requested: AIRWISE_VISUAL_PORT must be 18766.
No screenshot or private content is logged.
"""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import aiohttp

PORT=18766
PAGES=(("/","overview"),("/pollution","pollution"),("/context","context"))
CHROME=(
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
)

class AuditError(Exception): pass

class CDP:
    def __init__(self, ws):
        self.ws=ws
        self.seq=0
    async def call(self,method,params=None):
        self.seq+=1
        key=self.seq
        await self.ws.send_json({"id":key,"method":method,"params":params or {}})
        while True:
            msg=await self.ws.receive(timeout=20)
            if msg.type!=aiohttp.WSMsgType.TEXT: raise AuditError("CDP lost")
            data=json.loads(msg.data)
            if data.get("id")!=key: continue
            if "error" in data: raise AuditError("CDP command "+method+" failed")
            return data.get("result",{})
    async def eval(self,expression):
        response=await self.call("Runtime.evaluate",{
            "expression":expression,"returnByValue":True,"awaitPromise":True})
        if "exceptionDetails" in response: raise AuditError("JS browser evaluation error")
        return response.get("result",{}).get("value")

GEOMETRY=r"""(() => {
  const b=document.documentElement, nav=document.querySelector('.aw-nav');
  const r=nav?.getBoundingClientRect(), shown=e=>!!e && !!e.getClientRects().length;
  const actions=[...document.querySelectorAll('button,input,select,.aw-nav a')].filter(shown);
  const tooNarrow=actions.filter(e=>!e.disabled && (e.getBoundingClientRect().width<30||e.getBoundingClientRect().height<30));
  const main=document.querySelector('main')||document.getElementById('app');
  const mainRect=main?.getBoundingClientRect();
  const bottom=nav?.getBoundingClientRect();
  const chart=document.querySelector('canvas');
  return {
    width:Math.round(innerWidth),
    scrollWidth:Math.round(b.scrollWidth),
    bodyScrollWidth:Math.round(document.body.scrollWidth),
    viewportHeight:Math.round(innerHeight),
    navVisible:shown(nav)&&r.width>0&&r.height>0,
    navLinks:nav?.querySelectorAll('a').length||0,
    navInViewport:!!r&&r.left>=-2&&r.right<=innerWidth+2,
    navLabelled:nav?.getAttribute('aria-label')?.length>0,
    navSelection:nav?.querySelectorAll('a[aria-current="page"]').length||0,
    labelledChart:!!chart?.getAttribute('aria-label'),
    mainVisible:shown(main),
    smallActions:tooNarrow.length,
    dataTables:document.querySelectorAll('table').length,
    headings:document.querySelectorAll('h1').length,
    nonEmptyApp:!!document.querySelector('[data-days].active'),
    rangeReady:!['','—'].includes(document.querySelector('#dateRange,#range')?.textContent?.trim()),
    fixedNavOverlap:!!bottom&&!!mainRect&&bottom.top<0
  };
})()"""
FOCUS=r"""(() => {
 const e=document.activeElement;
 const css=e?getComputedStyle(e):null;
 const bounds=e?.getBoundingClientRect();
 return {
  tag:e?.tagName||'NONE',
  insideBody:!!e&&e!==document.body,
  outlined:css?.outlineStyle!=='none'&&parseFloat(css?.outlineWidth||'0')>0,
  visible:!!bounds&&bounds.width>0&&bounds.height>0,
  role:e?.getAttribute('role')||null,
  className:typeof e?.className==='string'?e.className.slice(0,50):'',
  ariaLabel:!!e?.getAttribute('aria-label'),
 };
})()"""

async def main():
    if os.environ.get("AIRWISE_VISUAL_PORT")!="18766":
        raise AuditError("Only synthetic loopback port 18766 permitted")
    chrome=next((p for p in CHROME if p.is_file() and os.access(p,os.X_OK)),None)
    if not chrome: raise AuditError("No Chrome/Chromium")
    issues=[]
    counts=[]
    with tempfile.TemporaryDirectory(prefix="airwise-a11y-") as profile:
        proc=subprocess.Popen([str(chrome),"--headless=new","--disable-gpu",
            "--disable-background-networking","--disable-extensions",
            "--no-first-run","--no-default-browser-check",
            "--remote-debugging-port=0",f"--user-data-dir={profile}","about:blank"],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            dev=Path(profile)/"DevToolsActivePort"
            for _ in range(70):
                if dev.is_file(): break
                if proc.poll() is not None: raise AuditError("Chrome exited")
                await asyncio.sleep(.2)
            if not dev.is_file(): raise AuditError("CDP not ready")
            port=int(dev.read_text().splitlines()[0])
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25),trust_env=False) as client:
                async with client.get(f"http://127.0.0.1:{port}/json/list") as resp:
                    tabs=await resp.json()
                tab=next((x for x in tabs if x.get("type")=="page"),None)
                if not tab:raise AuditError("No CDP tab")
                async with client.ws_connect(tab["webSocketDebuggerUrl"],heartbeat=10) as ws:
                    c=CDP(ws)
                    await c.call("Page.enable");await c.call("Runtime.enable")
                    await c.call("Accessibility.enable")
                    for route,name in PAGES:
                        for width,scale in ((375,1),(375,2),(768,2),(1280,2)):
                            await c.call("Emulation.setDeviceMetricsOverride",{
                                "width":width,"height":844,"deviceScaleFactor":1,"mobile":width<=375})
                            await c.call("Page.navigate",{"url":f"http://127.0.0.1:{PORT}{route}"})
                            await asyncio.sleep(.6)
                            await c.eval(f"document.documentElement.style.zoom={scale};true")
                            await asyncio.sleep(.2)
                            m=await c.eval(GEOMETRY)
                            if not isinstance(m,dict):raise AuditError("Invalid browser metrics")
                            counts.append((name,width,scale,m))
                            # Chrome CSS zoom at fixed viewport is a conservative reflow stress test,
                            # not a claim of true iOS pinch-to-zoom behavior.
                            over=max(m["scrollWidth"],m["bodyScrollWidth"])-m["width"]
                            if over>2:issues.append(f"{name}:{width}x@{scale}:overflow={over}")
                            for field in ("navVisible","navLabelled","navInViewport","labelledChart","mainVisible","nonEmptyApp","rangeReady"):
                                if not m[field]:issues.append(f"{name}:{width}x@{scale}:{field}")
                            if m["navLinks"]!=3 or m["navSelection"]!=1:
                                issues.append(f"{name}:{width}x@{scale}:navigation")
                            if m["headings"]!=1 or m["dataTables"]<1:
                                issues.append(f"{name}:{width}x@{scale}:landmarks")
                            if scale==1 and m["smallActions"]:
                                issues.append(f"{name}:{width}x@{scale}:small_targets={m['smallActions']}")
                            if scale==1:
                                tree=await c.call("Accessibility.getFullAXTree")
                                nodes=tree.get("nodes",[])
                                roles=[x.get("role",{}).get("value","") for x in nodes if not x.get("ignored")]
                                if "navigation" not in roles:issues.append(f"{name}:accessibility_navigation_missing")
                                if "heading" not in roles:issues.append(f"{name}:accessibility_heading_missing")
                                for role in ("button","link"):
                                    unnamed=sum(1 for x in nodes if not x.get("ignored")
                                        and x.get("role",{}).get("value")==role
                                        and not x.get("name",{}).get("value","").strip())
                                    if unnamed:issues.append(f"{name}:unnamed_{role}={unnamed}")
                                # Hardware keyboard Tab navigation. No click or data mutations.
                                seen=0;visible=0;outlined=0
                                for _ in range(12):
                                    await c.call("Input.dispatchKeyEvent",{
                                        "type":"rawKeyDown","key":"Tab","code":"Tab",
                                        "windowsVirtualKeyCode":9,"nativeVirtualKeyCode":48})
                                    await c.call("Input.dispatchKeyEvent",{
                                        "type":"keyUp","key":"Tab","code":"Tab",
                                        "windowsVirtualKeyCode":9,"nativeVirtualKeyCode":48})
                                    f=await c.eval(FOCUS)
                                    if f["insideBody"]:
                                        seen+=1
                                        if f["visible"]:visible+=1
                                        if f["outlined"]:outlined+=1
                                if seen<8:issues.append(f"{name}:tab_traversal={seen}")
                                if visible<8:issues.append(f"{name}:focus_hidden={visible}")
                                if outlined<6:issues.append(f"{name}:focus_ring={outlined}")
                                print(f"ACCESSIBILITY page={name} named_controls=true tab_steps={seen} "
                                      f"visible_focus={visible} outlined_focus={outlined}")
                            print(f"REFLOW page={name} width={width} zoom={scale}x "
                                  f"body_overflow_px={over} small_targets={m['smallActions']} "
                                  f"nav={m['navSelection']}/{m['navLinks']}")
        finally:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
    for issue in issues: print("A11Y_ISSUE="+issue)
    print("A11Y_AUDIT="+("PASS" if not issues else "FAIL"))
    print("LIMITATION=Chrome synthetic keyboard and CSS zoom; not physical iPhone or VoiceOver")
    return 1 if issues else 0

if __name__=="__main__":
    raise SystemExit(asyncio.run(main()))
