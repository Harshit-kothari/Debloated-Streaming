# Debloated Streaming

A lean terminal app for browsing live sports, discovering JioHotstar live events, and watching with MPV or VLC. Login, discovery, and playback run from one project.

**Built on top of these projects, with credit to their original creators:**

- [livesports-tui](https://github.com/sonstellar969/livesports-tui) by [sonstellar969](https://github.com/sonstellar969) — the terminal sports browser and player workflow.
- [jio-hotstar-stream-proxy](https://github.com/arvind88765/jio-hotstar-stream-proxy) by [arvind88765](https://github.com/arvind88765) — the original JioHotstar relay and login foundation.

Their work made this project possible. See [CREDITS.md](CREDITS.md) for component attribution. The terminal browser's original MIT notice is preserved in [LICENSE.tui](LICENSE.tui).

## What it does

- Lists live and upcoming Fancode and SonyLiv events alongside live entries discovered directly from your Hotstar catalogue.
- Keeps live entries red and shows whether your Hotstar login has expired at the top.
- Supports phone/OTP login inside the terminal, pending OTP resumption, and resend cooldowns.
- Finds MPV and VLC through common installation locations, portable tools, or executable overrides.
- Relays nested HLS playlists, audio tracks, init fragments, and byte ranges through a local service.
- Fetches fresh Hotstar playback URLs when you select an event.

## Quick start

Install **Node.js 22+**, **Python 3.9+**, and either [MPV](https://mpv.io/installation/) or [VLC](https://www.videolan.org/vlc/).

```powershell
git clone https://github.com/Harshit-kothari/Debloated-Streaming.git
cd Debloated-Streaming
npm install
python -m pip install -r Requirements.txt
node index.js
```

Run `node index.js --doctor` to check your Python dependencies, players, local relay, and saved login.

Player binaries and account credentials are not bundled. A portable MPV installation can be placed at `tools/mpv/mpv.exe` on Windows. You can also point to an installed executable:

```powershell
$env:MPV_PATH = 'C:\Apps\mpv\mpv.exe'
$env:VLC_PATH = 'C:\Program Files\VideoLAN\VLC\vlc.exe'
$env:PYTHON_PATH = 'C:\Python313\python.exe'
```

Use only the executable path in each override. A project `.venv` is detected automatically. The Node launcher manages its Python service on a free loopback port.

## Controls

| Key | Action |
| --- | --- |
| Up / Down | Select an event or player |
| Enter | Choose a player and play the selected event |
| l | Log in to Hotstar, or resume a pending OTP |
| j | Paste a Hotstar live URL manually |
| r | Refresh events and login status; resend when inside the OTP prompt |
| Esc | Cancel a prompt or leave the browser |
| q / Ctrl+C | Quit |

Close the player window to return to browsing. Login and manual URL entry remain available if event feeds cannot be reached.

## Hotstar login and discovery

Press `l` and enter your Indian mobile number and OTP locally. A confirmed OTP request can be resumed after restarting the app; resends have a 60-second cooldown. The app checks the provider's response before displaying an OTP acknowledgment and shows provider errors when a request fails.

Hotstar live entries are read from Home/Sports pages, live trays, and their pagination. Press `r` after login to refresh them. Selecting an event checks your session and opens the chosen player; if login is required, the same flow resumes playback after OTP verification.

The catalogue reflects what Hotstar exposes to your account and region. Page/time limits and unavailable pages are reported as partial results. DRM content requires the official Hotstar player.

Login tokens, guest sessions, optional tunnel credentials, debug captures, dependencies, and player binaries are excluded from Git. Credentials stay in local session files and are not included in catalogue exports.

## Commands

```powershell
node index.js --doctor
node index.js --login
node index.js --list
node index.js --hotstar-live
node index.js --hotstar-live --json
node index.js --hotstar 'YOUR_HOTSTAR_LIVE_URL' --player mpv --lang eng
```

`--hotstar-live` prints discovered titles and official watch links. The JSON form adds metadata and partial-result warnings. Playback URLs are resolved on selection because signed media links expire.

The optional browser interface runs with `python hotstar_live.py` at `http://127.0.0.1:8080`. Set `HOTSTAR_PORT` to change that port. Public tunneling is optional and starts only when explicitly enabled. The terminal app does not require a tunnel.

`python login.py` provides a standalone login prompt. `python resolve_cli.py --resolve URL` serves a local playback URL until Ctrl+C. `auth_cli.py` also accepts JSON input for integrations.

## Development

```powershell
npm test
```

The tests cover login state and response validation, catalogue discovery and pagination, partial feed failures, player launch/cancellation, HLS rewriting, and the Node-to-Python bridge. They do not send OTPs or require real account playback.

## Additional acknowledgments

- [fancode-live-events](https://github.com/drmlive/fancode-live-events) and [sliv-live-events](https://github.com/drmlive/sliv-live-events) supply community event feeds.

Debloated Streaming adds the unified launcher, persistent OTP handling, direct Hotstar discovery, player discovery, corrected relay handling, and terminal status presentation on top of the credited foundations.
