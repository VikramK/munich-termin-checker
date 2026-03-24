"""Integration tests for the appointment checker.

These tests use a mock HTML page (see conftest.py) that replicates the ZMS
widget's nested shadow DOM structure.  They exercise the Playwright shadow
DOM piercing, the ALTCHA captcha flow, and the full booking wizard.
"""
import os
import sys
from unittest.mock import patch

import pytest

# Add the project root to the path so we can import terminchecker
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import terminchecker as tc


# =========================================================================
# Shadow DOM navigation tests
# =========================================================================

class TestShadowDOMPiercing:
    """Verify Playwright can pierce the nested shadow DOM structure."""

    def test_inner_shadow_root_reachable(self, mock_zms_page):
        """The >> selector should reach elements inside the inner shadow root."""
        page = mock_zms_page
        altcha = page.locator("zms-appointment >> zms-appointment-wrapped >> .altcha")
        assert altcha.count() == 1
        assert altcha.get_attribute("data-state") == "unverified"

    def test_altcha_checkbox_clickable(self, mock_zms_page):
        """The ALTCHA checkbox should be reachable and clickable."""
        page = mock_zms_page
        cb = page.locator(
            "zms-appointment >> zms-appointment-wrapped >> .altcha-checkbox input"
        )
        assert cb.count() == 1
        assert cb.is_checked() is False

    def test_weiter_button_found(self, mock_zms_page):
        """The Weiter button should be found inside the shadow DOM."""
        page = mock_zms_page
        btn = page.locator(
            "zms-appointment >> zms-appointment-wrapped >> .m-button--primary"
        )
        assert btn.count() >= 1
        assert "Weiter" in btn.first.text_content()


# =========================================================================
# ALTCHA captcha flow tests
# =========================================================================

class TestAltchaCaptcha:
    """Test the ALTCHA proof-of-work captcha interaction."""

    def test_click_checkbox_triggers_verification(self, mock_zms_page):
        """Clicking the checkbox should transition data-state to 'verified'."""
        page = mock_zms_page
        shadow = "zms-appointment >> zms-appointment-wrapped"

        page.locator(f"{shadow} >> .altcha-checkbox input").click()

        page.locator(f'{shadow} >> .altcha[data-state="verified"]').wait_for(
            state="attached", timeout=5000
        )
        state = page.locator(f"{shadow} >> .altcha").get_attribute("data-state")
        assert state == "verified"

    def test_weiter_enabled_after_verification(self, mock_zms_page):
        """The Weiter button should become enabled after ALTCHA is verified."""
        page = mock_zms_page
        shadow = "zms-appointment >> zms-appointment-wrapped"

        btn = page.locator(f"{shadow} >> .m-button--primary")
        assert btn.first.get_attribute("aria-disabled") == "true"

        page.locator(f"{shadow} >> .altcha-checkbox input").click()
        page.locator(f'{shadow} >> .altcha[data-state="verified"]').wait_for(
            state="attached", timeout=5000
        )

        assert btn.first.get_attribute("aria-disabled") == "false"


# =========================================================================
# No-appointment detection tests
# =========================================================================

class TestNoAppointmentDetection:
    """Test that the script correctly detects 'no appointment' responses."""

    def test_no_appointment_text_detected(self, mock_zms_no_appointment):
        """When no appointments exist, the result page should contain a
        recognised no-appointment phrase."""
        page = mock_zms_no_appointment
        shadow = "zms-appointment >> zms-appointment-wrapped"

        page.locator(f"{shadow} >> .altcha-checkbox input").click()
        page.locator(f'{shadow} >> .altcha[data-state="verified"]').wait_for(
            state="attached", timeout=5000
        )
        page.locator(f"{shadow} >> .m-button--primary").first.click()
        page.wait_for_timeout(500)

        paragraphs = page.locator(f"{shadow} >> p").all_text_contents()
        all_text = " ".join(paragraphs).lower()

        matched = any(p in all_text for p in tc.NO_APPOINTMENT_PHRASES)
        assert matched, f"Expected no-appointment phrase in: {all_text}"


# =========================================================================
# Full booking flow tests (direct Playwright, no terminchecker)
# =========================================================================

class TestBookingFlow:
    """End-to-end test of the booking wizard using the mock page."""

    def _solve_captcha_and_proceed(self, page):
        """Helper: solve ALTCHA and click Weiter to reach the result page."""
        shadow = "zms-appointment >> zms-appointment-wrapped"
        page.locator(f"{shadow} >> .altcha-checkbox input").click()
        page.locator(f'{shadow} >> .altcha[data-state="verified"]').wait_for(
            state="attached", timeout=5000
        )
        page.locator(f"{shadow} >> .m-button--primary").first.click()
        page.wait_for_timeout(300)

    def test_calendar_appears_when_appointments_available(self, mock_zms_booking):
        """After Weiter, a calendar with clickable dates should appear."""
        page = mock_zms_booking
        self._solve_captcha_and_proceed(page)

        shadow = "zms-appointment >> zms-appointment-wrapped"
        cal_btn = page.locator(f"{shadow} >> muc-calendar button")
        assert cal_btn.count() >= 1

    def test_timeslots_appear_after_date_selection(self, mock_zms_booking):
        """Clicking a calendar date should show timeslot buttons."""
        page = mock_zms_booking
        self._solve_captcha_and_proceed(page)

        shadow = "zms-appointment >> zms-appointment-wrapped"
        page.locator(f"{shadow} >> muc-calendar button").first.click()
        page.wait_for_timeout(300)

        timeslots = page.locator(f"{shadow} >> .timeslot button")
        assert timeslots.count() == 3

    def test_contact_form_appears_after_timeslot(self, mock_zms_booking):
        """Selecting a timeslot and clicking Weiter should show the form."""
        page = mock_zms_booking
        self._solve_captcha_and_proceed(page)

        shadow = "zms-appointment >> zms-appointment-wrapped"
        page.locator(f"{shadow} >> muc-calendar button").first.click()
        page.wait_for_timeout(200)
        page.locator(f"{shadow} >> .timeslot button").first.click()
        page.wait_for_timeout(200)
        page.locator(
            f'{shadow} >> .m-button--primary:not([aria-disabled="true"])'
        ).first.click()
        page.wait_for_timeout(200)

        fn_input = page.locator(f"{shadow} >> input[name=firstName]")
        assert fn_input.count() == 1

    def test_form_fill_works(self, mock_zms_booking):
        """Playwright's fill() should work on shadow DOM form inputs."""
        page = mock_zms_booking
        self._solve_captcha_and_proceed(page)

        shadow = "zms-appointment >> zms-appointment-wrapped"
        page.locator(f"{shadow} >> muc-calendar button").first.click()
        page.wait_for_timeout(200)
        page.locator(f"{shadow} >> .timeslot button").first.click()
        page.wait_for_timeout(200)
        page.locator(
            f'{shadow} >> .m-button--primary:not([aria-disabled="true"])'
        ).first.click()
        page.wait_for_timeout(200)

        page.locator(f"{shadow} >> input[name=firstName]").fill("Test")
        page.locator(f"{shadow} >> input[name=lastName]").fill("User")
        page.locator(f"{shadow} >> input[name=mailAddress]").fill("test@example.com")

        assert page.locator(f"{shadow} >> input[name=firstName]").input_value() == "Test"
        assert page.locator(f"{shadow} >> input[name=lastName]").input_value() == "User"
        assert page.locator(f"{shadow} >> input[name=mailAddress]").input_value() == "test@example.com"

    def test_full_booking_to_confirmation(self, mock_zms_booking):
        """Walk through the entire booking flow to the confirmation page."""
        page = mock_zms_booking
        shadow = "zms-appointment >> zms-appointment-wrapped"

        self._solve_captcha_and_proceed(page)

        # Select date
        page.locator(f"{shadow} >> muc-calendar button").first.click()
        page.wait_for_timeout(200)

        # Select timeslot
        page.locator(f"{shadow} >> .timeslot button").first.click()
        page.wait_for_timeout(200)

        # Click Weiter to form
        page.locator(
            f'{shadow} >> .m-button--primary:not([aria-disabled="true"])'
        ).first.click()
        page.wait_for_timeout(200)

        # Fill form and click Weiter
        page.locator(f"{shadow} >> input[name=firstName]").fill("Test")
        page.locator(f"{shadow} >> input[name=lastName]").fill("User")
        page.locator(f"{shadow} >> input[name=mailAddress]").fill("test@example.com")
        page.locator(
            f'{shadow} >> .m-button--primary:not([aria-disabled="true"])'
        ).first.click()
        page.wait_for_timeout(200)

        # Check checkboxes using the while-loop pattern (same as production code)
        while True:
            unchecked = page.locator(
                f"{shadow} >> input[type=checkbox]:not(:checked)"
            )
            if unchecked.count() == 0:
                break
            unchecked.first.check(force=True)

        # Click "Termin buchen"
        book_btn = page.locator(f"{shadow} >> .m-button--primary").filter(
            has_text="buchen"
        )
        book_btn.first.click()
        page.wait_for_timeout(300)

        confirmation = page.locator(f"{shadow} >> h3").text_content()
        assert "gebucht" in confirmation.lower()


# =========================================================================
# check_appointment() integration tests
# =========================================================================

def _fast_random(a, b=None):
    """Return near-zero delay for tests."""
    return 0.01


class TestCheckAppointment:
    """Test the check_appointment function against the mock page.

    These tests monkeypatch TARGET_URL to a local mock HTML file so
    check_appointment() navigates to the mock instead of the real site.
    Random delays are minimized to keep tests fast.
    """

    def test_returns_false_when_no_appointments(
        self, mock_url_no_appointment, env_setup, monkeypatch
    ):
        """check_appointment should return False when no slots available."""
        page, url = mock_url_no_appointment
        monkeypatch.setattr(tc, "LOGS_DIR", str(env_setup))
        monkeypatch.setattr(tc, "TARGET_URL", url)
        monkeypatch.setattr("random.uniform", _fast_random)

        with patch.object(tc, "_send_email"):
            result = tc.check_appointment(page, attempt_num=1)
        assert result is False

    def test_returns_true_when_booking_succeeds(
        self, mock_url_booking, env_setup, monkeypatch
    ):
        """check_appointment should return True after a successful booking."""
        page, url = mock_url_booking
        monkeypatch.setattr(tc, "LOGS_DIR", str(env_setup))
        monkeypatch.setattr(tc, "TARGET_URL", url)
        monkeypatch.setattr("random.uniform", _fast_random)

        with patch.object(tc, "_send_email"):
            result = tc.check_appointment(page, attempt_num=1)
        assert result is True

    def test_screenshots_created_on_no_appointment(
        self, mock_url_no_appointment, env_setup, monkeypatch
    ):
        """Screenshots should be saved during a no-appointment check."""
        page, url = mock_url_no_appointment
        monkeypatch.setattr(tc, "LOGS_DIR", str(env_setup))
        monkeypatch.setattr(tc, "TARGET_URL", url)
        monkeypatch.setattr("random.uniform", _fast_random)

        with patch.object(tc, "_send_email"):
            tc.check_appointment(page, attempt_num=1)

        screenshots = list(env_setup.glob("*.png"))
        assert len(screenshots) >= 2, f"Expected >=2 screenshots, found: {[s.name for s in screenshots]}"

    def test_screenshots_created_on_booking(
        self, mock_url_booking, env_setup, monkeypatch
    ):
        """The full booking flow should create screenshots at each step."""
        page, url = mock_url_booking
        monkeypatch.setattr(tc, "LOGS_DIR", str(env_setup))
        monkeypatch.setattr(tc, "TARGET_URL", url)
        monkeypatch.setattr("random.uniform", _fast_random)

        with patch.object(tc, "_send_email"):
            tc.check_appointment(page, attempt_num=1)

        screenshots = list(env_setup.glob("*.png"))
        assert len(screenshots) >= 5, f"Expected >=5 screenshots, found: {[s.name for s in screenshots]}"

    def test_email_sent_on_booking(
        self, mock_url_booking, env_setup, monkeypatch
    ):
        """_send_email should be called with booked=True on success."""
        page, url = mock_url_booking
        monkeypatch.setattr(tc, "LOGS_DIR", str(env_setup))
        monkeypatch.setattr(tc, "TARGET_URL", url)
        monkeypatch.setattr("random.uniform", _fast_random)

        with patch.object(tc, "_send_email") as mock_email:
            tc.check_appointment(page, attempt_num=1)

        mock_email.assert_called_once()
        call_kwargs = mock_email.call_args
        assert call_kwargs[1]["booked"] is True
        assert "Name" in call_kwargs[1]["booking_details"]


# =========================================================================
# Helper function tests
# =========================================================================

class TestHelpers:
    """Test utility functions from terminchecker."""

    def test_cfg_reads_env_var(self, monkeypatch):
        """cfg() should return an environment variable value."""
        monkeypatch.setenv("TEST_KEY_ABC", "hello")
        assert tc.cfg("TEST_KEY_ABC") == "hello"

    def test_cfg_returns_default(self):
        """cfg() should return the default when key is not set."""
        assert tc.cfg("NONEXISTENT_KEY_XYZ", "fallback") == "fallback"

    def test_cfg_env_var_takes_precedence(self, monkeypatch):
        """Environment variables should take precedence over defaults."""
        monkeypatch.setenv("MY_KEY", "from_env")
        assert tc.cfg("MY_KEY", "from_default") == "from_env"

    def test_fill_field_fills_first_matching_selector(self, mock_zms_booking):
        """_fill_field should fill the first matching input."""
        page = mock_zms_booking
        shadow = "zms-appointment >> zms-appointment-wrapped"

        # Navigate to the form
        page.locator(f"{shadow} >> .altcha-checkbox input").click()
        page.locator(f'{shadow} >> .altcha[data-state="verified"]').wait_for(
            state="attached", timeout=5000
        )
        page.locator(f"{shadow} >> .m-button--primary").first.click()
        page.wait_for_timeout(300)
        page.locator(f"{shadow} >> muc-calendar button").first.click()
        page.wait_for_timeout(200)
        page.locator(f"{shadow} >> .timeslot button").first.click()
        page.wait_for_timeout(200)
        page.locator(
            f'{shadow} >> .m-button--primary:not([aria-disabled="true"])'
        ).first.click()
        page.wait_for_timeout(200)

        result = tc._fill_field(page, ['[name=firstName]'], "Alice")
        assert result is True
        val = page.locator(f"{shadow} >> input[name=firstName]").input_value()
        assert val == "Alice"

    def test_fill_field_returns_false_for_missing_selector(self, mock_zms_page):
        """_fill_field should return False when no selector matches."""
        result = tc._fill_field(mock_zms_page, ['[name=nonexistent]'], "value")
        assert result is False

    def test_screenshot_creates_file(self, mock_zms_page, tmp_path):
        """_screenshot should create a PNG file."""
        path = str(tmp_path / "test_screenshot.png")
        tc._screenshot(mock_zms_page, path)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0


# =========================================================================
# Email construction tests
# =========================================================================

class TestEmailConstruction:
    """Test email HTML generation (without actually sending)."""

    def test_booking_email_escapes_html(self, monkeypatch):
        """HTML special chars in booking details should be escaped."""
        monkeypatch.setenv("SENDER_EMAIL", "s@example.com")
        monkeypatch.setenv("RECEIVER_EMAIL", "r@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "fake")

        with patch("smtplib.SMTP") as mock_smtp:
            mock_srv = mock_smtp.return_value.__enter__.return_value
            tc._send_email(
                booked=True,
                booking_details={"Name": "<script>alert(1)</script>"},
            )

        call_args = mock_srv.sendmail.call_args
        email_body = call_args[0][2]
        assert "&lt;script&gt;" in email_body
        assert "<script>alert(1)</script>" not in email_body

    def test_availability_email_sent_without_booking_details(self, monkeypatch):
        """An availability email should be sent when booked=False."""
        monkeypatch.setenv("SENDER_EMAIL", "s@example.com")
        monkeypatch.setenv("RECEIVER_EMAIL", "r@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "fake")

        with patch("smtplib.SMTP") as mock_smtp:
            mock_srv = mock_smtp.return_value.__enter__.return_value
            tc._send_email(booked=False)

        call_args = mock_srv.sendmail.call_args
        email_body = call_args[0][2]
        assert "may be available" in email_body.lower()

    def test_email_skipped_when_no_password(self, monkeypatch, capsys):
        """_send_email should log a failure when SMTP_PASSWORD is missing."""
        monkeypatch.setenv("SENDER_EMAIL", "s@example.com")
        monkeypatch.setenv("RECEIVER_EMAIL", "r@example.com")
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)

        tc._send_email()

        captured = capsys.readouterr()
        assert "SMTP_PASSWORD" in captured.out
