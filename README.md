# Government Appointment Checker & Auto-Booker

A Python script that automatically monitors a government appointment booking page (using the ZMS appointment widget) and **books the first available slot** the moment one appears — no human interaction needed.

Currently configured for the Munich Auslaenderbehoerde (immigration office), but adaptable to any site using the same ZMS widget.

## How it works

The target site uses a web widget with nested shadow DOMs and an ALTCHA proof-of-work captcha. This script:

### Checking for availability (Steps 1-5)

1. Launches a headless Chrome browser.
2. Navigates to the appointment booking page.
3. Traverses two layers of shadow DOM to reach the booking widget.
4. Clicks the ALTCHA captcha checkbox and waits for the proof-of-work computation to complete (no human needed).
5. Clicks the "Weiter" (Next) button and reads the result page.

### Auto-booking when slots are found (Steps 6-10)

6. Selects the first available date on the calendar.
7. Selects the first available timeslot.
8. Clicks "Weiter" to proceed to the contact form.
9. Fills in first name, last name, and email address (guest booking — no login required).
10. Accepts the privacy policy and electronic communication checkboxes, then clicks "Termin buchen" (Book Appointment).

If no appointments are available, the script repeats every 45-90 seconds (randomised) for up to 30 minutes. It exits immediately after a successful booking.

Every attempt is logged with timestamps and full-page screenshots at each step so you can review exactly what happened.

## Project structure

```
munich-termin-checker/
├── terminchecker.py   # Main script
├── .env.example       # Example configuration (copy to .env)
├── .env               # Your local config (not committed to git)
├── README.md          # This file
└── logs/              # Created automatically at runtime
    ├── run_20260209_143000.log                 # Full log for the run
    ├── attempt_001_altcha_verified.png         # After captcha solved
    ├── attempt_001_result_no_appointment.png   # No slots available
    ├── ...
    ├── attempt_015_availability_detected.png   # Slots found!
    ├── attempt_015_step6_calendar.png          # Calendar date selected
    ├── attempt_015_step7_timeslot.png          # Timeslot selected
    ├── attempt_015_step8_contact_form.png      # Contact form page
    ├── attempt_015_step9_form_filled.png       # Form filled in
    ├── attempt_015_step10_summary.png          # Summary before booking
    └── attempt_015_step10_booked.png           # Confirmation page
```

## Prerequisites

- **Python 3.8+**
- **Email account** with SMTP access (e.g., a [Gmail App Password](https://myaccount.google.com/apppasswords))

## Installation

```bash
# Clone the project
git clone https://github.com/anveshg/munich-termin-checker.git
cd munich-termin-checker

# Install dependencies
pip install -r requirements.txt

# Install Playwright's bundled Chromium browser
playwright install chromium
```

## Configuration

Copy the example config and fill in your values:

```bash
cp .env.example .env
```

| Key              | Required | Description                                      |
|------------------|----------|--------------------------------------------------|
| `SMTP_PASSWORD`  | Yes      | SMTP password (e.g., Gmail App Password)         |
| `SENDER_EMAIL`   | Yes      | Email address to send notifications from         |
| `RECEIVER_EMAIL` | Yes      | Email address to notify (also used in the form)  |
| `SMTP_HOST`      | No       | SMTP server hostname (default: `smtp.gmail.com`) |
| `SMTP_PORT`      | No       | SMTP server port (default: `587`)                |
| `FIRST_NAME`     | No*      | First name for the guest booking form            |
| `LAST_NAME`      | No*      | Last name for the guest booking form             |

\* If `FIRST_NAME` and `LAST_NAME` are not set, the script will still detect available appointments and send a notification email, but will **not** auto-book. Set both to enable auto-booking.

Environment variables override `.env` values.

## Usage

### Manual run

```bash
python terminchecker.py
```

The script runs for 30 minutes, checking every 45-90 seconds. It exits early after a successful booking.

### Run as a cron job

Open your crontab:

```bash
crontab -e
```

Add a line to run the script (e.g. every 35 minutes, so runs don't overlap):

```
*/35 * * * * /usr/bin/python3 /path/to/terminchecker.py >> /tmp/terminchecker_cron.log 2>&1
```

> **macOS note:** You may need to grant Full Disk Access to `/usr/sbin/cron` under System Settings > Privacy & Security.

## Tuning

The following constants at the top of `terminchecker.py` can be adjusted:

| Constant             | Default | Description                               |
|----------------------|---------|-------------------------------------------|
| `CHECK_INTERVAL_MIN` | 45      | Minimum seconds between checks            |
| `CHECK_INTERVAL_MAX` | 90      | Maximum seconds between checks            |
| `MAX_DURATION_S`     | 1800    | Total run time in seconds (30 min)        |

## Adapting to another site

To use this script with a different ZMS-based appointment page:

1. Change `TARGET_URL` to point at your target booking page.
2. Update `NO_APPOINTMENT_PHRASES` with the phrases your site shows when no slots are available.
3. If the shadow DOM structure differs, adjust the `SHADOW` prefix and the CSS selectors.

## License

MIT
