"""The agent's browser, and the wrapper every agent action goes through.

`AgentPage` is the only way agents touch a page. Each action:
  1. waits at the control checkpoint (pause / take over),
  2. re-checks the site policy for the current URL,
  3. performs the action,
  4. takes a screenshot and adds a step, with the agent's reason, to the timeline.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Locator, Page, Playwright, async_playwright

from applypilot.config import AutomationSettings
from applypilot.live.control import RunControl
from applypilot.live.events import EventBus
from applypilot.policy import Decision, decide

log = logging.getLogger(__name__)


class HandedOff(Exception):
    """The current site doesn't allow automation; the user now has the browser."""

    def __init__(self, decision: Decision):
        super().__init__(decision.warning)
        self.decision = decision


class LiveBrowser:
    """The browser for ONE application run.

    Each run gets its own browser context: separate tabs, cookies, local storage
    and form drafts. Nothing from one application can leak into the next: not a
    tab, not a half-filled form, not a session on the employer's site. The context
    is closed when the run ends, and `start()` also clears any tabs left over from
    before (for example, from a crash or from browsing by hand).
    """

    def __init__(self, settings: AutomationSettings):
        self.settings = settings
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.closed_leftovers = 0

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        if self.settings.backend == "docker":
            try:
                self._browser = await self._pw.chromium.connect_over_cdp(self.settings.cdp_url)
            except Exception as exc:
                await self._pw.stop()
                raise RuntimeError(
                    f"Can't reach the desktop browser at {self.settings.cdp_url}. "
                    "Start it with `applypilot desktop up`."
                ) from exc
            self.closed_leftovers = await self._close_leftovers(self._browser)
            # no_viewport: the page fills the desktop window instead of a fixed box.
            self.context = await self._browser.new_context(no_viewport=True)
        else:
            self._browser = await self._pw.chromium.launch(headless=False)
            self.context = await self._browser.new_context(viewport={"width": 1440, "height": 900})

    @staticmethod
    async def _close_leftovers(browser: Browser) -> int:
        """Close every tab from earlier runs. One blank tab stays so the window remains open."""
        closed = 0
        for i, ctx in enumerate(browser.contexts):
            pages = ctx.pages
            keep = pages[0] if i == 0 and pages else None
            for p in pages:
                if p is not keep:
                    await p.close()
                    closed += 1
            if keep is not None and keep.url != "about:blank":
                await keep.goto("about:blank")
                closed += 1
        return closed

    async def new_page(self) -> Page:
        assert self.context is not None, "call start() first"
        page = await self.context.new_page()
        await page.bring_to_front()
        if self.settings.backend == "docker":
            await self._maximize(page)
        return page

    async def _maximize(self, page: Page) -> None:
        # A new context opens a new window; fill the desktop so the live view isn't a small box.
        with contextlib.suppress(Exception):
            cdp = await page.context.new_cdp_session(page)
            win = await cdp.send("Browser.getWindowForTarget")
            await cdp.send("Browser.setWindowBounds",
                           {"windowId": win["windowId"], "bounds": {"windowState": "maximized"}})
            await cdp.detach()

    async def stop(self) -> None:
        """End the run: its context, and every tab in it, goes away."""
        with contextlib.suppress(Exception):
            if self.context:
                await self.context.close()
        if self.settings.backend == "local" and self._browser:
            with contextlib.suppress(Exception):
                await self._browser.close()
        if self._pw:
            await self._pw.stop()



class AgentPage:
    def __init__(
        self,
        page: Page,
        *,
        bus: EventBus,
        control: RunControl,
        settings: AutomationSettings,
        run_id: str,
    ):
        self.page = page
        self.bus = bus
        self.control = control
        self.settings = settings
        self.run_id = run_id
        self._warned_hosts: set[str] = set()
        self.decision: Decision | None = None
        self._shots = settings.screenshots_dir / run_id
        self._shots.mkdir(parents=True, exist_ok=True)

    # ---- policy -----------------------------------------------------------

    def check_policy(self) -> Decision:
        """Apply the site policy to wherever the page is now. Raises HandedOff if needed."""
        d = decide(self.page.url, self.settings.accept_tos_risk_for)
        self.decision = d
        if d.warning and d.host not in self._warned_hosts:
            self._warned_hosts.add(d.host)
            kind = "handoff" if not d.may_automate else "warning"
            self.bus.emit(kind, f"Site policy: {d.policy.level} on {d.host}", detail=d.warning,
                          url=self.page.url)
        if not d.may_automate:
            self.control.take_over()
            raise HandedOff(d)
        return d

    # ---- actions ------------------------------------------------------------

    async def goto(self, url: str, why: str) -> None:
        # Check the destination before loading it, so the agent never loads a
        # prohibited site on its own.
        pre = decide(url, self.settings.accept_tos_risk_for)
        if not pre.may_automate:
            self.decision = pre
            self._warned_hosts.add(pre.host)
            self.bus.emit("handoff", f"Site policy: {pre.policy.level} on {pre.host}",
                          detail=pre.warning or "", url=url)
            self.control.take_over()
            raise HandedOff(pre)

        async def act() -> None:
            await self.page.goto(url, wait_until="domcontentloaded")
            with contextlib.suppress(Exception):  # some pages never go idle; the DOM is enough
                await self.page.wait_for_load_state("networkidle", timeout=8000)

        await self._step(f"Open {pre.host}", why, act, check_before=False)
        self.check_policy()  # redirects can land somewhere else

    async def fill(self, target: Locator, value: str, *, label: str, why: str) -> None:
        await self._step(f"Fill “{label}”", why, lambda: target.fill(value), shot=False)

    async def upload(self, target: Locator, path: Path, *, label: str, why: str) -> None:
        await self._step(f"Upload {path.name} to “{label}”", why,
                         lambda: target.set_input_files(str(path)), shot=False)

    async def select(self, target: Locator, option: str, *, label: str, why: str) -> None:
        await self._step(f"Choose “{option}” for “{label}”", why,
                         lambda: target.select_option(label=option), shot=False)

    async def check(self, target: Locator, *, option: str, label: str, why: str) -> None:
        async def act() -> None:
            try:
                await target.check(timeout=3000)
            except Exception:
                # Styled checkboxes often hide the input; clicking its label does the same.
                input_id = await target.get_attribute("id")
                await self.page.locator(f'label[for="{input_id}"]').click(timeout=3000)

        await self._step(f"Choose “{option}” for “{label}”", why, act, shot=False)

    async def click(self, target: Locator, *, label: str, why: str) -> None:
        await self._step(f"Click “{label}”", why, lambda: target.click())
        self.check_policy()

    async def snapshot(self, title: str, detail: str = "", kind="info") -> None:
        self.bus.emit(kind, title, detail=detail, url=self.page.url, screenshot=await self._shot())

    async def _step(
        self, title: str, why: str, act: Callable[[], Awaitable], *, check_before=True, shot=True
    ) -> None:
        await self.control.checkpoint()
        if check_before:
            self.check_policy()
        await act()
        self.bus.emit("action", title, detail=why, url=self.page.url,
                      screenshot=await self._shot() if shot else None)

    async def _shot(self) -> str | None:
        name = f"{len(self.bus.history) + 1:03d}.png"
        try:
            await self.page.screenshot(path=self._shots / name)
        except Exception as exc:  # a screenshot failing should never fail the run
            log.warning("screenshot failed: %s", exc)
            return None
        return f"{self.run_id}/{name}"
