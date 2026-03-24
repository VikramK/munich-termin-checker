"""Shared fixtures for integration tests.

The key fixture is ``mock_zms_page`` which serves a local HTML page that
replicates the ZMS appointment widget's nested shadow DOM structure.  This
lets us integration-test the full check_appointment flow without hitting
the real site.
"""
import os
import pytest
from playwright.sync_api import sync_playwright


# ---------------------------------------------------------------------------
# Mock HTML that replicates the ZMS widget shadow DOM structure
# ---------------------------------------------------------------------------
# The real site renders:
#   <zms-appointment> shadowRoot
#     <zms-appointment-i18n-host>
#       <zms-appointment-wrapped> shadowRoot
#         .altcha-checkbox input, .altcha[data-state], .m-button--primary, <p> ...
#
# This mock builds the same structure via JS so Playwright's >> selectors
# can traverse it identically.

MOCK_ZMS_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Mock ZMS Widget</title></head>
<body>
<zms-appointment id="zms"></zms-appointment>
<script>
// ---- Configuration injected by test via window.__ZMS_CONFIG ----
// {
//   noAppointment: bool    -- if true, show "no appointment" text
//   bookingResult: string  -- confirmation text after booking
// }
const CFG = window.__ZMS_CONFIG || {};

// Build the outer shadow root
const zms = document.getElementById('zms');
const outerShadow = zms.attachShadow({mode: 'open'});

// Intermediate host element (matches real DOM)
const host = document.createElement('zms-appointment-i18n-host');
outerShadow.appendChild(host);

// Wrapped element with inner shadow root
const wrapped = document.createElement('zms-appointment-wrapped');
host.appendChild(wrapped);
const innerShadow = wrapped.attachShadow({mode: 'open'});

// ---- ALTCHA captcha mock ----
const altcha = document.createElement('div');
altcha.className = 'altcha';
altcha.setAttribute('data-state', 'unverified');
innerShadow.appendChild(altcha);

const altchaCheckboxDiv = document.createElement('div');
altchaCheckboxDiv.className = 'altcha-checkbox';
const altchaInput = document.createElement('input');
altchaInput.type = 'checkbox';
// Simulate proof-of-work: clicking the checkbox sets verified after 100ms
altchaInput.addEventListener('click', () => {
    altcha.setAttribute('data-state', 'verifying');
    setTimeout(() => {
        altcha.setAttribute('data-state', 'verified');
        altchaInput.checked = true;
    }, 100);
});
altchaCheckboxDiv.appendChild(altchaInput);
innerShadow.appendChild(altchaCheckboxDiv);

// ---- "Weiter" button (initially disabled) ----
const weiterBtn = document.createElement('button');
weiterBtn.className = 'm-button--primary';
weiterBtn.textContent = 'Weiter';
weiterBtn.setAttribute('aria-disabled', 'true');
innerShadow.appendChild(weiterBtn);

// Enable the button once ALTCHA is verified
const observer = new MutationObserver(() => {
    if (altcha.getAttribute('data-state') === 'verified') {
        weiterBtn.setAttribute('aria-disabled', 'false');
    }
});
observer.observe(altcha, {attributes: true});

// ---- State machine for the booking wizard ----
let step = 'initial';

weiterBtn.addEventListener('click', () => {
    if (weiterBtn.getAttribute('aria-disabled') === 'true') return;

    if (step === 'initial') {
        step = 'result';
        innerShadow.querySelectorAll('p').forEach(p => p.remove());

        if (CFG.noAppointment) {
            const p = document.createElement('p');
            p.textContent = 'Leider kein Termin verfügbar. Bitte versuchen Sie es noch einmal.';
            innerShadow.appendChild(p);
        } else {
            step = 'calendar';
            renderCalendar();
        }
    } else if (step === 'timeslot') {
        step = 'form';
        innerShadow.querySelectorAll('.timeslot, muc-calendar').forEach(e => e.remove());
        renderContactForm();
    } else if (step === 'form') {
        step = 'summary';
        innerShadow.querySelectorAll('.contact-form').forEach(e => e.remove());
        renderSummary();
    }
});

function renderCalendar() {
    const cal = document.createElement('muc-calendar');
    const dayBtn = document.createElement('button');
    dayBtn.className = 'calendar-day';
    dayBtn.textContent = '15';
    dayBtn.setAttribute('data-date', '2026-04-15');
    dayBtn.addEventListener('click', () => {
        step = 'timeslot';
        cal.remove();
        renderTimeslots();
    });
    cal.appendChild(dayBtn);
    innerShadow.appendChild(cal);
}

function renderTimeslots() {
    const container = document.createElement('div');
    container.className = 'timeslot';
    ['09:00', '10:30', '14:00'].forEach(t => {
        const btn = document.createElement('button');
        btn.className = 'timeslot-btn';
        btn.textContent = t;
        btn.addEventListener('click', () => {
            weiterBtn.setAttribute('aria-disabled', 'false');
        });
        container.appendChild(btn);
    });
    innerShadow.appendChild(container);
    weiterBtn.setAttribute('aria-disabled', 'true');
}

function renderContactForm() {
    const form = document.createElement('div');
    form.className = 'contact-form';

    const fn = document.createElement('input');
    fn.name = 'firstName';
    fn.placeholder = 'Vorname';
    form.appendChild(fn);

    const ln = document.createElement('input');
    ln.name = 'lastName';
    ln.placeholder = 'Nachname';
    form.appendChild(ln);

    const email = document.createElement('input');
    email.name = 'mailAddress';
    email.type = 'email';
    email.placeholder = 'E-Mail';
    form.appendChild(email);

    innerShadow.appendChild(form);
    weiterBtn.setAttribute('aria-disabled', 'false');
}

function renderSummary() {
    // Privacy checkboxes
    const cb1 = document.createElement('input');
    cb1.type = 'checkbox';
    cb1.name = 'privacy';
    innerShadow.appendChild(cb1);

    const cb2 = document.createElement('input');
    cb2.type = 'checkbox';
    cb2.name = 'electronic';
    innerShadow.appendChild(cb2);

    // "Termin buchen" button
    const bookBtn = document.createElement('button');
    bookBtn.className = 'm-button--primary';
    bookBtn.textContent = 'Termin buchen';
    bookBtn.setAttribute('aria-disabled', 'false');
    bookBtn.addEventListener('click', () => {
        step = 'booked';
        innerShadow.querySelectorAll('input[type=checkbox]').forEach(e => e.remove());
        bookBtn.remove();
        weiterBtn.remove();

        const h3 = document.createElement('h3');
        h3.textContent = 'Termin erfolgreich gebucht!';
        innerShadow.appendChild(h3);

        const p = document.createElement('p');
        p.textContent = CFG.bookingResult || 'Ihr Termin am 15.04.2026 um 09:00 wurde gebucht.';
        innerShadow.appendChild(p);
    });
    innerShadow.appendChild(bookBtn);

    weiterBtn.style.display = 'none';
}
</script>
</body>
</html>
"""


def _write_mock_html(tmp_path):
    """Write the mock HTML to a temp file and return its file:// URL."""
    html_path = tmp_path / "mock_zms.html"
    html_path.write_text(MOCK_ZMS_HTML)
    return f"file://{html_path}", html_path


@pytest.fixture(scope="session")
def browser():
    """Launch a single Chromium browser for the entire test session."""
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def context(browser):
    """Create a fresh browser context per test (clean state)."""
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
    yield ctx
    ctx.close()


@pytest.fixture
def mock_zms_page(context, tmp_path):
    """Serve the mock ZMS HTML and return a page navigated to it.

    Default config: appointments available, no special settings.
    """
    url, _ = _write_mock_html(tmp_path)
    page = context.new_page()
    page.goto(url)
    return page


@pytest.fixture
def mock_zms_no_appointment(context, tmp_path):
    """Serve the mock ZMS HTML configured to show 'no appointment'."""
    url, _ = _write_mock_html(tmp_path)
    page = context.new_page()
    page.add_init_script("window.__ZMS_CONFIG = {noAppointment: true};")
    page.goto(url)
    return page


@pytest.fixture
def mock_zms_booking(context, tmp_path):
    """Serve the mock ZMS HTML configured for a full booking flow."""
    url, _ = _write_mock_html(tmp_path)
    page = context.new_page()
    page.add_init_script("""
        window.__ZMS_CONFIG = {
            noAppointment: false,
            bookingResult: 'Termin am 15.04.2026 um 09:00 gebucht. Ref: TEST-12345'
        };
    """)
    page.goto(url)
    return page


@pytest.fixture
def mock_url_no_appointment(context, tmp_path):
    """Return (page, file_url) for a no-appointment mock — page NOT pre-navigated.

    Used by TestCheckAppointment which needs to monkeypatch TARGET_URL
    and let check_appointment() handle navigation.
    """
    url, _ = _write_mock_html(tmp_path)
    page = context.new_page()
    page.add_init_script("window.__ZMS_CONFIG = {noAppointment: true};")
    return page, url


@pytest.fixture
def mock_url_booking(context, tmp_path):
    """Return (page, file_url) for a booking mock — page NOT pre-navigated."""
    url, _ = _write_mock_html(tmp_path)
    page = context.new_page()
    page.add_init_script("""
        window.__ZMS_CONFIG = {
            noAppointment: false,
            bookingResult: 'Termin am 15.04.2026 um 09:00 gebucht. Ref: TEST-12345'
        };
    """)
    return page, url


@pytest.fixture
def env_setup(tmp_path, monkeypatch):
    """Set up environment variables for testing (no real email sending)."""
    monkeypatch.setenv("FIRST_NAME", "Test")
    monkeypatch.setenv("LAST_NAME", "User")
    monkeypatch.setenv("RECEIVER_EMAIL", "test@example.com")
    monkeypatch.setenv("SENDER_EMAIL", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "fake_password")

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    return logs_dir
