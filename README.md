# A to Z Shift Grabber

Autonomous shift pickup tool for Amazon A to Z. Monitors the "Find Shifts" page daily at noon and automatically grabs shifts that match your preferred time windows.

## How It Works

1. A Windows Scheduled Task fires the script at **11:58 AM** daily
2. The script launches a headless Chromium browser with your saved A to Z session
3. It navigates to the Find Shifts page for the date **7 days from today** (matching Amazon's shift release cadence)
4. At exactly **12:00 PM**, it begins polling for new shifts every 2 seconds
5. When shifts appear that match your configured time windows, it clicks **"Add shift"** instantly
6. You get a **Windows toast notification** with what was grabbed
7. Full activity is logged to `atoz_grabber.log`

## Quick Start

### 1. Setup
```
setup.bat
```
Installs Python dependencies (Playwright, winotify) and the Chromium browser.

### 2. Login
```
python atoz_grabber.py login
```
Opens a visible browser window. Log in to A to Z normally. Your session cookies are saved to `browser_session/` for future automated runs.

### 3. Test
```
python atoz_grabber.py test --headed
```
Runs against today's date with a visible browser so you can verify it's reading shifts correctly. No waiting for noon.

### 4. Install Daily Task
```
install_task.bat
```
Creates a Windows Scheduled Task that runs the grabber daily at 11:58 AM.

## Commands

| Command | Description |
|---------|-------------|
| `python atoz_grabber.py run` | Normal run: wait for noon, grab shifts for next week |
| `python atoz_grabber.py run --now` | Run immediately without waiting for noon |
| `python atoz_grabber.py run --date 2026-03-19` | Target a specific date |
| `python atoz_grabber.py run --headed` | Run with visible browser (debugging) |
| `python atoz_grabber.py login` | Interactive login to save session |
| `python atoz_grabber.py test` | Test run on today's date, no waiting |
| `python atoz_grabber.py test --headed` | Test with visible browser |
| `python atoz_grabber.py blackout list` | Show configured blackout days |
| `python atoz_grabber.py blackout add Saturday` | Block a day of the week |
| `python atoz_grabber.py blackout add 2026-03-25` | Block a specific date |
| `python atoz_grabber.py blackout remove Saturday` | Remove a blackout |

## Configuration

Edit `config.json` to customize behavior:

### Preferred Shifts
```json
"preferred_shifts": [
    {
        "label": "Early Morning",
        "start_time": "3:15 AM",
        "end_time": "8:00 AM"
    },
    {
        "label": "Mid Morning",
        "start_time": "8:45 AM",
        "end_time": "10:45 AM"
    }
]
```

### Time Matching
- `"exact"` — Shift start AND end times must exactly match a preferred window (default)
- `"start_within"` — Shift start time just needs to fall within a preferred window

### Blackout Days
```json
"blackout_days": ["Saturday", "Sunday", "2026-04-01"]
```
Supports day names (applies every week) and specific dates.

### Job Types
```json
"job_types": ["Associate TDR", "Dispatch Assist", "RTS", "Sorting"]
```
Only grab shifts matching these job types. Empty list = grab any type.

### Polling
```json
"polling": {
    "start_minutes_before_noon": 2,
    "poll_interval_seconds": 2,
    "max_poll_duration_minutes": 10,
    "page_load_wait_seconds": 3
}
```

### Browser
```json
"browser": {
    "headless": true,
    "user_data_dir": "./browser_session",
    "slow_mo_ms": 100
}
```
- `headless`: Run without visible browser window
- `slow_mo_ms`: Delay between browser actions (helps avoid detection; 100ms is a good balance)

## Session Management

Your A to Z login session is stored in the `browser_session/` directory. This persists between runs so you don't have to log in every time.

**If the session expires** (typically every few days), the script will:
1. Detect the expired session
2. Open a visible browser window
3. Send you a notification asking you to log in
4. Wait up to 5 minutes for you to complete login
5. Save the new session and continue

To proactively refresh your session:
```
python atoz_grabber.py login
```

## Logging

All activity is logged to `atoz_grabber.log` with timestamps. Logs rotate automatically (max 10MB, keeps 3 backups).

Check recent activity:
```
type atoz_grabber.log
```

## Troubleshooting

**"Session expired" errors:**
Run `python atoz_grabber.py login` to re-authenticate.

**No shifts being grabbed:**
1. Run `python atoz_grabber.py test --headed` to see what the page looks like
2. Check that your preferred shift times in `config.json` exactly match what appears on the page
3. Check `atoz_grabber.log` for detailed matching output

**Task Scheduler not running:**
1. Check task status: `schtasks /Query /TN "AtoZ_ShiftGrabber" /V`
2. Ensure your PC is awake at 11:58 AM (set power options accordingly)
3. Re-run `install_task.bat` as Administrator

**Browser automation detected:**
Increase `slow_mo_ms` in config (try 200-500ms). Amazon may detect automated browsers.

## Requirements

- Windows 10/11
- Python 3.10+
- Internet connection at noon daily
- PC must be awake/unlocked at 11:58 AM

## Important Disclaimer

This tool automates interactions with Amazon's internal A to Z system. Use at your own risk. Automating against employer systems may violate acceptable use policies. The author assumes no responsibility for any consequences of using this tool.

## Files

```
atoz-grabber/
├── atoz_grabber.py      # Main script (this is the brain)
├── config.json          # Your preferences and settings
├── setup.bat            # One-time dependency installer
├── install_task.bat     # Task Scheduler setup
├── README.md            # This file
├── atoz_grabber.log     # Activity log (created on first run)
└── browser_session/     # Saved login session (created on first login)
```
