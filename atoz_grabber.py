"""
A to Z Shift Grabber v3 - SPEED OPTIMIZED
==========================================
Built for competitive shift grabbing where shifts disappear in seconds.

Modes:
  run    : Single noon burst targeting +7 day
  daemon : Continuous fast-poll across all dates
  sniper : Inject live DOM watcher — grabs in milliseconds (experimental)

Author: Corwin for Rob
Version: 3.0.0
"""

import json, sys, re, logging, argparse, signal, random, time as _time
from datetime import datetime, timedelta, time as dt_time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"

try:
    from playwright.sync_api import sync_playwright, Page, BrowserContext
except ImportError:
    print("ERROR: Playwright not installed. Run setup.bat first.")
    sys.exit(1)

try:
    from winotify import Notification, audio
    WINOTIFY_AVAILABLE = True
except ImportError:
    WINOTIFY_AVAILABLE = False

SHUTDOWN = False
def _sig(s, f):
    global SHUTDOWN
    SHUTDOWN = True
signal.signal(signal.SIGINT, _sig)
signal.signal(signal.SIGTERM, _sig)


# ============================================================================
# Config
# ============================================================================

class ShiftWindow:
    def __init__(self, label: str, start_time: str, end_time: str):
        self.label = label
        self.start = self._parse(start_time)
        self.end = self._parse(end_time)

    @staticmethod
    def _parse(s: str) -> dt_time:
        s = s.strip().upper().replace(".", "")
        for f in ("%I:%M %p", "%I:%M%p", "%H:%M"):
            try: return datetime.strptime(s, f).time()
            except ValueError: pass
        raise ValueError(f"Bad time: '{s}'")

    def exact(self, s: dt_time, e: dt_time) -> bool:
        return self.start == s and self.end == e

    def within(self, s: dt_time) -> bool:
        return self.start <= s <= self.end

    def __repr__(self):
        return f"{self.label}({self.start.strftime('%I:%M%p')}-{self.end.strftime('%I:%M%p')})"


class Config:
    def __init__(self, path: Path = CONFIG_PATH):
        with open(path, "r", encoding="utf-8") as f:
            r = json.load(f)

        self.atoz_url = r["atoz_url"]
        self.shifts = [ShiftWindow(s["label"], s["start_time"], s["end_time"]) for s in r["preferred_shifts"]]
        self.match_mode = r.get("time_match_mode", "exact")
        self.blackouts: list[str] = r.get("blackout_days", [])
        self.job_types: list[str] = r.get("job_types", [])
        self.location: str = r.get("location", "")

        p = r.get("polling", {})
        self.poll_interval = p.get("poll_interval_seconds", 2)
        self.max_poll_min = p.get("max_poll_duration_minutes", 10)
        self.page_wait = p.get("page_load_wait_seconds", 3)

        d = r.get("daemon", {})
        self.cycle_sec = d.get("cycle_interval_seconds", 5)
        self.jitter_sec = d.get("jitter_seconds", 2)
        self.active_start = d.get("start_hour", 6)
        self.active_end = d.get("end_hour", 23)
        self.refresh_min = d.get("session_refresh_minutes", 30)

        n = r.get("notifications", {})
        self.notify_on = n.get("enabled", True)
        self.notify_sound = n.get("sound", True)

        lg = r.get("logging", {})
        self.log_file = lg.get("log_file", "atoz_grabber.log")
        self.log_mb = lg.get("max_log_size_mb", 10)
        self.log_backups = lg.get("backup_count", 3)

        b = r.get("browser", {})
        self.headless = b.get("headless", True)
        self.user_data = str(SCRIPT_DIR / b.get("user_data_dir", "./browser_session"))
        self.slow_mo = b.get("slow_mo_ms", 100)

        login = r.get("login", {})
        self.username = login.get("username", "")


# ============================================================================
# Logging
# ============================================================================

def mk_logger(c: Config) -> logging.Logger:
    lg = logging.getLogger("atoz")
    lg.setLevel(logging.DEBUG)
    if lg.handlers: lg.handlers.clear()

    fh = RotatingFileHandler(SCRIPT_DIR / c.log_file, maxBytes=c.log_mb*1024*1024,
                             backupCount=c.log_backups, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s|%(levelname)-7s|%(message)s", "%Y-%m-%d %H:%M:%S"))

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s|%(levelname)-7s|%(message)s", "%H:%M:%S"))

    lg.addHandler(fh); lg.addHandler(ch)
    return lg


# ============================================================================
# Notifications
# ============================================================================

def notify(title: str, msg: str, c: Config, lg: logging.Logger):
    if not c.notify_on: return
    if WINOTIFY_AVAILABLE:
        try:
            t = Notification(app_id="AtoZ Grabber", title=title, msg=msg[:256], duration="long")
            if c.notify_sound: t.set_audio(audio.Default, loop=False)
            t.show(); lg.info(f"NOTIFY: {title}")
        except: pass


# ============================================================================
# Shift parsing + matching
# ============================================================================

class Shift:
    def __init__(self, start: dt_time, end: dt_time, dur: str, loc: str, job: str, raw: str, date: str = ""):
        self.start, self.end, self.dur, self.loc, self.job, self.raw, self.date = start, end, dur, loc, job, raw, date

    def uid(self): return f"{self.date}|{self.start}|{self.end}|{self.job}"

    def __repr__(self):
        d = f"[{self.date}] " if self.date else ""
        return f"{d}{self.start.strftime('%I:%M%p')}-{self.end.strftime('%I:%M%p')} {self.dur} {self.loc} {self.job}"


def parse_time(s: str) -> dt_time:
    s = re.sub(r'\s*(E[DS]T|[A-Z]{2,4})\s*$', '', s.strip()).strip().upper().replace(".", "")
    for f in ("%I:%M%p", "%I:%M %p", "%H:%M"):
        try: return datetime.strptime(s, f).time()
        except ValueError: pass
    raise ValueError(f"Bad time: '{s}'")


# Combined JS: extract shifts AND get all tab badge counts in ONE call
SCAN_JS = """
() => {
    // --- Tab badges ---
    const tabs = [];
    for (const tab of document.querySelectorAll('[role="tab"]')) {
        const l = tab.getAttribute('aria-label') || tab.textContent || '';
        const dm = l.match(/(\\w+),\\s*(\\w+)\\s+(\\d+)/);
        const sm = l.match(/(\\d+)\\s+shifts?\\s+available/i);
        if (dm) tabs.push({day: dm[1], month: dm[2], num: parseInt(dm[3]),
                           count: sm ? parseInt(sm[1]) : 0, selected: l.includes('selected')});
    }

    // --- Current page shifts ---
    const shifts = [];
    for (const li of document.querySelectorAll('main li, main [role="listitem"]')) {
        const txt = li.textContent || '';
        const tm = txt.match(/(\\d{1,2}:\\d{2}(?:am|pm))\\s*-\\s*(\\d{1,2}:\\d{2}(?:am|pm))\\s*(?:E[DS]T)?/i);
        if (!tm) continue;

        const dm = txt.match(/\\((\\d+\\s*hrs?(?:\\s*\\d+\\s*min)?)\\)/i);
        const labels = Array.from(li.querySelectorAll('*')).map(e => e.textContent.trim()).filter(t => t.length > 1 && t.length < 50);
        const loc = labels.find(t => /^[A-Z]{2,5}\\d{0,3}$/.test(t)) || '';

        const types = ['Associate TDR','Dispatch Assist','RTS','Sorting','Pick','Pack','Stow','Count','Inbound','Outbound','Problem Solve','Water Spider','Dock','Ship Dock','Receive Dock','ICQA','AFE','Singles','Rebin'];
        let job = '';
        for (const l of labels) { for (const k of types) { if (l === k) { job = k; break; } } if (job) break; }

        if (!Array.from(li.querySelectorAll('button')).some(b => (b.textContent||'').toLowerCase().includes('add shift'))) continue;

        shifts.push({s: tm[1], e: tm[2], dur: dm ? dm[1].trim() : '?', loc, job, raw: tm[0]});
    }
    return {tabs, shifts};
}
"""

# Click "Add shift" by matching time text — returns immediately
GRAB_JS = """
(timeText) => {
    for (const li of document.querySelectorAll('main li, main [role="listitem"]')) {
        if (!(li.textContent||'').includes(timeText)) continue;
        for (const btn of li.querySelectorAll('button')) {
            if ((btn.textContent||'').toLowerCase().includes('add shift')) {
                btn.click(); return true;
            }
        }
    }
    return false;
}
"""

# Click a date tab by day name + number — returns immediately
TAB_JS = """
(args) => {
    const dayName = args.day;
    const dayNum = args.num;
    for (const tab of document.querySelectorAll('[role="tab"]')) {
        const l = tab.getAttribute('aria-label') || tab.textContent || '';
        if (l.includes(dayName) && l.includes(String(dayNum))) { tab.click(); return true; }
    }
    return false;
}
"""

# Sniper: inject a MutationObserver that auto-grabs matching shifts in milliseconds
SNIPER_JS = """
(config) => {
    // Kill any existing observer
    if (window._atozObserver) { window._atozObserver.disconnect(); }
    window._atozGrabLog = [];

    const parseTime = (s) => {
        s = s.trim().toUpperCase().replace(/\\s*(EDT|EST|CDT|CST|PDT|PST)\\s*$/i, '').replace('.','');
        const m = s.match(/(\\d{1,2}):(\\d{2})(AM|PM)/);
        if (!m) return null;
        let h = parseInt(m[1]); const min = parseInt(m[2]); const ap = m[3];
        if (ap === 'PM' && h < 12) h += 12;
        if (ap === 'AM' && h === 12) h = 0;
        return h * 60 + min;
    };

    // Pre-compute preferred windows as minute values for fast comparison
    const windows = config.shifts.map(w => ({
        s: parseTime(w.start), e: parseTime(w.end), label: w.label
    }));

    const tryGrab = () => {
        const items = document.querySelectorAll('main li, main [role="listitem"]');
        for (const li of items) {
            const txt = li.textContent || '';
            const tm = txt.match(/(\\d{1,2}:\\d{2}(?:am|pm))\\s*-\\s*(\\d{1,2}:\\d{2}(?:am|pm))/i);
            if (!tm) continue;

            const shiftStart = parseTime(tm[1]);
            const shiftEnd = parseTime(tm[2]);
            if (shiftStart === null || shiftEnd === null) continue;

            // Check if matches any window (exact mode)
            let matched = false;
            for (const w of windows) {
                if (w.s === shiftStart && w.e === shiftEnd) { matched = true; break; }
            }
            if (!matched) continue;

            // Check not already grabbed
            const uid = `${shiftStart}-${shiftEnd}`;
            if (window._atozGrabLog.includes(uid)) continue;

            // GRAB IT
            const btns = li.querySelectorAll('button');
            for (const btn of btns) {
                if ((btn.textContent||'').toLowerCase().includes('add shift')) {
                    btn.click();
                    window._atozGrabLog.push(uid);
                    console.log('[ATOZ-SNIPER] GRABBED: ' + tm[0]);

                    // Also click any confirm dialog that might appear
                    setTimeout(() => {
                        for (const b of document.querySelectorAll('button')) {
                            const t = (b.textContent||'').toLowerCase();
                            if (t === 'confirm' || t === 'yes' || t === 'ok' || t === 'accept') {
                                b.click();
                                console.log('[ATOZ-SNIPER] CONFIRMED');
                                break;
                            }
                        }
                    }, 500);
                    break;
                }
            }
        }
    };

    // Run immediately on current page
    tryGrab();

    // Watch for ANY DOM changes in the main content area
    const target = document.querySelector('main') || document.body;
    window._atozObserver = new MutationObserver((mutations) => {
        tryGrab();
    });

    window._atozObserver.observe(target, {
        childList: true, subtree: true, characterData: true
    });

    return { status: 'armed', windows: windows.length, watching: true };
}
"""

# Check sniper grab log
SNIPER_CHECK_JS = """
() => {
    return {
        grabs: window._atozGrabLog || [],
        active: !!window._atozObserver
    };
}
"""


def extract_shifts(page: Page, lg: logging.Logger) -> tuple:
    """Extract tab badges + current shifts in one JS call. Returns (tabs, shifts)."""
    try:
        data = page.evaluate(SCAN_JS)
    except Exception as e:
        lg.error(f"Scan failed: {e}")
        return [], []

    tabs = data.get("tabs", [])
    raw_shifts = data.get("shifts", [])
    shifts = []
    for s in raw_shifts:
        try:
            shifts.append(Shift(parse_time(s["s"]), parse_time(s["e"]), s["dur"], s["loc"], s["job"], s["raw"]))
        except Exception as e:
            lg.warning(f"Parse fail: {s} — {e}")
    return tabs, shifts


def matches(s: Shift, c: Config, lg: logging.Logger) -> bool:
    if c.location and s.loc and s.loc != c.location: return False
    if c.job_types and s.job:
        if not any(j.lower() in s.job.lower() for j in c.job_types): return False
    for w in c.shifts:
        if c.match_mode == "exact" and w.exact(s.start, s.end):
            lg.info(f"  MATCH: {s} <> {w}"); return True
        if c.match_mode == "start_within" and w.within(s.start):
            lg.info(f"  MATCH: {s} <> {w}"); return True
    return False


def grab(page: Page, shift: Shift, lg: logging.Logger) -> bool:
    try:
        if page.evaluate(GRAB_JS, shift.raw):
            lg.info(f"  CLICKED: {shift}")
            page.wait_for_timeout(1500)
            try:
                cb = page.locator("button:has-text('Confirm'), button:has-text('Yes'), button:has-text('OK'), button:has-text('Accept')")
                if cb.count() > 0:
                    cb.first.click(); lg.info(f"  CONFIRMED"); page.wait_for_timeout(1000)
            except: pass
            return True
        lg.warning(f"  No button: {shift}"); return False
    except Exception as e:
        lg.error(f"  Grab error: {e}"); return False


# ============================================================================
# Login
# ============================================================================

def logged_in(page: Page, lg: logging.Logger, quiet=False) -> bool:
    try:
        url, title = page.url.lower(), page.title().lower()
        if any(x in url for x in ["login","midway","sso","signin","authenticate"]):
            if not quiet: lg.warning("Login page detected.")
            return False
        if "idprism" in url or "idprism" in title:
            if not quiet: lg.info("IdPrism auth in progress...")
            return False
        if page.locator("img[alt*='Employee']").count() > 0: return True
        if any(x in title for x in ["find shifts","a to z","atoz"]): return True
        if "atoz.amazon.work" in url: return True
        return False
    except: return False


def do_login(ctx: BrowserContext, c: Config, lg: logging.Logger) -> bool:
    lg.info("=" * 40 + " AUTO-LOGIN " + "=" * 40)
    page = ctx.new_page()
    page.goto(c.atoz_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    # Auto-fill username if configured and we're on the login page
    if c.username:
        for attempt in range(3):
            url = page.url.lower()
            if "login" in url or "idprism" in url:
                lg.info(f"Login page detected. Auto-filling username: {c.username}")
                try:
                    username_input = page.locator('input[placeholder="Enter your Login"], input[type="text"]').first
                    username_input.wait_for(state="visible", timeout=10000)
                    username_input.fill(c.username)
                    page.wait_for_timeout(500)
                    submit_btn = page.locator('button[type="submit"]').first
                    submit_btn.click()
                    lg.info("Submitted login. Waiting for redirect...")
                    page.wait_for_timeout(5000)
                except Exception as e:
                    lg.warning(f"Auto-fill attempt {attempt+1} failed: {e}")
                    page.wait_for_timeout(2000)
                    continue
            else:
                break

    # Wait for login to complete (max 60 seconds)
    elapsed = 0
    while elapsed < 60:
        if logged_in(page, lg, quiet=True):
            lg.info("Auto-login successful!")
            notify("Login OK", "Auto-login complete.", c, lg)
            page.close()
            return True
        page.wait_for_timeout(2000)
        elapsed += 2

    # Fallback: wait for manual login
    lg.warning("Auto-login incomplete. Waiting for manual login...")
    notify("Login Help", "Check browser - auto-login may need help.", c, lg)
    while elapsed < 300:
        page.wait_for_timeout(3000)
        elapsed += 3
        if logged_in(page, lg, quiet=(elapsed % 15 != 0)):
            lg.info("Login OK!")
            notify("Login OK", "Session saved.", c, lg)
            page.close()
            return True

    lg.error("Login timeout.")
    page.close()
    return False


def ensure_login(page: Page, ctx: BrowserContext, c: Config, lg: logging.Logger):
    if logged_in(page, lg, quiet=True): return True, page
    lg.warning("Session expired.")
    page.close()
    if not do_login(ctx, c, lg): return False, None
    return True, ctx.new_page()


# ============================================================================
# Helpers
# ============================================================================

def is_blackout(target, bl: list[str]) -> bool:
    if isinstance(target, datetime):
        ds, dn = target.strftime("%Y-%m-%d"), target.strftime("%A")
    elif isinstance(target, dict):
        dn = target.get("day", "")
        ds = ""
    else:
        ds, dn = str(target), ""
    return any(e.strip() == ds or e.strip().lower() == dn.lower() for e in bl)


def launch_browser(c: Config, headed=False):
    pw = sync_playwright().start()
    Path(c.user_data).mkdir(parents=True, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=c.user_data,
        headless=not headed and c.headless,
        slow_mo=c.slow_mo, viewport={"width": 1440, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
    )
    return pw, ctx


# ============================================================================
# DAEMON v3 — Fast Poll
# ============================================================================

def run_daemon(c: Config, lg: logging.Logger):
    lg.info("=" * 60)
    lg.info("DAEMON v3 — FAST POLL MODE")
    lg.info(f"  Windows : {c.shifts}")
    lg.info(f"  Cycle   : ~{c.cycle_sec}s")
    lg.info(f"  Hours   : {c.active_start}-{c.active_end}")
    lg.info(f"  Ctrl+C to stop")
    lg.info("=" * 60)
    notify("Daemon Started", f"Fast-poll every ~{c.cycle_sec}s", c, lg)

    grabbed_uids: set = set()
    total_grabbed: list = []

    pw, ctx = launch_browser(c)
    try:
        page = ctx.new_page()
        page.goto(c.atoz_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(c.page_wait * 1000)

        ok, page = ensure_login(page, ctx, c, lg)
        if not ok: return
        if "find" not in page.url:
            page.goto(c.atoz_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(c.page_wait * 1000)

        cycle = 0
        last_refresh = datetime.now()
        now_year = datetime.now().year

        while not SHUTDOWN:
            now = datetime.now()

            # Outside active hours — sleep longer
            if now.hour < c.active_start or now.hour >= c.active_end:
                lg.info(f"Outside hours ({c.active_start}-{c.active_end}). Sleep 5m.")
                _time.sleep(300)
                continue

            cycle += 1

            # Session refresh periodically
            if (now - last_refresh).total_seconds() / 60 >= c.refresh_min:
                lg.info("Session refresh...")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(c.page_wait * 1000)
                ok, page = ensure_login(page, ctx, c, lg)
                if not ok: break
                if "find" not in page.url:
                    page.goto(c.atoz_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(c.page_wait * 1000)
                last_refresh = datetime.now()

            # --- FAST SCAN: reload page, read ALL tabs + current shifts in one call ---
            page.reload(wait_until="domcontentloaded")
            # Minimal wait — just enough for the shift list to render
            page.wait_for_timeout(1500)

            tabs, current_shifts = extract_shifts(page, lg)

            # Find tabs with available shifts (excluding blackouts)
            hot_tabs = []
            for t in tabs:
                if t["count"] == 0: continue
                # Blackout check
                day_name = t["day"]
                if any(b.strip().lower() == day_name.lower() for b in c.blackouts):
                    continue
                # Try date-based blackout
                try:
                    for yr in [now_year, now_year + 1]:
                        try:
                            pd = datetime.strptime(f"{t['month']} {t['num']} {yr}", "%b %d %Y")
                            if pd.date() >= now.date(): break
                        except ValueError: continue
                    if pd.strftime("%Y-%m-%d") in c.blackouts: continue
                    t["date_str"] = pd.strftime("%Y-%m-%d")
                except:
                    t["date_str"] = f"{t['month']}-{t['num']}"
                hot_tabs.append(t)

            # Check shifts on the currently selected tab first (no navigation needed)
            if current_shifts:
                # Figure out which tab is selected
                selected_tab = next((t for t in tabs if t.get("selected")), None)
                selected_date = selected_tab["date_str"] if selected_tab and "date_str" in selected_tab else "current"

                for s in current_shifts:
                    s.date = selected_date
                    if s.uid() in grabbed_uids: continue
                    if matches(s, c, lg):
                        lg.info(f">>> GRAB: {s}")
                        if grab(page, s, lg):
                            grabbed_uids.add(s.uid())
                            total_grabbed.append(s)
                            notify("SHIFT GRABBED!", str(s), c, lg)

            # Now check OTHER hot tabs (ones not currently selected)
            other_hot = [t for t in hot_tabs if not t.get("selected")]

            if other_hot:
                for t in other_hot:
                    if SHUTDOWN: break
                    lbl = f"{t['day']} {t['month']} {t['num']}"

                    # Navigate via URL (faster than clicking tab + waiting)
                    date_str = t.get("date_str", "")
                    if date_str and "-" not in date_str[:4]:
                        # Fallback: click tab
                        page.evaluate(TAB_JS, {"day": t["day"], "num": t["num"]})
                    else:
                        page.goto(f"{c.atoz_url}&date={date_str}", wait_until="domcontentloaded", timeout=15000)

                    page.wait_for_timeout(1500)

                    _, tab_shifts = extract_shifts(page, lg)
                    for s in tab_shifts:
                        s.date = date_str or lbl
                        if s.uid() in grabbed_uids: continue
                        if matches(s, c, lg):
                            lg.info(f">>> GRAB: {s}")
                            if grab(page, s, lg):
                                grabbed_uids.add(s.uid())
                                total_grabbed.append(s)
                                notify("SHIFT GRABBED!", str(s), c, lg)

                # Navigate back to base URL for next cycle
                page.goto(c.atoz_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1000)

            # Status line (compact)
            hot_summary = ", ".join(f"{t['day'][:3]}{t['num']}({t['count']})" for t in hot_tabs) if hot_tabs else "none"
            if cycle % 10 == 0 or hot_tabs:
                lg.info(f"C{cycle} | hot: {hot_summary} | grabbed: {len(total_grabbed)}")
            else:
                lg.debug(f"C{cycle} | hot: {hot_summary}")

            # Sleep with small jitter
            jitter = random.uniform(-c.jitter_sec, c.jitter_sec)
            sleep = max(2, c.cycle_sec + jitter)

            # Sleep using Python time.sleep (survives browser crashes)
            slept = 0.0
            while slept < sleep and not SHUTDOWN:
                _time.sleep(min(2.0, sleep - slept))
                slept += min(2.0, sleep - slept)

        # Shutdown
        lg.info(f"{'='*60}\nDAEMON STOPPED — {cycle} cycles, {len(total_grabbed)} grabbed")
        for s in total_grabbed: lg.info(f"  {s}")
        notify(f"Stopped — {len(total_grabbed)} shifts", f"{cycle} cycles", c, lg)
        page.close()

    except Exception as e:
        lg.error(f"Fatal: {e}", exc_info=True)
        notify("ERROR", str(e)[:200], c, lg)
    finally:
        ctx.close(); pw.stop()


# ============================================================================
# SNIPER MODE — DOM watcher with instant grab
# ============================================================================

def run_sniper(c: Config, lg: logging.Logger):
    """
    Inject a MutationObserver into the page that watches for new shift elements
    and grabs matching ones in MILLISECONDS. Combines with periodic page reloads
    to catch shifts across all dates.
    """
    lg.info("=" * 60)
    lg.info("SNIPER MODE — Millisecond reaction time")
    lg.info(f"  Windows : {c.shifts}")
    lg.info(f"  Ctrl+C to stop")
    lg.info("=" * 60)
    notify("Sniper Armed", "Watching for shifts with instant grab", c, lg)

    # Build config object for the JS observer
    sniper_config = {
        "shifts": [{"start": w.start.strftime("%I:%M%p"), "end": w.end.strftime("%I:%M%p"), "label": w.label}
                   for w in c.shifts]
    }

    grabbed_total: list = []
    pw, ctx = launch_browser(c)

    try:
        page = ctx.new_page()
        page.goto(c.atoz_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(c.page_wait * 1000)

        ok, page = ensure_login(page, ctx, c, lg)
        if not ok: return
        if "find" not in page.url:
            page.goto(c.atoz_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(c.page_wait * 1000)

        # Arm the sniper on the current page
        result = page.evaluate(SNIPER_JS, sniper_config)
        lg.info(f"Sniper armed: {result}")

        cycle = 0
        last_refresh = datetime.now()
        now_year = datetime.now().year

        while not SHUTDOWN:
            now = datetime.now()

            if now.hour < c.active_start or now.hour >= c.active_end:
                lg.info("Outside hours. Sleep 5m.")
                _time.sleep(300)
                continue

            cycle += 1

            # Check if sniper caught anything
            try:
                status = page.evaluate(SNIPER_CHECK_JS)
                new_grabs = status.get("grabs", [])
                if len(new_grabs) > len(grabbed_total):
                    for g in new_grabs[len(grabbed_total):]:
                        lg.info(f"SNIPER GRAB: {g}")
                        notify("SNIPER GRAB!", g, c, lg)
                    grabbed_total = new_grabs[:]
            except: pass

            # Periodically cycle through hot tabs
            if cycle % 6 == 0:  # Every ~30 seconds at 5s cycle
                # Scan tabs
                tabs, _ = extract_shifts(page, lg)
                hot = [t for t in tabs if t["count"] > 0 and not t.get("selected")]

                # Check blackouts
                hot_clean = []
                for t in hot:
                    if any(b.strip().lower() == t["day"].lower() for b in c.blackouts): continue
                    try:
                        for yr in [now_year, now_year + 1]:
                            try:
                                pd = datetime.strptime(f"{t['month']} {t['num']} {yr}", "%b %d %Y")
                                if pd.date() >= now.date(): break
                            except ValueError: continue
                        if pd.strftime("%Y-%m-%d") in c.blackouts: continue
                        t["date_str"] = pd.strftime("%Y-%m-%d")
                    except:
                        t["date_str"] = f"{t['month']}-{t['num']}"
                    hot_clean.append(t)

                for t in hot_clean:
                    if SHUTDOWN: break
                    ds = t.get("date_str", "")
                    if ds and len(ds) == 10:
                        page.goto(f"{c.atoz_url}&date={ds}", wait_until="domcontentloaded", timeout=15000)
                    else:
                        page.evaluate(TAB_JS, {"day": t["day"], "num": t["num"]})
                    page.wait_for_timeout(1500)
                    # Re-arm sniper on new page content
                    page.evaluate(SNIPER_JS, sniper_config)
                    page.wait_for_timeout(3000)

                # Return to base
                page.goto(c.atoz_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1500)
                page.evaluate(SNIPER_JS, sniper_config)

            # Session refresh
            if (now - last_refresh).total_seconds() / 60 >= c.refresh_min:
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(c.page_wait * 1000)
                ok, page = ensure_login(page, ctx, c, lg)
                if not ok: break
                if "find" not in page.url:
                    page.goto(c.atoz_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(c.page_wait * 1000)
                page.evaluate(SNIPER_JS, sniper_config)
                last_refresh = datetime.now()

            if cycle % 10 == 0:
                lg.info(f"Sniper C{cycle} | grabs: {len(grabbed_total)}")

            # Sleep using Python time.sleep (survives browser crashes)
            slept = 0.0
            while slept < c.cycle_sec and not SHUTDOWN:
                _time.sleep(min(2.0, c.cycle_sec - slept))
                slept += min(2.0, c.cycle_sec - slept)

        lg.info(f"SNIPER STOPPED — {cycle} cycles, {len(grabbed_total)} grabs")
        notify(f"Sniper Stopped — {len(grabbed_total)}", f"{cycle} cycles", c, lg)
        page.close()

    except Exception as e:
        lg.error(f"Fatal: {e}", exc_info=True)
        notify("ERROR", str(e)[:200], c, lg)
    finally:
        ctx.close(); pw.stop()


# ============================================================================
# Single run (original noon burst)
# ============================================================================

def run_single(c: Config, lg: logging.Logger, date_override=None, login_only=False, no_wait=False):
    target = datetime.strptime(date_override, "%Y-%m-%d") if date_override else datetime.now() + timedelta(days=7)
    tstr, tdisp = target.strftime("%Y-%m-%d"), target.strftime("%A, %b %d")
    url = f"{c.atoz_url}&date={tstr}"

    lg.info(f"SINGLE — targeting {tdisp} ({tstr})")

    if is_blackout(target, c.blackouts):
        lg.info("Blackout day."); return

    pw, ctx = launch_browser(c, headed=login_only)
    try:
        page = ctx.new_page()

        # --- Step 1: Navigate to A to Z with target date in URL ---
        lg.info(f"Navigating to: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(c.page_wait * 1000)

        # --- Step 2: Auto-login ---
        ok, page = ensure_login(page, ctx, c, lg)
        if not ok: return
        if login_only: lg.info("Login done."); page.close(); return

        # --- Step 3: Navigate to target date via URL (most reliable method) ---
        lg.info(f"Navigating to target date: {tdisp}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(c.page_wait * 1000)
        lg.info(f"On {tdisp} tab. Ready and waiting.")

        # --- Step 4: Wait until 11:59 if we're early ---
        if not no_wait:
            poll_start_time = datetime.now().replace(hour=11, minute=59, second=0, microsecond=0)
            now = datetime.now()
            if now < poll_start_time:
                wait_sec = (poll_start_time - now).total_seconds()
                lg.info(f"Waiting {wait_sec:.0f}s until 11:59 AM...")
                while wait_sec > 0 and not SHUTDOWN:
                    sleep_chunk = min(30, wait_sec)
                    _time.sleep(sleep_chunk)
                    wait_sec -= sleep_chunk
                lg.info("11:59 AM - Starting poll!")

        # --- Step 5: Poll for shifts ---
        deadline = datetime.now() + timedelta(minutes=c.max_poll_min)
        grabbed, count = [], 0

        while datetime.now() < deadline and not SHUTDOWN:
            count += 1

            # Navigate with date param each time (guarantees correct tab)
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)

            _, avail = extract_shifts(page, lg)
            lg.info(f"Poll#{count}: {len(avail)} shifts on {tdisp}")

            for s in avail:
                if matches(s, c, lg) and not any(g.uid() == s.uid() for g in grabbed):
                    lg.info(f">>> GRAB: {s}")
                    if grab(page, s, lg):
                        grabbed.append(s)
                        notify("Grabbed!", str(s), c, lg)

            page.wait_for_timeout(c.poll_interval * 1000)

        lg.info(f"DONE - {count} polls, {len(grabbed)} grabbed")
        if grabbed:
            for s in grabbed: lg.info(f"  {s}")
        notify(f"Done - {len(grabbed)} grabbed", f"{count} polls, {len(grabbed)} shifts", c, lg)
        page.close()
    except Exception as e:
        lg.error(f"Fatal: {e}", exc_info=True)
        notify("ERROR", str(e)[:200], c, lg)
    finally:
        ctx.close(); pw.stop()


# ============================================================================
# CLI
# ============================================================================

def main():
    p = argparse.ArgumentParser(description="AtoZ Grabber v3", formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="  run | daemon | sniper | login | test | blackout add Saturday")
    sub = p.add_subparsers(dest="cmd")

    rp = sub.add_parser("run");    rp.add_argument("--date"); rp.add_argument("--now", action="store_true"); rp.add_argument("--headed", action="store_true")
    dp = sub.add_parser("daemon"); dp.add_argument("--headed", action="store_true")
    sp = sub.add_parser("sniper"); sp.add_argument("--headed", action="store_true")
    sub.add_parser("login")
    tp = sub.add_parser("test");   tp.add_argument("--headed", action="store_true")
    bp = sub.add_parser("blackout"); bp.add_argument("action", choices=["list","add","remove"]); bp.add_argument("value", nargs="?")

    args = p.parse_args()
    if not args.cmd: args.cmd = "run"; args.date = None; args.now = False; args.headed = False

    c = Config()

    if args.cmd == "blackout":
        _blackout(args); return

    lg = mk_logger(c)
    if hasattr(args, "headed") and args.headed: c.headless = False

    if args.cmd == "login":    run_single(c, lg, login_only=True)
    elif args.cmd == "test":   run_single(c, lg, date_override=datetime.now().strftime("%Y-%m-%d"), no_wait=True)
    elif args.cmd == "run":    run_single(c, lg, date_override=getattr(args,"date",None), no_wait=getattr(args,"now",False))
    elif args.cmd == "daemon": run_daemon(c, lg)
    elif args.cmd == "sniper": run_sniper(c, lg)


def _blackout(args):
    with open(CONFIG_PATH, "r") as f: r = json.load(f)
    bl = r.get("blackout_days", [])
    if args.action == "list":
        print("Blackouts:" if bl else "None.")
        for i,d in enumerate(bl,1): print(f"  {i}. {d}")
    elif args.action == "add":
        if not args.value: print("Need value"); return
        if args.value not in bl:
            bl.append(args.value); r["blackout_days"] = bl
            with open(CONFIG_PATH, "w") as f: json.dump(r, f, indent=4)
            print(f"Added: {args.value}")
    elif args.action == "remove":
        if args.value in bl:
            bl.remove(args.value); r["blackout_days"] = bl
            with open(CONFIG_PATH, "w") as f: json.dump(r, f, indent=4)
            print(f"Removed: {args.value}")


if __name__ == "__main__":
    main()
