# CLAUDE.md

## Project overview

Automated government appointment checker & auto-booker for ZMS-based booking pages. Uses Playwright for browser automation with native shadow DOM piercing. Currently configured for the Munich Auslaenderbehoerde (immigration office). Notifications are **terminal-only** — there is no email/SMTP integration.

## Tech stack

- **Python 3.10+** (rye-managed)
- **Playwright** (sync API) — browser automation, shadow DOM piercing via `>>` selectors
- **python-dotenv** — config loading from `.env`
- **pytest** — integration tests with mock HTML that replicates the ZMS shadow DOM

## Key commands

```bash
rye sync                                   # Install dependencies
rye run playwright install chromium        # Install browser (first time)
rye run pytest tests/ -v                   # Run integration tests
rye run python terminchecker.py            # Standard run (30 min, 45-90s interval)
rye run python terminchecker.py --burst    # Short run (3 min, 10-15s interval) — pair with cron
```

## Project structure

- `terminchecker.py` — Single-file script containing all logic
- `tests/conftest.py` — Mock HTML page replicating the ZMS shadow DOM structure, fixtures
- `tests/test_integration.py` — 22 integration tests
- `.github/workflows/ci.yml` — CI runs lint + tests on Python 3.10 and 3.12
- `.env.example` — Template for config values

## Architecture notes

- The ZMS widget uses **two nested shadow DOMs**: `zms-appointment >> zms-appointment-wrapped`. The `SHADOW` constant holds this prefix. All element access uses `_shadow(page, selector)` which prepends it.
- The booking flow is a 10-step wizard (steps 1-5 check availability, steps 6-10 auto-book). Each step is documented in `check_appointment()`.
- `muc-calendar` may or may not have its own shadow root — the code tries both `>>` (shadow) and direct selectors as fallback.
- Config precedence: env vars > `.env` file > defaults. All config keys are in `.env.example`.

## Munich-specific gotchas (load-bearing)

- **Usercentrics cookie banner** (`#usercentrics-root`) is injected on every page load and intercepts pointer events over the whole viewport — any click (including the ALTCHA checkbox) fails with "intercepts pointer events" until it's dismissed. `_dismiss_cookie_banner()` removes the root element and installs a MutationObserver to kill re-injections. This must run before Step 1.
- **Headless Chromium throttles Web Workers**, which causes the ALTCHA proof-of-work worker to stall in the `verifying` state indefinitely. The browser must be launched with these flags:
  - `--disable-background-timer-throttling`
  - `--disable-backgrounding-occluded-windows`
  - `--disable-renderer-backgrounding`
  - `--disable-features=CalculateNativeWinOcclusion`
- **ALTCHA PoW timing**: cold start takes ~80–100s, warm (recent runs) ~25–35s. Timeout is set to 120s.
- The benign `www48.muenchen.de/wrapper.js` CORS error fires on every attempt but does not affect the flow — it's filtered via `_BENIGN_ERROR_SUBSTRINGS`.
- Munich releases new appointments at fixed times: Mon/Wed/Fri 07:30 CET and Tue/Thu 08:30 CET — schedule runs around these windows for the best odds.

## Scheduling around release windows

The highest-odds strategy is to run the checker in `--burst` mode (3 min, 10–15s interval) starting ~2 minutes before each release window so the first ALTCHA PoW is already warm when slots drop. Example crontab:

```cron
# Munich release times (CET): Mon/Wed/Fri 07:30, Tue/Thu 08:30
28 7 * * 1,3,5  cd /path/to/munich-appointment && rye run python terminchecker.py --burst
28 8 * * 2,4    cd /path/to/munich-appointment && rye run python terminchecker.py --burst
```

On macOS, `launchd` is the native equivalent. Without `--burst`, plain `python terminchecker.py` runs for 30 minutes with polite 45–90s intervals.

## Testing conventions

- Tests use a mock HTML page served via `file://` that builds the same shadow DOM structure as the real site.
- `TestCheckAppointment` tests monkeypatch `TARGET_URL` to the mock HTML and `random.uniform` to near-zero to skip delays.
- `_send_email` is mocked via `patch.object(tc, "_send_email")` in booking-flow tests — the function itself is a terminal-only stub.
- Browser is launched once per session (`scope="session"`), fresh context per test.

## Code conventions

- No personal data in code — all config comes from env vars / `.env`
- Form filling uses Playwright `fill()` — never string concatenation into JS
- Checkbox iteration uses a while loop (re-query after each check to avoid stale selectors)
