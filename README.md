# Fjord
<img width="1912" height="1199" alt="image" src="https://github.com/user-attachments/assets/70846741-ff09-4de3-b6ba-29c8cb2e90be" />

A small, dark browser I built in Python. It runs on PyQt6 and QtWebEngine, so pages are rendered by Chromium, but everything around the page (tabs, sidebar, start page, settings) is my own.

I wanted a browser that stays out of the way, looks calm, and doesn't eat all my RAM. It's one file, `fjord.py`.

## Getting it

**Windows exe:** grab `fjord.exe` from the [Releases page](https://github.com/gargoyle-coder/fjord_browser/releases). Windows may warn you the first time because the app isn't signed. Click More info, then Run anyway.

**From source:** you need Python 3 and two packages:

```
pip install PyQt6 PyQt6-WebEngine
python fjord.py
```

For extensions you need PyQt6 6.10 or newer. Older versions still run Fjord, just without extension support.

You can also pass URLs: `python fjord.py example.com another.com`

The first time you open Fjord it runs a short welcome tour. You can replay it from the ⋯ menu.

## What's in it

**Tabs**
- Vertical tabs in a sidebar by default. If you prefer a normal tab bar, switch to horizontal in settings.
- Tab groups with colours. Drag tabs in and out of them.
- Essentials: sites you always want a click away, kept at the top of the sidebar.
- Split view, with 2 to 4 tabs side by side.
- Reopen closed tabs with Ctrl+Shift+T. Your tabs come back when you reopen the browser.

**Blocking ads and trackers**
Fjord has its own filter engine that reads uBlock Origin / EasyList style lists (uBlock filters, EasyList, EasyPrivacy, a malware list and a few others). It's on by default. You can pause it per site, and update the lists from settings.

It can't run uBlock's scriptlet rules, so it won't catch everything uBlock Origin does. It handles the bulk of it, and YouTube gets some extra handling.

**Importing from other browsers**
Fjord finds Chrome, Edge, Brave, Vivaldi, Chromium, Opera, Opera GX, Arc, Firefox, LibreWolf, Waterfox, Zen and Safari profiles on your computer and brings over bookmarks and history. You can also import a bookmarks `.html` file. It's part of the welcome tour, or find it at ⋯ > Import from another browser.

**Site time budgets**
Give a site a daily limit, like 30 minutes for YouTube. Fjord counts the time the site is in front of you, plus any tab playing audio. At the limit it dims the page and pauses media. You can take five more minutes, ignore the limit for today, or close the tab. Counts reset at midnight and stay on your computer. Find it at ⋯ > Privacy > Site time budgets.

**Extensions**
Fjord runs Chrome extensions as they are, and converts Firefox add-ons and Safari web extensions when you add them. Click the puzzle piece in the toolbar, then paste a Chrome Web Store or Firefox Add-ons link, or pick a `.crx`, `.xpi`, `.zip` or an unpacked folder. You'll see what the extension can access before it installs.

Some Firefox and Safari extensions use features Chromium doesn't have, so don't expect every single one to work. Extensions that talk to apps on your computer (native messaging) aren't supported.

**Toolbar**
Right-click the toolbar and pick Customize. Drag buttons to rearrange them, drag them off to remove them, and add separators or spaces. Reset puts it all back. The address bar and menu button always stay.

**Sidebar extras**
- A media player that shows up when a page is playing audio or video. It has a seek bar, a visualizer, and it picks up colours from the album art.
- Scratchpad (Ctrl+Shift+S): drag images, files, text or links toward the window and it pops open to catch them. Things stay there until you need them again. If the pop-up gets in your way, turn it off in the ⋯ menu.
- Sticky notes: there's a small note button in the bottom right of every site. Notes are saved per site and are still there next time.

**Looks**
- Three interface styles: Default, macOS (frosted glass, smooth motion, Mac window buttons) and Windows (a Windows 11 look and Windows buttons). Switch in settings or from the ⋯ menu.
- Two sliders for how bright and how transparent the glass surfaces are.
- New tab page with a custom greeting and a background of your choice: a colour, a gradient, an image, or a video.
- Accent colour and font are in settings.

**Performance**
Three speed modes in settings:
- **Eco** puts background tabs to sleep quickly and keeps the cache small.
- **Normal** is the default.
- **Turbo** never sleeps tabs and uses as much as it likes.

Changing modes mostly applies right away, but some Chromium flags only take effect after a restart.

**Updates**
Fjord checks GitHub for a newer release when it starts. You can also check by hand at ⋯ > Check for updates. If there's a new version, it downloads it, checks it and swaps it in, then asks if you want to restart. Turn off the check on launch in the same menu.

**Other stuff**
- Search with Google, DuckDuckGo, Bing or Brave.
- Bookmarks, history, find in page, zoom, fullscreen, save page as PDF.
- Proxy support (SOCKS5, HTTP or HTTPS, see the note below).

## Shortcuts

| | |
|---|---|
| Ctrl+T | New tab |
| Ctrl+W | Close tab |
| Ctrl+Shift+T | Reopen closed tab |
| Ctrl+L | Focus the address bar |
| Ctrl+1 to 9 | Jump to tab |
| Ctrl+Tab | Cycle tabs |
| Ctrl+D | Bookmark page |
| Ctrl+Shift+O | Bookmarks |
| Ctrl+H | History |
| Ctrl+F | Find in page |
| Ctrl+B | Toggle sidebar |
| Ctrl+Shift+S | Scratchpad |
| Ctrl+P | Save as PDF |
| Ctrl+= / Ctrl+- / Ctrl+0 | Zoom in / out / reset |
| Alt+Left / Alt+Right | Back / forward |
| F11 | Fullscreen |

Drag the right edge of the sidebar to resize it. Double-click the edge to reset it. Right-click the media player for more options.

## A note on the proxy setting

Fjord can send its traffic through a proxy (SOCKS5, HTTP or HTTPS), which is handy if you have one from a VPN or privacy service. It is not a VPN. A browser can't create one on its own.

If your proxy needs a password, that password is saved in plain text in `settings.json`. Keep that in mind on a shared computer.

## Where your stuff is saved

Everything lives in `~/.fjord_browser/` (so `C:\Users\you\.fjord_browser` on Windows): settings, bookmarks, history, notes, scratchpad items, site time counts, backgrounds and cached icons. Delete the folder to reset Fjord completely.

## Making a Windows exe

Put `fjord.py` and `fjord.ico` in the same folder, then run this in a Command Prompt opened in that folder:

```
pip install pyinstaller
python -m PyInstaller --noconsole --onefile --icon=fjord.ico --add-data "fjord.ico;." fjord.py
```

The exe ends up in `dist\fjord.exe`. If the one-file build is slow to start or won't launch (QtWebEngine can be fussy), use `--onedir` instead of `--onefile`.

For self-update to work, attach the exe to your GitHub release and make sure its name ends in `.exe`. Bump `APP_VERSION` at the top of `fjord.py` so it matches the release tag.

## Known rough edges

- It's built and tested mostly on Windows. Linux and macOS should work but get less attention.
- Self-update only works for the Windows exe, or when running `fjord.py` directly.
- The idle check for site time budgets only works on Windows.
- Firefox and Safari extensions are converted on the fly, so a few won't work properly.
- No sync between devices.
- Fjord is a hobby project. It's built on Chromium so it gets what QtWebEngine gets, but don't count on it for anything high-stakes.
