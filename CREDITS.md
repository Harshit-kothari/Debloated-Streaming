# Credits

Debloated Streaming is built on top of the following projects. Credit for the original work belongs to their creators and contributors.

## livesports-tui

- Project: https://github.com/sonstellar969/livesports-tui
- Creator: [sonstellar969](https://github.com/sonstellar969)
- Upstream contributors: [sonstellar969](https://github.com/sonstellar969) and [praveenmutyala2006](https://github.com/praveenmutyala2006), as listed in the [original contributor history](https://github.com/sonstellar969/livesports-tui/graphs/contributors).
- Contribution: the original React/Ink terminal sports browser, Fancode/SonyLiv event workflow, poster previews, and MPV/VLC player selection.
- Original copyright notice: Copyright (c) 2026 Lakshman Reddy.
- The original MIT notice is retained in [LICENSE.tui](LICENSE.tui).
- Original commits and authorship are preserved as part of this project's foundation history.

## jio-hotstar-stream-proxy

- Project: https://github.com/arvind88765/jio-hotstar-stream-proxy
- Creator: [arvind88765](https://github.com/arvind88765), credited in the original source as Rvind.
- Contribution: the original Python/Flask JioHotstar relay, login foundation, client header helpers, and browser playback interface. Its Git history and source attribution are preserved.

## Additional references and data

- [jiohotstar-downloader](https://github.com/arvind88765/jiohotstar-downloader) by arvind88765 was consulted for login behavior.
- [fancode-live-events](https://github.com/drmlive/fancode-live-events) and [sliv-live-events](https://github.com/drmlive/sliv-live-events) by drmlive provide community event data.
- MPV, VLC, and the Node/Python dependencies retain their own authorship and licensing. Player binaries are installed separately.

## Changes in Debloated Streaming

This project combines those foundations into one launcher and adds persistent OTP sessions, validated login responses, automatic Hotstar live discovery, robust local player discovery, signed/nested HLS URL handling, regression tests, and explicit live/login status in the terminal.

Attribution to these projects does not imply that their maintainers endorse or maintain Debloated Streaming. The preserved component notices remain applicable to their respective source.
