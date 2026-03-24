#!/usr/bin/env python3
"""
Government Appointment Checker & Auto-Booker
=============================================
Automatically polls a government appointment booking page (using the ZMS
appointment widget), solves the ALTCHA proof-of-work captcha, and — when
slots appear — selects the first timeslot, fills the guest booking form,
and completes the booking.  An email notification is sent on success.

Designed to run for a configurable duration (default 30 minutes) with
randomised intervals between checks.

Logs and screenshots for every attempt are written to a ``logs/`` directory
next to this script for full auditability.

Usage:
    python terminchecker.py

Configuration is read from a ``.env`` file (or environment variables, which
take precedence).  See ``.env.example`` for all available keys.
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
import html
import os
import sys
import time
import random
import smtplib
import traceback
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ---------------------------------------------------------------------------
# ANSI colour codes for terminal output
# ---------------------------------------------------------------------------
# Colours are only applied to terminal (stdout).  The log file always
# receives plain text so it remains easy to grep and parse.

class _C:
    """ANSI escape sequences.  Disabled when stdout is not a tty."""
    if sys.stdout.isatty():
        RESET   = "\033[0m"
        BOLD    = "\033[1m"
        DIM     = "\033[2m"
        RED     = "\033[31m"
        GREEN   = "\033[32m"
        YELLOW  = "\033[33m"
        CYAN    = "\033[36m"
        WHITE   = "\033[37m"
        BG_GREEN = "\033[42;97;1m"
    else:
        RESET = BOLD = DIM = RED = GREEN = YELLOW = CYAN = WHITE = BG_GREEN = ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Target URL for the appointment page.
TARGET_URL = (
    "https://stadt.muenchen.de/buergerservice/"
    "terminvereinbarung.html#/services/10339028/"
)

# Phrases (lowercase) on the result page when NO appointments are available.
NO_APPOINTMENT_PHRASES = [
    "bitte versuchen sie es noch einmal",
    "kein termin verfügbar",
    "keine termine",
]

# ---------------------------------------------------------------------------
# Playwright shadow-DOM piercing prefix
# ---------------------------------------------------------------------------
# The ZMS widget nests two shadow roots:
#   <zms-appointment>              -- shadowRoot
#     <zms-appointment-i18n-host>
#       <zms-appointment-wrapped>  -- shadowRoot  ("inner root")
#
# Playwright's ">>" combinator crosses each shadow boundary.
SHADOW = "zms-appointment >> zms-appointment-wrapped"

# ---------------------------------------------------------------------------
# CSS selectors for booking wizard elements (used inside the shadow root)
# ---------------------------------------------------------------------------

# Clickable calendar dates — muc-calendar may have its own shadow root
CALENDAR_SELECTORS_SHADOW = (
    "muc-calendar >> button:not([disabled]),"
    "muc-calendar >> .calendar-day:not(.disabled),"
    "muc-calendar >> td.available,"
    "muc-calendar >> [data-date]:not([disabled])"
)

# Calendar dates rendered directly (no muc-calendar shadow root)
CALENDAR_SELECTORS_DIRECT = (
    "muc-calendar button:not([disabled]),"
    ".calendar-day:not(.disabled),"
    ".muc-calendar__day--available,"
    "td.available a,"
    "[data-date]:not([disabled])"
)

# Timeslot buttons
TIMESLOT_SELECTORS = (
    ".timeslot button:not([disabled]),"
    "muc-button button:not([disabled]),"
    ".time-slot button:not([disabled]),"
    "button.timeslot:not([disabled])"
)

# Primary action button (not disabled)
PRIMARY_BTN = '.m-button--primary:not([aria-disabled="true"]), button.primary:not([aria-disabled="true"])'

# Book button selectors
BOOK_BTN = (
    '.m-button--primary:not([aria-disabled="true"]),'
    'button.primary:not([aria-disabled="true"]),'
    'button[type=submit]:not([aria-disabled="true"])'
)

# ---------------------------------------------------------------------------
# Paths & timing configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

CHECK_INTERVAL_MIN = 45   # seconds — minimum pause between attempts
CHECK_INTERVAL_MAX = 90   # seconds — maximum pause between attempts
MAX_DURATION_S = 30 * 60  # total run time before the script exits

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_log_fh = None  # file handle for the current run log


# =========================================================================
# Configuration
# =========================================================================

# Load .env into os.environ (existing env vars take precedence)
load_dotenv(ENV_FILE, override=False)


def cfg(key, default=None):
    """Read a config value.  Precedence: env var > .env file > *default*."""
    return os.environ.get(key) or default


# =========================================================================
# Logging helpers (colour on terminal, plain text in log file)
# =========================================================================

def _log_raw(plain, coloured):
    """Write *coloured* to stdout and *plain* to the log file."""
    print(coloured, flush=True)
    if _log_fh is not None:
        _log_fh.write(plain + "\n")
        _log_fh.flush()


def _ts():
    """Return the current timestamp string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    """Normal informational message."""
    plain = f"[{_ts()}] {msg}"
    coloured = f"{_C.DIM}[{_ts()}]{_C.RESET} {msg}"
    _log_raw(plain, coloured)


def log_step(step, msg, total=10):
    """Step progress message: ``[Step N/10]`` in cyan."""
    label = f"[Step {step}/{total}]"
    plain = f"[{_ts()}]   {label} {msg}"
    coloured = f"{_C.DIM}[{_ts()}]{_C.RESET}   {_C.CYAN}{label}{_C.RESET} {msg}"
    _log_raw(plain, coloured)


def log_ok(msg):
    """Success message with a green checkmark."""
    plain = f"[{_ts()}]   OK  {msg}"
    coloured = f"{_C.DIM}[{_ts()}]{_C.RESET}   {_C.GREEN}\u2714{_C.RESET}  {msg}"
    _log_raw(plain, coloured)


def log_fail(msg):
    """Failure message with a red cross."""
    plain = f"[{_ts()}]   FAIL  {msg}"
    coloured = f"{_C.DIM}[{_ts()}]{_C.RESET}   {_C.RED}\u2718  {msg}{_C.RESET}"
    _log_raw(plain, coloured)


def log_warn(msg):
    """Warning / non-critical notice in yellow."""
    plain = f"[{_ts()}]   {msg}"
    coloured = f"{_C.DIM}[{_ts()}]{_C.RESET}   {_C.YELLOW}{msg}{_C.RESET}"
    _log_raw(plain, coloured)


def log_detail(msg):
    """Indented detail line (dim, secondary info)."""
    plain = f"[{_ts()}]       {msg}"
    coloured = f"{_C.DIM}[{_ts()}]       {msg}{_C.RESET}"
    _log_raw(plain, coloured)


def log_banner(msg):
    """Bold white banner text."""
    plain = f"[{_ts()}] {msg}"
    coloured = f"{_C.DIM}[{_ts()}]{_C.RESET} {_C.BOLD}{_C.WHITE}{msg}{_C.RESET}"
    _log_raw(plain, coloured)


def log_success_banner(msg):
    """High-contrast success banner (white on green)."""
    pad = f"  {msg}  "
    plain = f"[{_ts()}] {pad}"
    coloured = f"{_C.DIM}[{_ts()}]{_C.RESET} {_C.BG_GREEN}{pad}{_C.RESET}"
    _log_raw(plain, coloured)


def log_result_negative(msg):
    """Result: no appointments — yellow with a dash."""
    plain = f"[{_ts()}]   RESULT  {msg}"
    coloured = f"{_C.DIM}[{_ts()}]{_C.RESET}   {_C.YELLOW}\u2013  {msg}{_C.RESET}"
    _log_raw(plain, coloured)


# =========================================================================
# Screenshot helper
# =========================================================================

def _screenshot(page, path):
    """Take a full-page screenshot.  Playwright handles viewport expansion."""
    try:
        page.screenshot(path=path, full_page=True)
        log_detail(f"Screenshot saved: {path}")
    except Exception as exc:
        log_fail(f"Screenshot failed: {exc}")


# =========================================================================
# Debug dump — captures DOM state + screenshot on failures
# =========================================================================

def _dump_debug(page, prefix):
    """Extract shadow-DOM state and save a screenshot for debugging.

    *prefix* is the filename prefix inside ``logs/``.
    """
    try:
        log_warn("--- Debug dump ---")

        # Use page.evaluate for the complex info-gathering since it requires
        # traversing shadow roots in a single JS call
        info = page.evaluate("""() => {
            const outer = document.querySelector('zms-appointment');
            if (!outer) return {error: 'zms-appointment not found'};
            if (!outer.shadowRoot) return {error: 'no outer shadowRoot'};
            const w = outer.shadowRoot.querySelector('zms-appointment-wrapped');
            if (!w) return {error: 'wrapped element not found',
                outerHtml: outer.shadowRoot.innerHTML.substring(0, 500)};
            if (!w.shadowRoot) return {error: 'no inner shadowRoot'};
            const r = w.shadowRoot;
            const a = r.querySelector('.altcha');
            const cb = r.querySelector('.altcha-checkbox input');
            return {
                altchaState: a ? a.getAttribute('data-state') : 'N/A',
                checkboxExists: !!cb,
                checkboxChecked: cb ? cb.checked : false,
                buttons: Array.from(r.querySelectorAll('button'))
                    .map(b => ({text: b.textContent.trim().substring(0, 50),
                                disabled: b.getAttribute('aria-disabled')})),
                paragraphs: Array.from(r.querySelectorAll('p'))
                    .map(p => p.textContent.trim()).filter(Boolean)
            };
        }""")
        for k, v in info.items():
            log_detail(f"{k}: {v}")

        _screenshot(page, os.path.join(LOGS_DIR, f"{prefix}.png"))
        log_warn("--- End debug dump ---")
    except Exception as exc:
        log_fail(f"Debug dump failed: {exc}")


# =========================================================================
# Email notification
# =========================================================================

def _send_email(booked=False, booking_details=None):
    """Send an HTML email notification.

    If *booked* is True, sends a booking confirmation with *booking_details*.
    Otherwise sends a generic "slots may be available" alert.
    """
    sender = cfg("SENDER_EMAIL")
    receiver = cfg("RECEIVER_EMAIL")
    password = cfg("SMTP_PASSWORD")

    if not sender or not receiver:
        log_fail("SENDER_EMAIL or RECEIVER_EMAIL not configured — skipping email.")
        return
    if not password:
        log_fail("SMTP_PASSWORD is not configured — skipping email.")
        return

    smtp_host = cfg("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(cfg("SMTP_PORT", "587"))

    safe_url = html.escape(TARGET_URL, quote=True)

    if booked and booking_details:
        subject = "Appointment BOOKED!"
        body = (
            "<h3>Appointment successfully booked!</h3>"
            "<table style='border-collapse:collapse;'>"
        )
        for label, value in booking_details.items():
            body += (
                f"<tr><td style='padding:4px 12px 4px 0;font-weight:bold;'>"
                f"{html.escape(str(label))}</td>"
                f"<td style='padding:4px 0;'>{html.escape(str(value))}</td></tr>"
            )
        body += "</table>"
        body += (
            "<p>Check the logs folder for confirmation screenshots.</p>"
            f'<p><a href="{safe_url}">Open booking page</a></p>'
        )
    else:
        subject = "Appointments May Be Available!"
        body = (
            "<h3>Appointments may be available!</h3>"
            "<p>The appointment page did <b>not</b> show the usual "
            "'no appointments' message.</p>"
            f'<p><a href="{safe_url}">Open booking page</a></p>'
        )

    msg = MIMEText(body, "html")
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = subject

    try:
        log(f"  Sending notification email to {receiver} ...")
        with smtplib.SMTP(smtp_host, smtp_port) as srv:
            srv.starttls()
            srv.login(sender, password)
            srv.sendmail(sender, receiver, msg.as_string())
        log_ok("Email sent successfully.")
    except Exception as exc:
        log_fail(f"Email failed: {type(exc).__name__}: {exc}")


# =========================================================================
# Playwright helpers
# =========================================================================

def _shadow(page, selector):
    """Return a Playwright locator that pierces into the ZMS shadow DOM.

    Prepends the two-level shadow root prefix so callers just pass the
    inner selector, e.g. ``_shadow(page, '.altcha-checkbox input')``.
    """
    return page.locator(f"{SHADOW} >> {selector}")


def _wait_and_click_weiter(page, tag, step_label, timeout=15000):
    """Wait for an active 'Weiter' button and click it.

    Returns True on success, False on timeout (sends debug dump + email).
    """
    try:
        btn = _shadow(page, PRIMARY_BTN).filter(has_text="Weiter")
        btn.first.wait_for(state="visible", timeout=timeout)
        btn.first.click()
        log_ok(f"Clicked 'Weiter' ({step_label}).")
        return True
    except PlaywrightTimeout:
        log_fail(f"'Weiter' button not active within {timeout // 1000}s ({step_label}).")
        _dump_debug(page, f"{tag}_{step_label}_timeout")
        _send_email()
        return False


def _fill_field(page, selectors, value):
    """Try multiple selectors to fill a form field inside the shadow DOM.

    Uses Playwright's ``fill()`` which dispatches ``input`` and ``change``
    events automatically (works with Vue/React reactivity).
    Returns True if a field was found and filled.
    """
    for sel in selectors:
        loc = _shadow(page, sel)
        if loc.count() > 0:
            loc.first.fill(value)
            return True
    return False


# =========================================================================
# Core: single appointment check
# =========================================================================

def check_appointment(page, attempt_num):
    """Run one complete appointment-check-and-book cycle.

    Steps 1-5: Check availability
      1. Load the booking page and wait for the widget shadow DOM.
      2. Locate the ALTCHA captcha checkbox.
      3. Click the checkbox and wait for proof-of-work verification.
      4. Click the "Weiter" (Next) button.
      5. Read the result page and decide if appointments are available.

    Steps 6-10: Auto-book (only if Step 5 finds availability)
      6.  Select the first available date on the calendar.
      7.  Select the first available timeslot.
      8.  Click "Weiter" to proceed to the contact form.
      9.  Fill in name and email, then click "Weiter".
      10. Accept privacy checkboxes and click "Termin buchen" (Book).

    Returns True if an appointment was booked (or confirmed available),
    False otherwise.
    """
    tag = f"attempt_{attempt_num:03d}"

    try:
        # -- Load page -----------------------------------------------------
        log("  Navigating to booking page ...")
        log_detail(TARGET_URL)
        page.goto(TARGET_URL, wait_until="domcontentloaded")

        # Random delay to let the widget JS initialise
        pause = random.uniform(4, 7)
        log_warn(f"Waiting {pause:.1f}s for widget JS to initialise ...")
        page.wait_for_timeout(pause * 1000)

        # -- Step 1: Wait for the inner shadow DOM -------------------------
        log_step(1, "Waiting for inner shadow DOM ...")
        try:
            _shadow(page, "*").first.wait_for(state="attached", timeout=30_000)
            log_ok("Inner shadow DOM is ready.")
        except PlaywrightTimeout:
            log_fail("Inner shadow DOM did not appear within 30 seconds.")
            _dump_debug(page, f"{tag}_step1_timeout")
            return False

        # -- Step 2: Locate ALTCHA captcha checkbox ------------------------
        log_step(2, "Looking for the ALTCHA anti-bot checkbox ...")
        altcha_cb = _shadow(page, ".altcha-checkbox input")
        try:
            altcha_cb.wait_for(state="attached", timeout=20_000)
            log_ok("ALTCHA checkbox is present.")
        except PlaywrightTimeout:
            log_fail("ALTCHA checkbox not found within 20 seconds.")
            _dump_debug(page, f"{tag}_step2_timeout")
            return False

        # -- Step 3: Click checkbox and wait for proof-of-work -------------
        log_step(3, "Clicking ALTCHA checkbox to start proof-of-work ...")
        altcha_cb.click()
        log_warn("Waiting up to 30s for proof-of-work computation ...")
        try:
            _shadow(page, '.altcha[data-state="verified"]').wait_for(
                state="attached", timeout=30_000
            )
            log_ok("ALTCHA captcha solved and verified.")
        except PlaywrightTimeout:
            log_fail("ALTCHA did not reach 'verified' state within 30s.")
            _dump_debug(page, f"{tag}_step3_timeout")
            return False

        _screenshot(page, os.path.join(LOGS_DIR, f"{tag}_altcha_verified.png"))

        # Brief human-like pause before clicking the button
        page.wait_for_timeout(random.uniform(1500, 3500))

        # -- Step 4: Click "Weiter" (Next) --------------------------------
        log_step(4, "Waiting for the 'Weiter' button to become active ...")
        weiter_btn = _shadow(page, PRIMARY_BTN)
        try:
            weiter_btn.first.wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeout:
            log_fail("'Weiter' button stayed disabled for 15 seconds.")
            _dump_debug(page, f"{tag}_step4_timeout")
            return False

        weiter_btn.first.click()
        log_ok("Clicked 'Weiter'.")
        log_warn("Waiting for the result page to load ...")
        page.wait_for_timeout(random.uniform(2500, 4500))

        # -- Step 5: Read the result page ----------------------------------
        log_step(5, "Reading page content to determine availability ...")
        paragraphs = _shadow(page, "p").all_text_contents()
        buttons = _shadow(page, "button").all_text_contents()
        all_text = " ".join(paragraphs + buttons).lower()

        log_detail(f"Paragraphs: {paragraphs}")
        log_detail(f"Buttons:    {buttons}")

        matched = next(
            (p for p in NO_APPOINTMENT_PHRASES if p in all_text), None
        )

        if matched:
            log_detail(f'Matched no-appointment phrase: "{matched}"')
            _screenshot(
                page,
                os.path.join(LOGS_DIR, f"{tag}_result_no_appointment.png"),
            )
            log_result_negative("No appointments available.")
            return False

        # Appointments may be available — proceed with booking
        log_success_banner(
            "APPOINTMENTS MAY BE AVAILABLE — starting booking flow!"
        )
        _screenshot(
            page,
            os.path.join(LOGS_DIR, f"{tag}_availability_detected.png"),
        )

        # ==============================================================
        # BOOKING FLOW — Steps 6-10
        # ==============================================================

        first_name = cfg("FIRST_NAME")
        last_name = cfg("LAST_NAME")
        booking_email = cfg("RECEIVER_EMAIL")

        if not first_name or not last_name:
            log_fail("FIRST_NAME or LAST_NAME not configured — "
                     "cannot auto-book.  Sending availability email instead.")
            _send_email()
            return True

        if not booking_email:
            log_fail("RECEIVER_EMAIL not configured — "
                     "cannot auto-book.  Sending availability email instead.")
            _send_email()
            return True

        # -- Step 6: Select the first available date -----------------------
        log_step(6, "Looking for the appointment calendar ...")
        page.wait_for_timeout(random.uniform(1500, 3000))

        # Try muc-calendar with its own shadow root first, then fall back
        # to dates rendered directly in the inner root.
        cal_shadow = _shadow(page, CALENDAR_SELECTORS_SHADOW)
        cal_direct = _shadow(page, CALENDAR_SELECTORS_DIRECT)

        try:
            cal_shadow.or_(cal_direct).first.wait_for(
                state="visible", timeout=20_000
            )
            log_ok("Calendar with available dates found.")
        except PlaywrightTimeout:
            log_fail("No clickable date found in the calendar within 20s.")
            _dump_debug(page, f"{tag}_step6_timeout")
            log_warn("Sending availability email — manual booking may "
                     "still be possible.")
            _send_email()
            return True

        # Click the first available date
        cal_shadow.or_(cal_direct).first.click()
        log_ok("Clicked the first available date.")
        _screenshot(page, os.path.join(LOGS_DIR, f"{tag}_step6_calendar.png"))
        page.wait_for_timeout(random.uniform(1500, 3000))

        # -- Step 7: Select the first available timeslot -------------------
        log_step(7, "Waiting for timeslot buttons to appear ...")
        timeslots = _shadow(page, TIMESLOT_SELECTORS)
        try:
            timeslots.first.wait_for(state="visible", timeout=15_000)
            log_ok("Timeslot buttons are visible.")
        except PlaywrightTimeout:
            log_fail("No timeslot buttons found within 15s.")
            _dump_debug(page, f"{tag}_step7_timeout")
            _send_email()
            return True

        timeslots.first.click()
        log_ok("Selected the first available timeslot.")
        _screenshot(page, os.path.join(LOGS_DIR, f"{tag}_step7_timeslot.png"))
        page.wait_for_timeout(random.uniform(1500, 3000))

        # -- Step 8: Click "Weiter" to go to the contact form -------------
        log_step(8, "Clicking 'Weiter' to proceed to the contact form ...")
        if not _wait_and_click_weiter(page, tag, "step8"):
            return True
        page.wait_for_timeout(random.uniform(2000, 4000))
        _screenshot(page, os.path.join(LOGS_DIR, f"{tag}_step8_contact_form.png"))

        # -- Step 9: Fill in the contact form ------------------------------
        log_step(9, "Filling in the guest booking form ...")
        log_detail(f"Name  : {first_name} {last_name}")
        log_detail(f"Email : {booking_email}")

        _fill_field(page, [
            '[name=firstName]', '#firstName',
            'input[placeholder*="Vorname"]', 'input[placeholder*="First"]',
        ], first_name)

        _fill_field(page, [
            '[name=lastName]', '#lastName',
            'input[placeholder*="Nachname"]', 'input[placeholder*="Last"]',
        ], last_name)

        _fill_field(page, [
            '[name=mailAddress]', '#mailAddress', '[name=email]',
            'input[type=email]', 'input[placeholder*="Mail"]',
            'input[placeholder*="mail"]',
        ], booking_email)

        log_ok("Form fields populated.")
        _screenshot(page, os.path.join(LOGS_DIR, f"{tag}_step9_form_filled.png"))
        page.wait_for_timeout(random.uniform(1000, 2000))

        # Click "Weiter" to proceed to the summary page
        log_detail("Clicking 'Weiter' to move to summary page ...")
        if not _wait_and_click_weiter(page, tag, "step9", timeout=10_000):
            return True
        page.wait_for_timeout(random.uniform(2000, 4000))

        # -- Step 10: Accept checkboxes and book ---------------------------
        log_step(10, "Accepting checkboxes on the summary page ...")
        _screenshot(page, os.path.join(LOGS_DIR, f"{tag}_step10_summary.png"))

        # Check all unchecked checkboxes (privacy policy + electronic comms).
        # Re-query after each check because :not(:checked) changes the set.
        while True:
            unchecked = _shadow(page, "input[type=checkbox]:not(:checked)")
            if unchecked.count() == 0:
                break
            unchecked.first.check(force=True)
        log_ok("All checkboxes checked.")
        page.wait_for_timeout(random.uniform(1000, 2000))

        # Click the "Termin buchen" (Book Appointment) button
        log_detail("Looking for 'Termin buchen' button ...")
        book_btn = _shadow(page, BOOK_BTN).filter(has_text="buchen")
        try:
            book_btn.first.wait_for(state="visible", timeout=10_000)
        except PlaywrightTimeout:
            # Fall back to English "book" text
            book_btn = _shadow(page, BOOK_BTN).filter(has_text="book")
            try:
                book_btn.first.wait_for(state="visible", timeout=2_000)
            except PlaywrightTimeout:
                log_fail("'Termin buchen' button not active within 10s.")
                _dump_debug(page, f"{tag}_step10_timeout")
                _send_email()
                return True

        book_btn.first.click()
        log_ok("Clicked 'Termin buchen'!")
        log_warn("Waiting for confirmation page ...")
        page.wait_for_timeout(random.uniform(3000, 5000))

        # Capture the final confirmation page
        _screenshot(page, os.path.join(LOGS_DIR, f"{tag}_step10_booked.png"))

        # Extract confirmation details for the email
        conf_parts = _shadow(
            page, "p, h2, h3, .callout, .confirmation, .muc-callout"
        ).all_text_contents()
        conf_text = "\n".join(t.strip() for t in conf_parts if t.strip())
        log_detail(f"Confirmation page text:\n{conf_text}")

        log_success_banner("APPOINTMENT BOOKED SUCCESSFULLY!")
        _send_email(
            booked=True,
            booking_details={
                "Name": f"{first_name} {last_name}",
                "Email": booking_email,
                "Confirmation": conf_text[:500] if conf_text else "(see screenshots)",
            },
        )
        return True

    except Exception as exc:
        log_fail(f"UNEXPECTED ERROR: {type(exc).__name__}: {exc}")
        log_detail(traceback.format_exc())
        try:
            _dump_debug(page, f"{tag}_error")
        except Exception:
            pass
        return False


# =========================================================================
# Main loop
# =========================================================================

def main():
    """Run the checker in a loop for up to ``MAX_DURATION_S`` seconds.

    Launches the Playwright browser once, creates a fresh page per attempt,
    and sleeps a randomised interval between checks.

    Returns 0 on successful booking, 1 if no appointments were found.
    """
    global _log_fh

    os.makedirs(LOGS_DIR, exist_ok=True)

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOGS_DIR, f"run_{run_ts}.log")
    _log_fh = open(log_path, "w")

    try:
        start = datetime.now()
        deadline = start + timedelta(seconds=MAX_DURATION_S)
        attempt = 0

        # -- Header banner -------------------------------------------------
        log_banner("=" * 60)
        log_banner("  Appointment Checker & Auto-Booker")
        log_banner("=" * 60)
        log_detail(f"Target URL     : {TARGET_URL}")
        log_detail(f"Check interval : {CHECK_INTERVAL_MIN}-{CHECK_INTERVAL_MAX}s (randomised)")
        log_detail(f"Max duration   : {MAX_DURATION_S // 60} minutes")
        log_detail(f"Started at     : {start.strftime('%Y-%m-%d %H:%M:%S')}")
        log_detail(f"Will stop at   : {deadline.strftime('%Y-%m-%d %H:%M:%S')}")
        log_detail(f"Log file       : {log_path}")
        log_detail(f"Screenshots    : {LOGS_DIR}/attempt_NNN_*.png")
        log_banner("=" * 60)

        _fn = cfg("FIRST_NAME")
        _ln = cfg("LAST_NAME")
        _bk = cfg("RECEIVER_EMAIL")
        if _fn and _ln:
            log_detail(f"Booking name   : {_fn} {_ln}")
            log_detail(f"Booking email  : {_bk or '(not set)'}")
        else:
            log_warn("FIRST_NAME / LAST_NAME not set — "
                     "auto-booking will be skipped if slots are found.")
        print()

        # Launch browser once; create a fresh context per attempt
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            while datetime.now() < deadline:
                attempt += 1
                remaining = (deadline - datetime.now()).total_seconds()
                mins, secs = divmod(int(remaining), 60)

                log_banner(f"--- Attempt #{attempt}  ({mins}m {secs}s remaining) "
                           f"{'-' * 36}")

                # Fresh context per attempt (clean cookies/state)
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080}
                )
                page = context.new_page()
                try:
                    found = check_appointment(page, attempt)
                finally:
                    context.close()
                    log_detail("Browser context closed.")

                if found:
                    log_success_banner(
                        f"Appointment found/booked on attempt #{attempt}! Stopping."
                    )
                    browser.close()
                    return 0

                # Sleep before next attempt if there is enough time left
                if datetime.now() + timedelta(seconds=CHECK_INTERVAL_MIN) < deadline:
                    pause = random.uniform(CHECK_INTERVAL_MIN, CHECK_INTERVAL_MAX)
                    log_warn(f"Sleeping {pause:.0f}s before next attempt ...")
                    time.sleep(pause)
                    print()
                else:
                    log_warn("Not enough time left for another check.")
                    break

            browser.close()

        log_banner("=" * 60)
        log_warn(f"Time limit reached after {attempt} attempt(s). "
                 "No appointments found.")
        log_banner("=" * 60)
        return 1

    except KeyboardInterrupt:
        log_warn("Interrupted by user (Ctrl+C). Shutting down.")
        return 130

    finally:
        _log_fh.close()
        _log_fh = None


if __name__ == "__main__":
    sys.exit(main())
