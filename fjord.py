#!/usr/bin/env python3
"""
Fjord Browser - a minimalist, dark, Nordic-inspired browser.

Install:  pip install PyQt6 PyQt6-WebEngine   (version 6.10 or newer if you want browser extensions)
Run:      python fjord.py [url ...]

Ctrl+T new tab · Ctrl+Shift+T reopen closed · Ctrl+W close · Ctrl+L address
Ctrl+D bookmark · Ctrl+Shift+O bookmarks · Ctrl+H history · Ctrl+F find
Ctrl+1..9 jump to tab · Ctrl+Tab cycle · Ctrl+B sidebar · Ctrl+P save as PDF
Ctrl+= / - / 0 zoom · Alt+←/→ back/forward · F11 fullscreen · ⋯ menu for more
Drag the sidebar's right edge to resize it (double-click resets) · right-click the media player for options
Scratchpad (Ctrl+Shift+S): drag images, files, text or links toward the window and it pops open to catch them; copy or save them again later
Sticky notes: click the little note button at the bottom right of any site to pin a note there (saved in ~/.fjord_browser/notes.json)
Extensions: the puzzle-piece button in the toolbar (or the ⋯ menu > Extensions) adds Chrome, Firefox and Safari extensions
Welcome tour: runs on first launch (import from another browser, accent colour, layout, speed, privacy, tools); replay it from ⋯ menu > Welcome tour…
Updates: ⋯ menu > Check for updates (also checks on launch; set GITHUB_REPO and APP_VERSION below)
Toolbar: right-click it (or ⋯ menu > Customize toolbar…), then drag buttons to rearrange, remove or add them
Interface style: ⋯ menu > View & appearance > Interface style (or right-click the toolbar, or Settings > Window):
    Default (Fjord as it is), macOS (Safari-like liquid glass + motion + macOS buttons) or Windows (Windows 11 look + Windows buttons)
"""
import base64
import colorsys
import datetime
import gc
import hashlib
import html
import io
import ipaddress
import json
import math
import mimetypes
import os
import posixpath
import plistlib
import re
import secrets
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlparse

# Speed modes. "sleep" = (freeze after s, discard after s) or None to keep background tabs awake.
# "flags" are Chromium switches, which only take effect at launch; the rest applies instantly.
SPEED_MODES = {
    "eco": {"label": "Eco", "desc": "Saves RAM and power", "needle": -55,
            "sleep": (15, 90), "cache_mb": 16, "trim_ticks": 4,
            "flags": ["--enable-low-end-device-mode", "--renderer-process-limit=2", "--num-raster-threads=1",
                      "--disable-features=SpareRendererForSitePerProcess"]},
    "normal": {"label": "Normal", "desc": "Balanced (default)", "needle": 0,
               "sleep": (30, 240), "cache_mb": 32, "trim_ticks": 8,
               "flags": ["--enable-low-end-device-mode", "--renderer-process-limit=4", "--num-raster-threads=2",
                         "--disable-features=SpareRendererForSitePerProcess"]},
    "turbo": {"label": "Turbo", "desc": "Uses all the resources it can", "needle": 55,
              "sleep": None, "cache_mb": 512, "trim_ticks": 0,
              "flags": ["--num-raster-threads=4", "--enable-gpu-rasterization", "--enable-zero-copy"]},
}


def _early_flags():
    """Chromium flags must be set before Qt starts, so the proxy and speed-mode settings are read from disk here."""
    try:
        st = json.loads((Path.home() / ".fjord_browser" / "settings.json").read_text())
    except Exception:
        st = {}
    mode = SPEED_MODES.get(st.get("speed_mode"), SPEED_MODES["normal"])
    flags = ["--force-dark-mode", "--enable-features=WebContentsForceDark", "--autoplay-policy=no-user-gesture-required",
             "--disable-background-networking", "--disable-component-update", "--disable-sync"]
    flags += mode["flags"]  # process limits, raster threads, low-end mode etc. for the chosen speed mode
    host, port = st.get("proxy_host"), st.get("proxy_port")
    if st.get("vpn") and host and port:
        flags.append("--proxy-server=%s://%s:%s" % (st.get("proxy_type", "socks5"), host, port))
        flags.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")  # no WebRTC IP leaks
    return " ".join(flags)


os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", _early_flags())

from PyQt6.QtCore import (QEasingCurve, QEvent, QPropertyAnimation, QRectF, QSize, QStringListModel, Qt,
                          QMimeData, QObject, QPoint, QPointF, QProcess, QProcessEnvironment, QTimer, QUrl, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import (QColor, QCursor, QDesktopServices, QFont, QFontDatabase, QFontMetrics, QIcon, QKeySequence,
                         QBrush, QConicalGradient, QDrag, QImage, QImageReader, QLinearGradient, QRadialGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QShortcut, QTextOption)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings,
                                   QWebEngineUrlRequestInfo, QWebEngineUrlRequestInterceptor)
from PyQt6.QtWebEngineWidgets import QWebEngineView
try:
    from PyQt6.QtWebEngineCore import QWebEngineExtensionManager
except Exception:  # extension support arrived in Qt WebEngine 6.10; older versions run Fjord without it
    QWebEngineExtensionManager = None
try:
    from PyQt6.QtSvg import QSvgRenderer
except Exception:  # QtSvg missing: fall back to downloaded logos / letter badges
    QSvgRenderer = None
from PyQt6.QtWidgets import (
    QApplication, QBoxLayout, QCompleter, QFileDialog, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QListView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QInputDialog, QMessageBox, QProgressBar, QPushButton, QSizePolicy,
    QScrollArea, QStackedWidget, QStyle, QAbstractButton, QColorDialog, QGraphicsEffect, QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QVBoxLayout, QWidget,
)

# ----- auto-update -----
# Bump APP_VERSION on every release so it matches the GitHub release tag (tag "v1.2.0" or "1.2.0" -> "1.2.0").
APP_VERSION = "0.3.0"
GITHUB_REPO = "gargoyle-coder/fjord_browser"      # <- change to "owner/repo" of your GitHub repository
UPDATE_ASSET = "fjord.py"                # name of the file attached to each release (falls back to the file at the release tag)

DATA_DIR = Path.home() / ".fjord_browser"
START_URL = QUrl("fjord://start")
ICON_DIR = DATA_DIR / "icons"
ENGINES = {
    "Google": "https://www.google.com/search",
    "DuckDuckGo": "https://duckduckgo.com/",
    "Bing": "https://www.bing.com/search",
    "Brave": "https://search.brave.com/search",
}

ENGINE_SHORT = {"Google": "Google", "DuckDuckGo": "DDG", "Bing": "Bing", "Brave": "Brave"}
FONT_PREFS = ["Inter", "Geist", "SF Pro Display", "Helvetica Neue", "Manrope", "DM Sans",
              "Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI", "Roboto", "Noto Sans"]
FONT_EXTRA = [
    "Poppins", "Montserrat", "Nunito", "Nunito Sans", "Outfit", "Lexend", "Sora", "IBM Plex Sans", "Work Sans",
    "Open Sans", "Ubuntu", "Space Grotesk", "Plus Jakarta Sans", "Figtree", "Albert Sans", "Urbanist", "Rubik",
    "Karla", "Mulish", "Lato", "Source Sans 3", "Source Sans Pro", "Raleway", "Quicksand", "Cabin", "Barlow",
    "Public Sans", "Red Hat Display", "Be Vietnam Pro", "Fira Sans", "Cantarell", "Oxygen", "Avenir Next",
    "Avenir", "Futura", "Gill Sans", "Optima", "Calibri", "Candara", "Trebuchet MS", "Tahoma", "Verdana",
    "Arial", "Helvetica", "Cascadia Code", "JetBrains Mono", "Fira Code", "IBM Plex Mono", "SF Mono", "Menlo",
    "Consolas", "Georgia", "Palatino Linotype", "Playfair Display", "Lora", "Merriweather",
]
CUR_FONT = {"family": "Segoe UI"}
BG_DIR = DATA_DIR / "backgrounds"
BG_COLORS = ["#0b141d", "#14294a", "#0f2a2e", "#1b2a3a", "#262626", "#3b2f1e", "#eef3f6", "#dcebf5"]
BG_GRADIENTS = [
    ("Aurora", "linear-gradient(135deg,#0a2540,#0f6b6b)"),
    ("Sunset", "linear-gradient(135deg,#3a1c4a,#a8435b 60%,#f19a5b)"),
    ("Ocean", "linear-gradient(160deg,#071a3a,#0e5a7a)"),
    ("Forest", "linear-gradient(150deg,#0b2a1e,#2f6b4f)"),
    ("Rose", "linear-gradient(135deg,#3b1830,#b24a7a)"),
    ("Midnight", "linear-gradient(135deg,#070d14,#1c2d44)"),
]
CUR_BG = {"css": None, "light": False}
DEFAULT_GREETING = "the web, calm and clear"
GREETING_MAX = 120
# Quotes for the new tab page ("Daily quote" mode). Add your own as (text, author) pairs.
QUOTES = [
    ("The journey of a thousand miles begins with a single step.", "Lao Tzu"),
    ("Nature does not hurry, yet everything is accomplished.", "Lao Tzu"),
    ("He who knows he has enough is rich.", "Lao Tzu"),
    ("The mountains are calling and I must go.", "John Muir"),
    ("Climb the mountains and get their good tidings.", "John Muir"),
    ("Heaven is under our feet as well as over our heads.", "Henry David Thoreau"),
    ("The question is not what you look at, but what you see.", "Henry David Thoreau"),
    ("Not till we are lost do we begin to find ourselves.", "Henry David Thoreau"),
    ("The earth laughs in flowers.", "Ralph Waldo Emerson"),
    ("Nothing great was ever achieved without enthusiasm.", "Ralph Waldo Emerson"),
    ("I lean and loafe at my ease observing a spear of summer grass.", "Walt Whitman"),
    ("It is not that we have a short time to live, but that we waste a lot of it.", "Seneca"),
    ("We suffer more often in imagination than in reality.", "Seneca"),
    ("Very little is needed to make a happy life.", "Marcus Aurelius"),
    ("No man is free who is not master of himself.", "Epictetus"),
    ("No man ever steps in the same river twice.", "Heraclitus"),
    ("Life can only be understood backwards; but it must be lived forwards.", "S\u00f8ren Kierkegaard"),
    ("The strongest man in the world is he who stands most alone.", "Henrik Ibsen"),
    ("Just living is not enough. One must have sunshine, freedom, and a little flower.", "Hans Christian Andersen"),
    ("The difficult is what takes a little time; the impossible is what takes a little longer.", "Fridtjof Nansen"),
    ("Victory awaits him who has everything in order.", "Roald Amundsen"),
    ("There is no such thing as bad weather, only bad clothing.", "Scandinavian proverb"),
    ("Lagom \u00e4r b\u00e4st: just the right amount is best.", "Swedish saying"),
    ("Well done is better than well said.", "Benjamin Franklin"),
    ("It does not matter how slowly you go as long as you do not stop.", "Confucius"),
    ("No act of kindness, no matter how small, is ever wasted.", "Aesop"),
    ("To travel hopefully is a better thing than to arrive.", "Robert Louis Stevenson"),
    ("Great things are not done by impulse, but by a series of small things brought together.", "Vincent van Gogh"),
    ("To live is the rarest thing in the world. Most people exist, that is all.", "Oscar Wilde"),
    ("There is no charm equal to tenderness of heart.", "Jane Austen"),
]
ACCENT_ALGO = 2  # bump when the colour sampler changes so saved accents are re-sampled
ACCENT = {"main": "#4fb0e8", "alt": "#7ef0d0"}
DEFAULT_ACCENT = dict(ACCENT)


def _rgb(hexcol):
    return int(hexcol[1:3], 16), int(hexcol[3:5], 16), int(hexcol[5:7], 16)


# ----- interface style -----
# "default" is Fjord's own look. "mac" is Safari-like: liquid-glass surfaces, pill shapes, extra motion, macOS traffic lights.
# "windows" is a flat Windows 11 (Fluent) look in neutral greys with the Windows caption buttons.
UI_MODES = (("default", "Default"), ("mac", "macOS"), ("windows", "Windows"))
UI_LABELS = {"default": "Default", "mac": "macOS  (Safari-like liquid glass)", "windows": "Windows  (Windows 11 look)"}
UI = {"mode": "default", "radius": 100, "bright": 100, "transp": 50}
# User-tunable look (Settings > Glass & corners): settings key -> (UI key, min, max, default)
TUNE = {"glass_bright": ("bright", 0, 200, 100), "glass_transp": ("transp", 0, 100, 50)}


def load_ui_tuning(st):
    """Read the corner-radius / glass-brightness / glass-transparency sliders from the settings dict."""
    st.pop("ui_radius", None)  # the corner-radius slider was removed; corners stay at the default
    for key, (name, lo, hi, dflt) in TUNE.items():
        try:
            val = int(st.get(key, dflt))
        except (TypeError, ValueError):
            val = dflt
        UI[name] = max(lo, min(hi, val))


def rr(r):
    """A hard-coded corner radius, scaled by the corner-radius slider."""
    return float(r) * UI["radius"] / 100.0


def glass_rgba(alpha):
    """A glass overlay colour. Brightness below 100% smokes the glass (darker tint), above 100% makes it stronger and whiter;
    transparency 0..100% runs from frosted (x2 opacity) through the original look (50%) to fully clear."""
    b = UI["bright"] / 100.0
    lvl = int(min(255, 255 * b))
    mul = max(1.0, b) * 2.0 * (100 - UI["transp"]) / 100.0
    return QColor(lvl, lvl, lvl, int(max(0, min(255, alpha * mul))))


_RADIUS_RE = re.compile(r"(border(?:-(?:top|bottom)-(?:left|right))?-radius)(\s*:\s*)([^;}\"']+)", re.I)
_WHITE_RE = re.compile(r"rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*(0?\.\d+)\s*\)")


def tune_css(css):
    """Apply the corner-radius and glass sliders to a stylesheet (Qt QSS or web CSS)."""
    m = UI["radius"] / 100.0
    if UI["radius"] != 100:
        def px(t):
            v = float(t.group(1))
            if v >= 99:  # "pill" values stay pills until the slider gets low
                v = 99.0 if m >= 0.5 else 32.0 * m
            else:
                v *= m
            return "%gpx" % round(v, 1)
        css = _RADIUS_RE.sub(lambda g: g.group(1) + g.group(2) + re.sub(r"(\d+(?:\.\d+)?)px", px, g.group(3)), css)
    if UI["bright"] != 100 or UI["transp"] != 50:
        b = UI["bright"] / 100.0
        lvl = int(min(255, 255 * b))
        mul = max(1.0, b) * 2.0 * (100 - UI["transp"]) / 100.0

        def white(g):
            a = float(g.group(1))
            if a > 0.3:  # stronger values are text colours, leave them readable
                return g.group(0)
            return "rgba(%d,%d,%d,%.3f)" % (lvl, lvl, lvl, min(1.0, a * mul))
        css = _WHITE_RE.sub(white, css)
    return css
PAGE_RADII = {"default": 16, "mac": 18, "windows": 8}
FRAME_MARGINS = {"default": 8, "mac": 10, "windows": 8}
# Windows 11 dark greys that replace Fjord's blue-tinted surfaces while the Windows style is on
WIN_SURFACES = {"#0b141d": "#1b1b1b", "#101b26": "#242424", "#172431": "#2c2c2c", "#1c2b3a": "#313131",
                "#132029": "#2b2b2b", "#243546": "#3a3a3a", "#1f2e3d": "#333333", "#2c4156": "#3d3d3d",
                "#2b3f54": "#454545", "#b5c6d4": "#c5c5c5", "#c3d3e0": "#d2d2d2", "#8ea3b4": "#9e9e9e",
                "#7d93a5": "#8c8c8c", "#3f5163": "#5c5c5c"}


def valid_ui_mode(m):
    return m if m in dict(UI_MODES) else "default"


def page_radius():
    return int(round(rr(PAGE_RADII[UI["mode"]])))


def frame_margin():
    return FRAME_MARGINS[UI["mode"]]


def shape_radius(base, rect):
    """Corner radius for a control: pill-shaped in macOS style, squarer in Windows style, unchanged otherwise."""
    half = min(rect.width(), rect.height()) / 2.0
    if UI["mode"] == "mac":
        return min(half, half * UI["radius"] / 100.0)
    if UI["mode"] == "windows":
        return min(half, rr(min(float(base), 5.0)))
    return min(half, rr(base))


SURFACE_HEX = ("#0b141d", "#101b26", "#172431", "#1c2b3a", "#132029", "#243546", "#1f2e3d", "#2c4156", "#2b3f54",
               "#c5e6fa", "#b5c6d4", "#c3d3e0", "#8ea3b4", "#7d93a5", "#3f5163")


def themed(css):
    # recolour Fjord's default glacial-blue/sea-foam accent, and tint the dark bar/sidebar surfaces with the accent's hue
    mapping = {"#4fb0e8": ACCENT["main"], "#7ef0d0": ACCENT["alt"]}
    if ACCENT["main"] != DEFAULT_ACCENT["main"]:
        hue = colorsys.rgb_to_hsv(*(c / 255.0 for c in _rgb(ACCENT["main"])))[0]
        for hx in SURFACE_HEX:
            _h, sat, val = colorsys.rgb_to_hsv(*(c / 255.0 for c in _rgb(hx)))
            mapping[hx] = "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(hue, sat, val))
    if UI["mode"] == "windows":
        mapping.update(WIN_SURFACES)
    css = re.sub(r"#[0-9a-fA-F]{6}\b", lambda m: mapping.get(m.group(0).lower(), m.group(0)), css)
    (r1, g1, b1), (r2, g2, b2) = _rgb(ACCENT["main"]), _rgb(ACCENT["alt"])
    for x, y in (("79,176,232", "%d,%d,%d" % (r1, g1, b1)), ("79, 176, 232", "%d, %d, %d" % (r1, g1, b1)),
                 ("126,240,208", "%d,%d,%d" % (r2, g2, b2)), ("126, 240, 208", "%d, %d, %d" % (r2, g2, b2))):
        css = css.replace(x, y)
    return tune_css(css)


def accent_color(alpha=255):
    c = QColor(ACCENT["main"])
    c.setAlpha(alpha)
    return c


def derive_accent(hexcol, min_sat=0.2):
    # turn any colour into a bright, vivid UI accent (plus a neighbouring hue); None for greys
    r, g, b = (c / 255.0 for c in _rgb(hexcol))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < min_sat:
        return None
    s, v = min(max(s, 0.45), 0.9), min(max(v, 0.8), 1.0)

    def to_hex(rgb):
        return "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in rgb)
    return to_hex(colorsys.hsv_to_rgb(h, s, v)), to_hex(colorsys.hsv_to_rgb((h + 0.12) % 1.0, s * 0.75, 0.95))
CUSTOM_FONTS = []


def load_custom_fonts():
    """Any .ttf/.otf dropped into ~/.fjord_browser/fonts is loaded at startup."""
    d = DATA_DIR / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    for f in sorted(d.iterdir()):
        if f.suffix.lower() in (".ttf", ".otf"):
            fid = QFontDatabase.addApplicationFont(str(f))
            if fid >= 0:
                CUSTOM_FONTS.extend(QFontDatabase.applicationFontFamilies(fid))


def installed_fonts():
    fams = set(QFontDatabase.families())
    out = []
    for f in CUSTOM_FONTS + FONT_PREFS + FONT_EXTRA:
        if f in fams and f not in out:
            out.append(f)
    return out


def pick_font(saved):
    inst = installed_fonts()
    if saved in inst:
        return saved
    for f in FONT_PREFS:
        if f in inst:
            return f
    return inst[0] if inst else "Segoe UI"


def apply_font(app, family):
    CUR_FONT["family"] = family
    f = QFont()
    f.setFamilies([family] + [x for x in FONT_PREFS if x != family])
    f.setWeight(QFont.Weight.Normal)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.2)
    app.setFont(f)
    app.setStyleSheet(themed(app_qss()))


def base_css():
    css = BASE_CSS.replace("FONT_STACK", f"'{CUR_FONT['family']}','Inter','Segoe UI',system-ui,sans-serif")
    if CUR_BG["css"]:
        css += "html,body{background:%s;background-attachment:fixed}" % CUR_BG["css"]
        if CUR_BG["light"]:
            css += "html,body{color:#12202c}"
    return themed(css)


def stop_anim(obj, prop):
    old = getattr(obj, "_anim_" + prop.decode(), None)
    if old is not None:
        try:
            old.finished.disconnect()
        except TypeError:
            pass
        try:
            old.stop()
            old.deleteLater()
        except RuntimeError:
            pass
        setattr(obj, "_anim_" + prop.decode(), None)


def animate(obj, prop, start, end, ms=220, curve=QEasingCurve.Type.OutCubic, done=None):
    """Smoothly animate a Qt property; a new animation on the same property replaces the old."""
    stop_anim(obj, prop)
    a = QPropertyAnimation(obj, prop, obj)
    a.setDuration(ms)
    a.setStartValue(start)
    a.setEndValue(end)
    a.setEasingCurve(curve)
    if done:
        a.finished.connect(done)
    setattr(obj, "_anim_" + prop.decode(), a)
    a.start()
    return a


def jload(name, default):
    try:
        return json.loads((DATA_DIR / name).read_text())
    except Exception:
        return default


def jsave(name, data):
    try:
        (DATA_DIR / name).write_text(json.dumps(data))
    except OSError:
        pass


def to_url(text, engine="Google"):
    t = text.strip()
    if not t:
        return None
    if "://" in t or t.startswith(("view-source:", "about:")):
        return QUrl(t)
    if t.startswith(("localhost", "127.0.0.1")):
        return QUrl("http://" + t)
    if " " not in t and "." in t:
        return QUrl("https://" + t)
    return QUrl(ENGINES[engine] + "?q=" + quote_plus(t))


# ---------- internal pages ----------
BASE_CSS = """
:root{color-scheme:dark}
html,body{margin:0;min-height:100%;font-family:FONT_STACK;-webkit-font-smoothing:antialiased;color:#e6eff6;
background:radial-gradient(60% 50% at 15% 10%,rgba(79,176,232,.20),transparent),
radial-gradient(50% 45% at 90% 90%,rgba(126,240,208,.14),transparent),#0b141d}
a{color:inherit;text-decoration:none}
"""


BG_SCRIPT = r"""
(function(){
var URL=__URL__, FILE=__FILE__, KIND=__KIND__, DONE=__DONE__, EVT=__EVT__;
function ping(q){try{fetch(EVT+'?'+q,{mode:'no-cors'});}catch(e){}}
function hex(n){n=Math.max(0,Math.min(255,Math.round(n)));return (n<16?'0':'')+n.toString(16);}
function pick(src){
  var c=document.createElement('canvas');c.width=80;c.height=45;
  var x=c.getContext('2d');x.drawImage(src,0,0,80,45);
  var d=x.getImageData(0,0,80,45).data,bk=[],i,k;
  for(k=0;k<24;k++)bk.push({c:0,r:0,g:0,b:0});
  var tr=0,tg=0,tb=0,n=0,col=0;
  for(i=0;i<d.length;i+=4){
    var r=d[i],g=d[i+1],b=d[i+2];
    if(d[i+3]<128)continue;
    var mx=Math.max(r,g,b),mn=Math.min(r,g,b),dl=mx-mn,v=mx/255,s=mx?dl/mx:0;
    tr+=r;tg+=g;tb+=b;n++;
    if(s<0.15||v<0.18)continue;
    var h=mx===r?((g-b)/dl+6)%6:(mx===g?(b-r)/dl+2:(r-g)/dl+4);
    k=Math.floor(h*4)%24;
    bk[k].c++;bk[k].r+=r;bk[k].g+=g;bk[k].b+=b;col++;
  }
  if(col<n*0.02)return n?[tr/n,tg/n,tb/n]:null;
  var best=0,bs=-1;
  for(k=0;k<24;k++){
    var sc=bk[k].c+0.5*(bk[(k+23)%24].c+bk[(k+1)%24].c);
    if(sc>bs){bs=sc;best=k;}
  }
  var R=0,G=0,B=0,C=0,j;
  for(j=-1;j<=1;j++){var q=bk[(best+j+24)%24];R+=q.r;G+=q.g;B+=q.b;C+=q.c;}
  return C?[R/C,G/C,B/C]:null;
}
function send(rgb){
  if(!rgb)return;
  ping('kind=accent&c='+encodeURIComponent('#'+hex(rgb[0])+hex(rgb[1])+hex(rgb[2]))+'&f='+encodeURIComponent(FILE));
}
if(KIND==='video'){
  var v=document.getElementById('bgv');
  if(v){
    v.muted=true;var pr=v.play();if(pr&&pr.catch)pr.catch(function(){});
    v.addEventListener('error',function(){ping('kind=videoerror');});
    if(!DONE){var fired=false;v.addEventListener('timeupdate',function(){
      if(fired||v.currentTime<1)return;fired=true;try{send(pick(v));}catch(e){}});}
  }
}else if(!DONE){
  var im=new Image();im.crossOrigin='anonymous';
  im.onload=function(){try{send(pick(im));}catch(e){}};im.src=URL;
}
})();
"""


def icon_uri(host):
    """Cached favicon of a site as a data: URI (empty string if we don't have one)."""
    if host and re.fullmatch(r"[\w.\-]+", host):
        ip = ICON_DIR / (host + ".png")
        if ip.exists():
            try:
                return "data:image/png;base64," + base64.b64encode(ip.read_bytes()).decode()
            except OSError:
                pass
    return ""


SEARCH_ICON = ('<svg class=mg viewBox="0 0 24 24" width=18 height=18 fill=none stroke=currentColor stroke-width=2 '
               'stroke-linecap=round stroke-linejoin=round><circle cx=11 cy=11 r=7></circle><path d="m20 20-3.5-3.5"></path></svg>')

# New-tab search box "focus mode": click it and the rest of the page blurs, the bar widens into a card with a site list.
SEARCH_CSS = """
main{position:relative;z-index:1}
.veil{position:fixed;inset:0;z-index:0;pointer-events:none;background:rgba(0,0,0,0);
-webkit-backdrop-filter:blur(0);backdrop-filter:blur(0);transition:background .35s ease,backdrop-filter .35s ease,-webkit-backdrop-filter .35s ease}
body.foc .veil{background:rgba(0,0,0,.42);-webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px);pointer-events:auto}
h1,.by,.g{transition:filter .35s ease,opacity .35s ease}
body.foc h1,body.foc .by,body.foc .g{filter:blur(12px);opacity:0!important;pointer-events:none}
.sb{position:relative;height:55px}
.card{position:absolute;top:0;left:0;width:100%;box-sizing:border-box;overflow:hidden;text-align:left;color:#e8f1f7;
background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.1);border-radius:999px;
transition:left .35s cubic-bezier(.2,.7,.2,1),width .35s cubic-bezier(.2,.7,.2,1),border-radius .35s,background .3s,border-color .3s,box-shadow .35s}
.card:focus-within{border-color:rgba(79,176,232,.7);background:rgba(255,255,255,.1);box-shadow:0 0 0 5px rgba(79,176,232,.12)}
body.foc .card{left:calc(50% - min(560px,46vw));width:min(1120px,92vw);border-radius:28px;background:rgba(24,24,24,.94);
border-color:rgba(255,255,255,.14);box-shadow:0 30px 80px rgba(0,0,0,.55)}
.sr{display:flex;align-items:center;height:53px;padding:0 24px;box-sizing:border-box}
.sr .mg{flex:none;width:0;margin:0;opacity:0;transition:width .35s,margin .35s,opacity .35s}
body.foc .sr .mg{width:18px;margin-right:14px;opacity:.55}
.sb .sr input,.sb .sr input:focus{flex:1;min-width:0;width:auto;padding:0;border:0;border-radius:0;background:none;box-shadow:none;text-align:left}
.sl{max-height:0;opacity:0;overflow:hidden;padding:0 8px;border-top:1px solid transparent;
transition:max-height .35s ease,opacity .25s ease,padding .35s ease,border-color .35s}
body.foc .sl{max-height:420px;opacity:1;padding:8px 8px 10px;border-top-color:rgba(255,255,255,.09)}
body.foc .sl:empty{padding:0;border-top-color:transparent}
.o{display:flex;align-items:center;gap:14px;padding:12px 16px;border-radius:10px;font-size:15px;transition:background .15s}
.o.on{background:rgba(255,255,255,.08)}
.o i{flex:none;font-style:normal;width:20px;height:20px;border-radius:6px;display:grid;place-items:center;font-size:12px;
background:linear-gradient(135deg,rgba(79,176,232,.5),rgba(126,240,208,.4))}
.o i.ic{background:rgba(255,255,255,.09)} .o i img{width:16px;height:16px;border-radius:4px}
.o i.s{background:none;opacity:.6}
.o span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.o em{font-style:normal;opacity:.5} .o b{font-weight:600}
"""

SEARCH_SCRIPT = r"""
(function(){
var D=__DATA__, ENG=__ENG__;
var ICON='<svg viewBox="0 0 24 24" width=16 height=16 fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round><circle cx=11 cy=11 r=7></circle><path d="m20 20-3.5-3.5"></path></svg>';
var body=document.body, card=document.getElementById('card'), inp=card.querySelector('input'),
    sl=document.getElementById('sl'), form=document.getElementById('sb'), cur=[], sel=-1;
function esc(s){return String(s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function host(u){try{return new URL(u).host.replace(/^www\./,'');}catch(e){return u;}}
function isOpen(){return body.classList.contains('foc');}
function build(){
  var q=inp.value.trim(), lq=q.toLowerCase(), out=[];
  if(q)out.push({q:q});
  D.forEach(function(d){
    if(out.length>=(q?7:6))return;
    if(!q||d.t.toLowerCase().indexOf(lq)>=0||d.u.toLowerCase().indexOf(lq)>=0)out.push(d);
  });
  return out;
}
function mark(){
  var rows=sl.children;
  for(var i=0;i<rows.length;i++)rows[i].classList.toggle('on',i===sel);
}
function render(){
  cur=build();sel=-1;
  sl.innerHTML=cur.map(function(d,i){
    if(d.q!==undefined)
      return '<a class=o data-i="'+i+'" href="#"><i class=s>'+ICON+'</i><span>Search '+esc(ENG)+' for <b>'+esc(d.q)+'</b></span></a>';
    var ic=d.i?'<i class=ic><img src="'+esc(d.i)+'"></i>':'<i>'+esc((d.t||'?').charAt(0).toUpperCase())+'</i>';
    return '<a class=o data-i="'+i+'" href="'+esc(d.u)+'">'+ic+'<span>'+esc(d.t)+'<em> \u2014 '+esc(host(d.u))+'</em></span></a>';
  }).join('');
}
function open(){if(!isOpen()){body.classList.add('foc');render();}}
function close(){body.classList.remove('foc');sel=-1;inp.blur();}
card.addEventListener('mousedown',open);
inp.addEventListener('input',function(){open();render();});
sl.addEventListener('mouseover',function(e){
  var a=e.target.closest&&e.target.closest('a.o');
  if(a){sel=+a.getAttribute('data-i');mark();}
});
sl.addEventListener('click',function(e){
  var a=e.target.closest&&e.target.closest('a.o');
  if(a&&cur[+a.getAttribute('data-i')].q!==undefined){e.preventDefault();form.submit();}
});
inp.addEventListener('keydown',function(e){
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){
    e.preventDefault();open();
    var n=cur.length;if(!n)return;
    sel=e.key==='ArrowDown'?(sel+1)%n:(sel<=0?n-1:sel-1);mark();
  }else if(e.key==='Enter'&&sel>=0&&cur[sel]&&cur[sel].u){
    e.preventDefault();location.href=cur[sel].u;
  }
});
document.addEventListener('mousedown',function(e){if(isOpen()&&!card.contains(e.target))close();},true);
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&isOpen()){e.preventDefault();close();}});
})();
"""


def start_html(engine, bookmarks, sites=None, bg=None, greeting=None):
    e = html.escape
    if sites is None:
        sites = [{"url": b["url"], "title": b["title"] or b["url"], "host": ""} for b in bookmarks[:8]]

    def site_icon(st):
        host = st.get("host", "")
        if host and re.fullmatch(r"[\w.\-]+", host):
            ip = ICON_DIR / (host + ".png")
            if ip.exists():
                try:
                    return '<i class=ic><img src="data:image/png;base64,%s"></i>' % base64.b64encode(ip.read_bytes()).decode()
                except OSError:
                    pass
        label = (st["title"] or st["url"])[:1].upper()
        return "<i>" + e(label) + "</i>"

    cells = []
    for st in sites:
        name = e((st["title"] or st["url"])[:16])
        rm = ""
        if st.get("host"):
            rm = '<a class=rm title="Remove" href="fjord://top-remove?h=%s">\u2715</a>' % quote(st["host"], safe="")
        cells.append('<div class=w><a class=t href="%s">%s<span>%s</span></a>%s</div>'
                     % (e(st["url"], True), site_icon(st), name, rm))
    cells.append('<div class=w><a class=t href="fjord://top-add"><i class=add>+</i><span>Add site</span></a></div>')
    tiles = "".join(cells)
    sugg, seen_u = [], set()
    for st in list(sites) + [{"url": bk["url"], "title": bk["title"] or bk["url"], "host": ""} for bk in bookmarks]:
        if st["url"] in seen_u or len(sugg) >= 30:
            continue
        seen_u.add(st["url"])
        sugg.append({"u": st["url"], "t": st["title"] or st["url"], "i": icon_uri(st.get("host", ""))})
    search_script = ("<script>" + SEARCH_SCRIPT.replace("__DATA__", json.dumps(sugg).replace("</", "<\\/"))
                     .replace("__ENG__", json.dumps(engine)) + "</script>")
    gtext, gauthor = greeting or (DEFAULT_GREETING, "")
    if gauthor:
        head = '<h1 class=q>\u201c%s\u201d</h1><p class=by>\u2014 %s</p>' % (e(gtext), e(gauthor))
    else:
        gcls = "" if len(gtext) <= 28 else ("m" if len(gtext) <= 50 else "q")
        head = "<h1%s>%s</h1>" % (" class=" + gcls if gcls else "", e(gtext))
    extra_css, bg_html, bg_script = "", "", ""
    if bg:
        url_attr = e(bg["url"], True)
        if bg["kind"] == "video":
            bg_html = '<video class=bg id=bgv src="%s" crossorigin=anonymous autoplay muted loop playsinline></video>' % url_attr
        else:
            bg_html = '<div class=bg style="background-image:url(&quot;%s&quot;)"></div>' % url_attr
        bg_html += "<div class=dim></div>"
        bg_script = ("<script>" + BG_SCRIPT.replace("__URL__", json.dumps(bg["url"])).replace("__FILE__", json.dumps(bg.get("file", "")))
                     .replace("__KIND__", json.dumps(bg["kind"])).replace("__EVT__", json.dumps(bg.get("evt", ""))).replace("__DONE__", "true" if bg.get("done") else "false")
                     + "</script>")
        extra_css += (".bg{position:fixed;inset:0;width:100%%;height:100%%;object-fit:cover;background-size:cover;"
                      "background-position:center;z-index:0} .dim{position:fixed;inset:0;z-index:0;"
                      "background:rgba(0,0,0,%.2f)} main{position:relative;z-index:1} "
                      "h1,.by,.t span{text-shadow:0 1px 10px rgba(0,0,0,.55)}"
                      % (bg["dim"] / 100.0))
    elif CUR_BG["light"]:
        extra_css += ("input{background:rgba(0,0,0,.06);border-color:rgba(0,0,0,.18)} "
                      ".t:hover{background:rgba(0,0,0,.07)} h1{font-weight:300}")
    if CUR_BG["light"] and not bg:
        extra_css += (".card{background:rgba(0,0,0,.06);border-color:rgba(0,0,0,.18);color:#12202c} "
                      "body.foc .card{background:rgba(255,255,255,.97);border-color:rgba(0,0,0,.12)} .o.on{background:rgba(0,0,0,.07)}")
    return themed(f"""<!doctype html><meta charset=utf-8><meta name=color-scheme content=dark><title>New Tab</title><style>{base_css()}
body{{display:flex;align-items:center;justify-content:center;height:100vh}}
main{{text-align:center;width:min(600px,88vw)}}
h1{{font-weight:300;font-size:44px;letter-spacing:.02em;margin:0 0 28px}}
input{{width:100%;box-sizing:border-box;padding:16px 24px;font-size:16px;color:inherit;
background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.1);border-radius:999px;outline:none}}
input:focus{{border-color:rgba(79,176,232,.7);background:rgba(255,255,255,.1)}}
.g{{display:flex;flex-wrap:wrap;gap:14px;justify-content:center;margin-top:34px}}
.t{{width:84px;display:flex;flex-direction:column;align-items:center;gap:8px;font-size:12px;
opacity:.85;padding:10px 0;border-radius:16px}} .t:hover{{background:rgba(255,255,255,.07)}}
.t i{{font-style:normal;width:44px;height:44px;border-radius:14px;display:grid;place-items:center;
font-size:18px;background:linear-gradient(135deg,rgba(79,176,232,.5),rgba(126,240,208,.4))}}
@keyframes up{{from{{opacity:0;transform:translateY(16px)}}to{{opacity:1;transform:none}}}}
h1{{font-weight:200;letter-spacing:.03em;animation:up .8s cubic-bezier(.2,.7,.2,1) both}}
form{{animation:up .8s .08s cubic-bezier(.2,.7,.2,1) both}} .g{{animation:up .8s .16s cubic-bezier(.2,.7,.2,1) both}}
input{{transition:border-color .3s,background .3s,box-shadow .3s}} input:focus{{box-shadow:0 0 0 5px rgba(79,176,232,.12)}}
.t{{transition:background .25s,transform .25s}} .t:hover{{transform:translateY(-3px)}}
.w{{position:relative}} .t span{{max-width:78px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.t i.ic{{background:rgba(255,255,255,.09)}} .t i img{{width:26px;height:26px;border-radius:6px}}
.t i.add{{background:rgba(255,255,255,.07);font-size:24px;font-weight:200}}
.rm{{position:absolute;top:4px;right:6px;font-size:9px;line-height:1;padding:4px 5px;border-radius:99px;background:rgba(0,0,0,.45);opacity:0;transition:opacity .2s}} .w:hover .rm{{opacity:.7}} .rm:hover{{opacity:1!important}}
h1.m{{font-size:34px;line-height:1.25}} h1.q{{font-size:26px;line-height:1.4;text-wrap:balance}}
.by{{margin:-16px 0 28px;opacity:.55;font-size:13px;letter-spacing:.06em;animation:up .8s .04s cubic-bezier(.2,.7,.2,1) both}}
{SEARCH_CSS}{extra_css}</style>{bg_html}<div class=veil id=veil></div><main>{head}
<form class=sb id=sb action="fjord://search" autocomplete=off><div class=card id=card><div class=sr>{SEARCH_ICON}<input name=q autofocus autocomplete=off
placeholder="Search with {e(engine)} or enter address"></div><div class=sl id=sl></div></div></form><div class=g>{tiles}</div></main>{bg_script}{search_script}""")


def list_page(title, rows, action_html=""):
    return f"""<!doctype html><meta charset=utf-8><meta name=color-scheme content=dark><title>{html.escape(title)}</title><style>{base_css()}
main{{max-width:760px;margin:0 auto;padding:40px 20px}}
h1{{font-weight:300;display:flex;justify-content:space-between;align-items:center}}
h1 a{{font-size:13px;opacity:.6;padding:6px 14px;border-radius:99px;background:rgba(255,255,255,.08)}}
.r{{display:flex;align-items:center;border-radius:14px}} .r:hover{{background:rgba(255,255,255,.06)}}
.r>a:first-child{{flex:1;min-width:0;padding:10px 14px}}
b{{display:block;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
small{{display:block;opacity:.45;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.x{{padding:10px 16px;opacity:.5}} .x:hover{{opacity:1}} .e{{opacity:.5}}
.fh{{font-size:11px;letter-spacing:.14em;text-transform:uppercase;opacity:.45;margin:26px 14px 6px}}
@keyframes up{{from{{opacity:0;transform:translateY(10px)}}to{{opacity:1;transform:none}}}}
main{{animation:up .5s cubic-bezier(.2,.7,.2,1) both}} .r{{transition:background .25s}} h1{{font-weight:200}}
</style><main><h1>{html.escape(title)}{action_html}</h1>{rows or '<p class=e>Nothing here yet.</p>'}</main>"""


SETTINGS_CSS = """
main{max-width:720px;margin:0 auto;padding:44px 22px 80px}
h1{font-weight:200;font-size:34px;letter-spacing:.03em;margin:0 0 26px}
h2{font-size:11px;font-weight:500;letter-spacing:.14em;text-transform:uppercase;opacity:.45;margin:34px 0 10px}
.card{background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.07);border-radius:18px;padding:6px 18px}
.row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 0;border-bottom:1px solid rgba(255,255,255,.06)}
.row:last-child{border:none}
.row small{display:block;opacity:.5;margin-top:3px;font-size:12px}
.sw{flex:none;width:42px;height:24px;border-radius:99px;background:rgba(255,255,255,.2);box-shadow:inset 0 0 0 1px rgba(255,255,255,.2);position:relative;transition:background .25s}
.sw i{position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#f1f7fb;box-shadow:0 1px 3px rgba(0,0,0,.4);transition:left .25s}
.sw.on{background:linear-gradient(135deg,#4fb0e8,#7ef0d0)} .sw.on i{left:21px}
.seg{flex:none;display:flex;background:rgba(255,255,255,.07);border-radius:99px;padding:3px}
.seg a{padding:6px 16px;border-radius:99px;font-size:12px;opacity:.7;transition:background .25s}
.seg a.on{background:linear-gradient(135deg,rgba(79,176,232,.55),rgba(126,240,208,.4));opacity:1}
.chips{display:flex;flex-wrap:wrap;gap:8px;padding:14px 0}
.chip{padding:8px 14px;border-radius:12px;background:rgba(255,255,255,.06);font-size:14px;transition:background .2s}
.chip:hover{background:rgba(255,255,255,.12)} .chip.on{background:rgba(79,176,232,.3);box-shadow:inset 0 0 0 1px rgba(79,176,232,.6)}
.x{opacity:.5;padding:4px 12px;border-radius:99px;font-size:12px} .x:hover{opacity:1;background:rgba(255,255,255,.08)}
.hint{opacity:.45;font-size:12px;line-height:1.6;padding:0 0 14px}
.lg{display:inline-grid;place-items:center;width:16px;height:16px;border-radius:50%;color:#fff;font:700 10px/1 sans-serif;font-style:normal;margin-right:7px;vertical-align:-3px}
img.lg{background:none;border-radius:4px}
.pf{padding:2px 0 8px} .fr{display:flex;gap:8px;margin:10px 0;flex-wrap:wrap}
.fr input,.fr select{flex:1;min-width:120px;padding:9px 14px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.06);color:inherit;font:inherit;outline:none}
.fr input:focus{border-color:rgba(79,176,232,.7)} option{background:#132029}
button{padding:9px 22px;border:none;border-radius:99px;background:linear-gradient(135deg,#4fb0e8,#7ef0d0);color:#0b141d;font:inherit;font-weight:500;cursor:pointer}
.bds{display:flex;flex-wrap:wrap;gap:10px;justify-content:flex-end;max-width:340px}
.bdot{width:26px;height:26px;border-radius:50%;box-shadow:inset 0 0 0 1px rgba(255,255,255,.28);transition:transform .2s}
.bdot:hover{transform:scale(1.14)} .bdot.on{outline:2px solid #4fb0e8;outline-offset:2px}
input[type=color]{background:none;border:none;cursor:pointer}
details{padding:0 0 6px} summary{cursor:pointer;padding:14px 0 10px;opacity:.65;font-size:13px;list-style:none}
summary::-webkit-details-marker{display:none} summary:hover{opacity:1}
.sl{flex:none;display:flex;align-items:center;gap:12px} .sl input{width:190px;accent-color:#4fb0e8;cursor:pointer}
.sl .pc{font-size:12px;opacity:.8;min-width:62px;display:flex;align-items:center;justify-content:flex-end;gap:2px}
.sl .pv{width:44px;min-width:0;flex:none;padding:3px 4px;text-align:right;font:inherit;color:inherit;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.14);border-radius:8px;outline:none;-moz-appearance:textfield}
.sl .pv:focus{border-color:#4fb0e8}
.sl .pv::-webkit-inner-spin-button,.sl .pv::-webkit-outer-spin-button{-webkit-appearance:none;margin:0}
.tag{flex:none;padding:4px 12px;border-radius:99px;font-size:12px;background:rgba(79,176,232,.3)}
"""


def settings_html(b):
    e = html.escape
    st = b.settings

    def L(k, v):
        return "fjord://set?k=%s&v=%s" % (k, quote(str(v), safe=""))

    def sw(key, title, desc, default=False):
        on = bool(st.get(key, default))
        return ('<div class=row><div>%s<small>%s</small></div><a class="sw%s" href="%s"><i></i></a></div>'
                % (title, desc, " on" if on else "", L(key, 0 if on else 1)))

    def seg(key, opts, cur):
        return "<span class=seg>" + "".join(
            '<a class="%s" href="%s">%s</a>' % ("on" if v == cur else "", L(key, v), e(lbl)) for v, lbl in opts) + "</span>"

    def chip(n):
        return ("<a class=\"chip%s\" style=\"font-family:'%s',sans-serif\" href=\"%s\">%s</a>"
                % (" on" if n == CUR_FONT["family"] else "", e(n), L("font", n), e(n)))

    def slider(key, title, desc):
        _n, lo, hi, dflt = TUNE[key]
        cur = int(st.get(key, dflt)) if str(st.get(key, dflt)).lstrip("-").isdecimal() else dflt
        cur = max(lo, min(hi, cur))
        return ('<div class=row><div>%s<small>%s</small></div><div class=sl><input type=range min=%d max=%d step=1 value=%d '
                'oninput="this.parentNode.querySelector(\'.pv\').value=this.value" '
                'onchange="location.href=\'fjord://set?k=%s&amp;v=\'+this.value">'
                '<span class=pc><input class=pv type=number min=%d max=%d step=1 value=%d '
                'oninput="var r=this.parentNode.parentNode.querySelector(\'[type=range]\');if(this.value!==\'\')r.value=this.value" '
                'onchange="var v=Math.max(%d,Math.min(%d,Math.round(Number(this.value))));if(isNaN(v))v=%d;'
                'location.href=\'fjord://set?k=%s&amp;v=\'+v">%%</span></div></div>'
                % (title, desc, lo, hi, cur, key, lo, hi, cur, lo, hi, cur, key))
    tune_rows = (slider("glass_bright", "Glass brightness", "Below 100% the glass turns smoky and dark, above it gets brighter and whiter. Applies to glass and translucent surfaces")
                 + slider("glass_transp", "Glass transparency", "0% is frosted and solid-looking, 50% is the original look, 100% is fully clear")
                 + '<div class=row><div>Reset glass<small>Back to the original brightness and transparency</small></div>'
                   '<a class=x href="fjord://set?k=ui_reset&amp;v=1">Reset</a></div>')
    layout_row = ('<div class=row><div>Tab layout<small>Vertical tabs in a sidebar, or a horizontal tab bar on top</small></div>'
                  + seg("layout", [("vertical", "Vertical"), ("horizontal", "Horizontal")], st.get("layout", "vertical")) + "</div>")
    style_row = ('<div class=row><div>Interface style<small>Default is Fjord as it is. macOS is Safari-like, with liquid-glass surfaces, '
                 'extra animation and macOS window buttons. Windows is a flat Windows 11 look with Windows window buttons</small></div>'
                 + seg("ui_style", list(UI_MODES), UI["mode"]) + "</div>")
    win_note = ("Minimise, zoom and close buttons next to the menu, in Windows or macOS style" if UI["mode"] == "default"
                else "Set by the interface style above. Pick Default there to choose these separately")
    win_row = ('<div class=row><div>Window buttons<small>' + win_note + '</small></div>'
               + seg("winbtns", [("windows", "Windows"), ("mac", "macOS")], st.get("winbtns", default_winbtns())) + "</div>")
    ess = "".join(
        '<div class=row><div>%s<small>%s</small></div><a class=x href="fjord://ess-remove?h=%s">Remove</a></div>'
        % (e(x["title"]), e(x["host"]), quote(x["host"], safe="")) for x in b.essentials)
    if not ess:
        ess = '<div class=hint style="padding-top:14px">No Essentials yet. Open a website in another tab, then pick it from the list below or right-click its tab and choose Add to Essentials.</div>'
    have = {x["host"] for x in b.essentials}
    open_rows = ""
    for i in range(b.stack.count()):
        w = b.stack.widget(i)
        h = b.host_of(w.url())
        if w.closing or b.is_internal(w.url()) or not h or h in have:
            continue
        have.add(h)
        open_rows += ('<div class=row><div>%s<small>%s</small></div><a class=x href="fjord://ess-add?i=%d">Add</a></div>'
                      % (e(w.title() or h), e(h), i))
    if open_rows:
        ess += '<div class=hint style="padding:16px 0 0">Add from your open tabs</div>' + open_rows
    def eng_seg():
        out = []
        for n in ENGINES:
            png = engine_png_bytes(n)
            if png:
                logo = '<img class=lg src="data:image/png;base64,%s">' % base64.b64encode(png).decode()
            else:
                c, l = ENGINE_BADGE.get(n, ("#4fb0e8", n[:1]))
                logo = '<i class=lg style="background:%s">%s</i>' % (c, l)
            out.append('<a class="%s" href="%s">%s%s</a>' % ("on" if n == b.engine else "", L("engine", n), logo, e(n)))
        return "<span class=seg>" + "".join(out) + "</span>"
    engine_row = ('<div class=row><div>Default search engine<small>Used by the address bar and the start page</small></div>'
                  + eng_seg() + "</div>")
    ab = b.adblock
    if ab.engine:
        info = "%d network rules, %d cosmetic rules" % (ab.engine.block.n + ab.engine.allow.n, ab.engine.cosmetic_n)
        if ab.updated:
            days = int((time.time() - ab.updated) // 86400)
            info += ", updated " + ("today" if days < 1 else "%d day%s ago" % (days, "" if days == 1 else "s"))
    elif ab.loading:
        info = "Loading filter lists..."
    else:
        info = "Filter lists not loaded (needs an internet connection)"
    paused_rows = "".join(
        '<div class=row><div>%s<small>Ad blocking paused on this site</small></div>'
        '<a class=x href="fjord://allow-remove?h=%s">Resume</a></div>' % (e(h), quote(h, safe="")) for h in sorted(ab.allow))
    privacy = ("<h2>Privacy</h2><div class=card>"
               + sw("adblock", "Block ads and trackers",
                    "Uses the uBlock Origin filter lists: uBlock filters, EasyList, EasyPrivacy, badware and more", True)
               + '<div class=row><div>Filter lists<small>' + e(info) + " &middot; " + str(ab.blocked)
               + ' requests blocked this session</small></div><a class=x href="fjord://adblock-update">Update now</a></div>'
               + paused_rows + "</div>")

    def opt(v, label):
        return "<option value=%s%s>%s</option>" % (v, " selected" if st.get("proxy_type", "socks5") == v else "", label)
    vpn = ("<h2>VPN / Proxy</h2><div class=card>"
           + sw("vpn", "Route Fjord through a proxy",
                "Sends all browser traffic and DNS through the proxy below, and stops WebRTC leaks. "
                "Turning it on or off restarts Fjord and restores your tabs.")
           + '<form class=pf action="fjord://vpn"><div class=fr><select name=type>'
           + opt("socks5", "SOCKS5") + opt("http", "HTTP") + opt("https", "HTTPS") + "</select>"
           + '<input name=host placeholder="Host" value="' + e(st.get("proxy_host", "")) + '">'
           + '<input name=port placeholder="Port" style="max-width:90px" value="' + e(str(st.get("proxy_port", ""))) + '"></div>'
           + '<div class=fr><input name=user placeholder="Username (HTTP/HTTPS only)" value="' + e(st.get("proxy_user", "")) + '">'
           + '<input name=pw type=password placeholder="Password (leave blank to keep)"></div>'
           + '<div class=fr style="align-items:center"><button>Save</button>'
           + '<a class=x href="fjord://set?k=preset&v=tor">Use Tor</a>'
           + '<a class=x href="fjord://set?k=preset&v=local">Local 127.0.0.1:1080</a>'
           + '<a class=x href="https://ipinfo.io/">Check my IP</a></div>'
           + "<div class=hint>A browser cannot create a VPN on its own. Point this at a proxy from a VPN or privacy service, "
             "or at Tor (install it and start it first). Chromium cannot do SOCKS5 logins, so use an HTTP proxy for services "
             "that need a username. The password is saved in plain text in settings.json.</div></form></div>")
    n_ext = len(b.extensions.records)
    ext_sec = ('<h2>Extensions</h2><div class=card><div class=row><div>Chrome, Firefox and Safari extensions<small>%s</small></div>'
               '<a class=x href="fjord://ext-open">Manage</a></div></div>' % (e("%d installed" % n_ext) if n_ext else "None installed yet"))
    gc = b.greeting_conf()
    gmode = gc.get("mode") if gc.get("mode") in ("default", "custom", "quote") else "default"
    greet_rows = ('<div class=row><div>Text above the search bar<small>Keep the default, write your own, or show a quote</small></div>'
                  + seg("greet_mode", [("default", "Default"), ("custom", "Custom"), ("quote", "Daily quote")], gmode) + "</div>")
    if gmode == "custom":
        greet_rows += ('<form class=row action="fjord://set"><div>Your text<small>Up to %d characters. Leave it blank to go back to the default.</small></div>'
                       '<div class=fr style="margin:0;flex:1;max-width:340px"><input type=hidden name=k value=greet_text>'
                       '<input name=v maxlength=%d placeholder="%s" value="%s"><button>Save</button></div></form>'
                       % (GREETING_MAX, GREETING_MAX, e(DEFAULT_GREETING, True), e(str(gc.get("text", "")), True)))
    elif gmode == "quote":
        pick = gc.get("pick")
        pinned = isinstance(pick, int) and not isinstance(pick, bool) and 0 <= pick < len(QUOTES)
        today = b.daily_quote_index()
        cur_idx = pick if pinned else today
        greet_rows += ('<div class=row><div>Quote<small>A new one every day, or pin the one you like</small></div>'
                       + seg("greet_pick", [("daily", "Changes daily"), (str(cur_idx), "Pinned" if pinned else "Pin today's")],
                             str(pick) if pinned else "daily") + "</div>")
        qrows = ""
        for i, (qt, qa) in enumerate(QUOTES):
            if pinned and i == pick:
                act = '<span class=tag>Pinned</span>'
            elif not pinned and i == today:
                act = '<a class="x tag" href="%s">Today &middot; pin</a>' % L("greet_pick", i)
            else:
                act = '<a class=x href="%s">Use</a>' % L("greet_pick", i)
            qrows += '<div class=row><div>\u201c%s\u201d<small>\u2014 %s</small></div>%s</div>' % (e(qt), e(qa), act)
        greet_rows += '<details%s><summary>Browse all %d quotes</summary>%s</details>' % (" open" if pinned else "", len(QUOTES), qrows)
    greet_sec = "<h2>New tab greeting</h2><div class=card>" + greet_rows + "</div>"
    bgc = st.get("bg") if isinstance(st.get("bg"), dict) else {}
    bkind = bgc.get("type", "default")

    def bl(**kw):
        return "fjord://bg?" + "&".join("%s=%s" % (k, quote(str(v), safe="")) for k, v in kw.items())

    def dot(css, title, href, on):
        return '<a class="bdot%s" title="%s" style="background:%s" href="%s"></a>' % (
            " on" if on else "", e(title), e(css, True), href)
    cols = "".join(dot(c, c, bl(kind="color", c=c), bkind == "color" and bgc.get("color", "").lower() == c)
                   for c in BG_COLORS)
    grads = "".join(dot(g, n, bl(kind="gradient", i=i), bkind == "gradient" and bgc.get("grad") == i)
                    for i, (n, g) in enumerate(BG_GRADIENTS))
    cur_col = bgc.get("color", "#0b141d") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(bgc.get("color", ""))) else "#0b141d"
    media_note = (e(bgc.get("label", "")) + " (playing on the new tab page)") if bkind in ("image", "video") else (
        "Pick a file from your computer. Videos play muted and loop. Fjord plays WebM; other formats (MP4, MOV) are "
        "converted automatically if ffmpeg is installed.")
    bg_sec = ("<h2>New tab background</h2><div class=card>"
              '<div class=row><div>Solid colour<small>Pick a preset</small></div><div class=bds>' + cols + "</div></div>"
              '<form class=row action="fjord://bg"><div>Custom colour<small>Any colour you like</small></div>'
              '<div class=fr style="margin:0;flex:none;align-items:center"><input type=hidden name=kind value=color>'
              '<input type=color name=c value="' + cur_col + '" style="flex:none;min-width:0;width:46px;height:36px;padding:2px">'
              "<button>Apply</button></div></form>"
              '<div class=row><div>Gradient<small>Soft two-tone backgrounds</small></div><div class=bds>' + grads + "</div></div>"
              "<div class=row><div>Image or video<small>" + media_note + "</small></div><span class=seg>"
              + '<a class="%s" href="%s">Image…</a>' % ("on" if bkind == "image" else "", bl(kind="image"))
              + '<a class="%s" href="%s">Video…</a>' % ("on" if bkind == "video" else "", bl(kind="video")) + "</span></div>"
              '<form class=row action="fjord://bg"><div>Darken image or video<small>Keeps text readable on bright backgrounds</small></div>'
              '<input type=hidden name=kind value=dim><input type=range name=v min=0 max=80 value="'
              + str(int(bgc.get("dim", 30))) + '" style="flex:none;width:160px" onchange="this.form.submit()"></form>'
              '<div class=row><div>Reset background<small>Go back to the default Fjord look</small></div>'
              '<a class=x href="' + bl(kind="default") + '">Reset</a></div></div>')
    has_accent = isinstance(st.get("accent"), dict)
    tour_sec = ("<h2>Welcome tour</h2><div class=card>"
                "<div class=row><div>Replay the tour<small>Import, accent colour, layout and a run through the features</small></div>"
                '<a class=x href="fjord://set?k=tour&v=1">Start</a></div>'
                "<div class=row><div>Accent colour<small>" + ("Custom accent chosen in the tour" if has_accent
                                                              else "Follows your new tab background") + "</small></div>"
                + ('<a class=x href="fjord://set?k=accent_reset&v=1">Reset</a>' if has_accent else "") + "</div></div>")
    return ("<!doctype html><meta charset=utf-8><meta name=color-scheme content=dark><title>Settings</title><style>" + base_css() + themed(SETTINGS_CSS) + "</style>"
            "<main><h1>Settings</h1><h2>Sidebar</h2><div class=card>" + layout_row
            + sw("compact", "Compact mode", "Shrink the vertical sidebar to a slim bar that shows only site icons")
            + sw("autohide", "Auto-hide sidebar", "Hide the sidebar until you move the mouse to the left edge")
            + sw("visualizer", "Media visualiser", "Show animated audio bars in the sidebar media player")
            + "</div><h2>Window</h2><div class=card>" + style_row + win_row + "</div><h2>Glass</h2><div class=card>" + tune_rows + "</div><h2>Essentials</h2><div class=card>"
            + sw("ess_startup", "Keep Essentials loaded", "Open your Essentials in the background at startup so they are always ready", True)
            + ess + "</div>" + privacy + vpn + ext_sec + "<h2>Search</h2><div class=card>" + engine_row
            + "</div>" + greet_sec + bg_sec + tour_sec + "<h2>Font</h2><div class=card><div class=chips>" + "".join(chip(n) for n in installed_fonts())
            + "</div><div class=hint>Want more? Drop .ttf or .otf files into <b>" + e(str(DATA_DIR / "fonts"))
            + "</b> and restart Fjord.</div></div></main>")


QSS = """
* { font-size: 12px; color: #e4edf3; }
QMainWindow, #root { background: #0b141d; }
#sidebar { background: #101b26; border-radius: 16px; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { border-radius: 12px; margin: 2px 0; }
QListWidget::item:hover { background: rgba(255,255,255,0.05); }
QListWidget::item:selected { background: rgba(79,176,232,0.22); }
QLabel { background: transparent; }
QToolButton { background: transparent; border: none; border-radius: 10px;
              padding: 4px 10px; font-size: 17px; color: #b5c6d4; }
QToolButton:hover { color: #fff; }
QToolButton:disabled { color: #3f5163; }
QToolButton#close { font-size: 11px; padding: 3px 6px; }
QToolButton#miniclose { font-size: 8px; padding: 0; background: rgba(255,255,255,0.16); border-radius: 7px; color: #e4edf3; }
QPushButton#newtab { background: rgba(79,176,232,0.16); border: none; border-radius: 14px;
                     padding: 10px; color: #c5e6fa; }
QPushButton#newtab:hover { background: rgba(79,176,232,0.28); }
QFrame#tbpanel { background: #101b26; border-radius: 16px; }
QLabel#tbtitle { font-size: 13px; font-weight: 600; color: #e4edf3; }
QLabel#tbhint { color: #8ea3b4; }
QPushButton#tbdone { background: rgba(79,176,232,0.22); border: none; border-radius: 12px; padding: 7px 18px; color: #c5e6fa; }
QPushButton#tbdone:hover { background: rgba(79,176,232,0.34); }
QPushButton#tbreset { background: transparent; border: none; border-radius: 12px; padding: 7px 14px; color: #8ea3b4; }
QPushButton#tbreset:hover { background: rgba(255,255,255,0.06); color: #e4edf3; }
QLineEdit { background: #172431; border: 1px solid transparent; border-radius: 16px;
            padding: 8px 16px; selection-background-color: #4fb0e8; }
QLineEdit:focus { border: 1px solid rgba(79,176,232,0.7); background: #1c2b3a; }
QProgressBar { background: transparent; border: none; max-height: 2px; min-height: 2px; }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #4fb0e8,stop:1 #7ef0d0); }
QStatusBar { background: #0b141d; color: #7d93a5; }
QToolButton#engine { padding: 0; background: transparent; border-radius: 12px; }
QToolButton#tbicon { padding: 0; background: transparent; border-radius: 12px; }
QToolButton#engine:hover { color: #fff; }
QToolButton#essential { background: rgba(255,255,255,0.06); border-radius: 12px; padding: 0; font-size: 15px; color: #c5e6fa; }
QToolButton#essential:checked { background: rgba(79,176,232,0.22); border: 1px solid rgba(79,176,232,0.65); }
QFrame#divider { background: rgba(255,255,255,0.09); border: none; }
QPushButton#newtab[compact="true"] { background: transparent; color: #8ea3b4; font-size: 20px; padding: 6px; }
QPushButton#newtab[compact="true"]:hover { background: rgba(255,255,255,0.07); color: #fff; }
QMessageBox, QDialog { background: #101b26; }
QDialog QPushButton { background: #1f2e3d; border: none; border-radius: 10px; padding: 7px 18px; min-width: 64px; }
QDialog QPushButton:hover { background: #2c4156; }
QToolButton#newgroup { font-size: 11px; color: #8ea3b4; border: 1px solid transparent; border-radius: 10px; padding: 5px 8px; }
QToolButton#newgroup:hover { color: #fff; }
QMessageBox QPushButton { background: #1f2e3d; border: none; border-radius: 10px; padding: 7px 18px; min-width: 64px; }
QMessageBox QPushButton:hover { background: #2c4156; }
QMenu { background: #132029; border: 1px solid #243546; border-radius: 12px; padding: 6px; menu-scrollable: 1; }
QMenu::item { padding: 7px 22px; border-radius: 8px; }
QMenu::item:selected { background: rgba(79,176,232,0.25); }
QMenu::separator { height: 1px; background: #243546; margin: 5px 8px; }
QFrame#media { background: rgba(255,255,255,0.06); border-radius: 14px; }
QLabel#mediatitle { font-size: 12px; font-weight: 600; color: #e4edf3; }
QLabel#mediasub { font-size: 11px; color: #8ea3b4; }
QFrame#scratch { background: #101b26; border: 1px solid rgba(255,255,255,0.08); border-radius: 16px; }
QWidget#sbody { background: transparent; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollArea QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 0; }
QScrollArea QScrollBar::handle:vertical { background: rgba(255,255,255,0.16); border-radius: 3px; min-height: 28px; }
QScrollArea QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.28); }
QScrollArea QScrollBar::add-line:vertical, QScrollArea QScrollBar::sub-line:vertical { height: 0; }
QScrollArea QScrollBar::add-page:vertical, QScrollArea QScrollBar::sub-page:vertical { background: transparent; }
QFrame#scard { background: rgba(255,255,255,0.045); border-radius: 13px; }
QFrame#scard:hover { background: rgba(255,255,255,0.085); }
QLabel#scratchtitle { font-size: 14px; font-weight: 600; color: #e4edf3; }
QToolButton#scratchclear { font-size: 11px; color: #8ea3b4; padding: 4px 8px; border-radius: 8px; }
QToolButton#scratchclear:hover { color: #fff; background: rgba(255,255,255,0.07); }
"""
POPUP_QSS = ("QListView{background:#172431;border:1px solid #2b3f54;border-radius:10px;"
             "outline:none;padding:4px}QListView::item{padding:6px 10px;border-radius:6px}"
             "QListView::item:selected{background:rgba(79,176,232,.3)}")

# Extra rules laid over QSS for the macOS and Windows interface styles (later rules win over the ones above).
QSS_MAC = """
#sidebar { background: rgba(255,255,255,0.035); border-radius: 20px; }
QListWidget::item { border-radius: 12px; margin: 2px 0; }
QListWidget::item:hover { background: rgba(255,255,255,0.06); }
QListWidget::item:selected { background: rgba(255,255,255,0.09); }
QToolButton { border-radius: 16px; }
QToolButton#engine, QToolButton#tbicon { border-radius: 18px; }
QToolButton#essential { background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.10); border-radius: 14px; }
QToolButton#essential:checked { background: rgba(79,176,232,0.22); border: 1px solid rgba(79,176,232,0.60); }
QPushButton#newtab { background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.10); border-radius: 16px; padding: 10px; color: #e4edf3; }
QPushButton#newtab:hover { background: rgba(255,255,255,0.12); }
QLineEdit { background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.09); border-radius: 18px; padding: 8px 16px; }
QLineEdit:focus { background: rgba(255,255,255,0.10); border: 1px solid rgba(79,176,232,0.45); }
QFrame#tbpanel, QFrame#scratch { border-radius: 20px; border: 1px solid rgba(255,255,255,0.12); }
QFrame#media { background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.09); border-radius: 16px; }
QFrame#scard { border-radius: 14px; }
QMenu { border: 1px solid rgba(255,255,255,0.14); border-radius: 14px; padding: 6px; }
QMenu::item { padding: 7px 20px; border-radius: 9px; }
QMenu::item:selected { background: rgba(255,255,255,0.12); }
QDialog QPushButton, QMessageBox QPushButton { border-radius: 14px; }
QPushButton#tbdone, QPushButton#tbreset { border-radius: 16px; }
QToolButton#newgroup { border-radius: 14px; }
QToolButton#miniclose { border-radius: 8px; }
"""
QSS_WIN = """
#sidebar { border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); }
QListWidget::item { border-radius: 5px; margin: 1px 0; }
QListWidget::item:hover { background: rgba(255,255,255,0.055); }
QListWidget::item:selected { background: rgba(255,255,255,0.095); }
QToolButton { border-radius: 5px; }
QToolButton#engine, QToolButton#tbicon { border-radius: 5px; }
QToolButton#essential { background: rgba(255,255,255,0.055); border: 1px solid rgba(255,255,255,0.05); border-radius: 6px; }
QToolButton#essential:checked { background: rgba(255,255,255,0.10); border: 1px solid rgba(79,176,232,0.80); }
QPushButton#newtab { background: #4fb0e8; border: none; border-radius: 5px; padding: 9px; color: #0b141d; }
QPushButton#newtab:hover { background: rgba(79,176,232,0.86); }
QLineEdit { background: #172431; border: 1px solid rgba(255,255,255,0.07); border-radius: 5px; padding: 6px 12px; selection-background-color: #4fb0e8; }
QLineEdit:focus { background: #0b141d; border: 1px solid rgba(255,255,255,0.12); }
QFrame#tbpanel, QFrame#scratch { border-radius: 8px; }
QFrame#media { border-radius: 8px; }
QFrame#scard { border-radius: 6px; }
QMenu { border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 20px; border-radius: 4px; margin: 1px 2px; }
QMenu::item:selected { background: rgba(255,255,255,0.08); }
QDialog QPushButton, QMessageBox QPushButton { border-radius: 5px; }
QPushButton#tbdone { background: #4fb0e8; color: #0b141d; border-radius: 5px; }
QPushButton#tbdone:hover { background: rgba(79,176,232,0.86); }
QPushButton#tbreset, QToolButton#newgroup { border-radius: 5px; }
QToolButton#miniclose { border-radius: 4px; }
QProgressBar::chunk { background: #4fb0e8; }
"""
POPUP_MAC = ("QListView{border-radius:14px;padding:6px;border:1px solid rgba(255,255,255,0.14)}"
             "QListView::item{border-radius:9px;padding:7px 12px}")
POPUP_WIN = ("QListView{border-radius:8px;padding:4px}QListView::item{border-radius:4px;padding:6px 10px}"
             "QListView::item:selected{background:rgba(255,255,255,0.09)}")


QSS_HORIZ = """
QListWidget[horiz="true"]::item { margin: 4px 1px 0 1px; border-radius: 0;
    border-top-left-radius: 9px; border-top-right-radius: 9px;
    border-bottom-left-radius: 0; border-bottom-right-radius: 0; }
QListWidget[horiz="true"]::item:hover { background: rgba(255,255,255,0.06); }
QListWidget[horiz="true"]::item:selected { background: rgba(255,255,255,0.12); }
"""


def app_qss():
    return QSS + {"mac": QSS_MAC, "windows": QSS_WIN}.get(UI["mode"], "") + QSS_HORIZ


def popup_qss():
    return POPUP_QSS + {"mac": POPUP_MAC, "windows": POPUP_WIN}.get(UI["mode"], "")


def trim_memory():
    """Collect garbage and hand freed heap pages back to the OS (Python's allocator otherwise keeps them)."""
    gc.collect()
    try:
        if sys.platform.startswith("linux"):
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        elif sys.platform == "win32":
            import ctypes
            k = ctypes.windll.kernel32
            k.GetCurrentProcess.restype = ctypes.c_void_p
            k.SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
            k.SetProcessWorkingSetSize(k.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))
    except Exception:
        pass


# ---------- ad blocking: parses uBlock Origin / EasyList filter syntax ----------
FILTER_DIR = DATA_DIR / "filters"
UA_BASE = "https://ublockorigin.github.io/uAssets/"
FILTER_LISTS = [
    ("ublock-filters", UA_BASE + "filters/filters.min.txt"),
    ("ublock-badware", UA_BASE + "filters/badware.min.txt"),
    ("ublock-privacy", UA_BASE + "filters/privacy.min.txt"),
    ("ublock-unbreak", UA_BASE + "filters/unbreak.min.txt"),
    ("ublock-quick-fixes", UA_BASE + "filters/quick-fixes.min.txt"),
    ("easylist", UA_BASE + "thirdparties/easylist.txt"),
    ("easyprivacy", UA_BASE + "thirdparties/easyprivacy.txt"),
    ("urlhaus", "https://malware-filter.gitlab.io/malware-filter/urlhaus-filter-online.txt"),
    ("peter-lowe", "https://pgl.yoyo.org/adservers/serverlist.php?hostformat=hosts&showintro=0&mimetype=plaintext"),
]
TWO_LEVEL = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "co.jp", "com.br", "co.in", "co.nz", "com.cn",
             "com.hk", "com.tw", "co.kr", "com.mx", "com.tr", "co.za", "com.sg", "com.ar"}
TYPE_OPTS = {"script": "script", "image": "image", "stylesheet": "stylesheet", "css": "stylesheet",
             "subdocument": "subdocument", "frame": "subdocument", "xmlhttprequest": "xmlhttprequest",
             "xhr": "xmlhttprequest", "media": "media", "font": "font", "object": "object", "ping": "ping",
             "other": "other"}
TOKEN_RE = re.compile(r"[a-z0-9%]{3,}")
HOST_RE = re.compile(r"([a-z0-9][a-z0-9.\-]*[a-z0-9])(?=[\^/:?]|$)")
HOSTS_LINE = re.compile(r"^(?:0\.0\.0\.0|127\.0\.0\.1)\s+([a-z0-9._\-]+)")
OPT_CHARS = re.compile(r"^[\w\-~=|.,*:/+]*$")
COS_RE = re.compile(r'^([^#/*|@"!]*)(##|#@#)(.+)$')
SKIP_COS = ("#?#", "#$#", "#@$#", "#%#", "#@%#", "#@?#", "+js(")
BAD_SEL = (":has-text(", ":-abp-", ":xpath(", ":matches-", ":upward(", ":remove(", ":style(", ":nth-ancestor(",
           ":min-text-length(", ":watch-attr(", ":others(", ":contains(", "{", "}", ":not(:")


def base_domain(host):
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if ".".join(parts[-2:]) in TWO_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _dom(first, ds):
    return any(first == d or first.endswith("." + d) for d in ds)


NOOPTS = (None, None, None, None, None)
_OPTS = {NOOPTS: NOOPTS}   # identical option tuples are stored once and shared by every rule that uses them
_RX = {}                   # small bounded cache of compiled patterns (a compiled regex costs far more than its source)


def _compiled(rx):
    c = _RX.get(rx)
    if c is None:
        try:
            c = re.compile(rx)
        except re.error:
            c = False
        if len(_RX) >= 1500:
            try:
                del _RX[next(iter(_RX))]
            except (StopIteration, KeyError, RuntimeError):
                pass
        _RX[rx] = c
    return c


class Rule:
    __slots__ = ("rx", "o")

    def __init__(self, rx, opts):
        self.rx = rx
        self.o = opts

    def check(self, url, first, rtype, third):
        t3, types, ntypes, inc, exc = self.o
        if t3 is not None and t3 != third:
            return False
        if types is not None and rtype not in types:
            return False
        if ntypes is not None and rtype in ntypes:
            return False
        if inc and not _dom(first, inc):
            return False
        if exc and _dom(first, exc):
            return False
        if self.rx is None:
            return True
        c = _compiled(self.rx)
        return bool(c) and c.search(url) is not None


def parse_opts(opts):
    third, types, ntypes, inc, exc = None, set(), set(), set(), set()
    for o in opts.lower().split(","):
        o = o.strip()
        if not o:
            continue
        neg = o.startswith("~")
        name = o[1:] if neg else o
        k, _, v = name.partition("=")
        if k == "domain":
            for d in v.split("|"):
                if not d:
                    continue
                if "*" in d or "/" in d:
                    return None
                (exc if d.startswith("~") else inc).add(d.lstrip("~"))
        elif k in ("third-party", "3p"):
            third = not neg
        elif k in ("first-party", "1p"):
            third = neg
        elif k in TYPE_OPTS:
            (ntypes if neg else types).add(TYPE_OPTS[k])
        elif k in ("important", "match-case"):
            pass
        else:  # redirect, csp, removeparam, popup, document, websocket, badfilter ... not supported
            return None
    res = (third, frozenset(types) if types else None, frozenset(ntypes) if ntypes else None,
           frozenset(inc) if inc else None, frozenset(exc) if exc else None)
    return _OPTS.setdefault(res, res)


def build_rule(pat, opts):
    o = parse_opts(opts)
    p = pat.lower()
    if o is None or not p or (len(p) > 2 and p[0] == "/" and p[-1] == "/"):
        return None
    prefix, start_anch, end_anch, host = "", False, False, None
    if p.endswith("|"):
        p, end_anch = p[:-1], True
    if p.startswith("||"):
        p, start_anch = p[2:], True
        prefix = r"^(?:[a-z][a-z0-9+.\-]*:)?//(?:[^/?#]*\.)?"
        m = HOST_RE.match(p)
        if m and "." in m.group(1) and p[m.end(1):] in ("", "^") and not end_anch:
            return m.group(1), None, Rule(None, o)  # whole-domain rule: no regex needed
        if m and "." in m.group(1):
            host = m.group(1)
    elif p.startswith("|"):
        p, start_anch, prefix = p[1:], True, "^"
    if not p:
        return None
    body = []
    for ch in p:
        body.append(".*" if ch == "*" else r"(?:[^\w\-.%]|$)" if ch == "^" else re.escape(ch))
    rx = prefix + "".join(body) + ("$" if end_anch else "")
    token = None
    if not host:
        for m in TOKEN_RE.finditer(p):
            a, b = m.span()
            if (a == 0 and not start_anch) or (b == len(p) and not end_anch):
                continue
            if (a > 0 and p[a - 1] == "*") or (b < len(p) and p[b] == "*"):
                continue
            if token is None or len(m.group()) > len(token):
                token = m.group()
    return host, token, Rule(rx, o)


class Index:
    def __init__(self):
        self.hosts, self.tokens, self.generic, self.n = {}, {}, [], 0
        self.hostset = set()   # "block this whole domain" rules: no Rule object needed

    def add_host(self, host):
        self.hostset.add(host)
        self.n += 1

    def add(self, host, token, rule):
        if host and rule.rx is None and rule.o == NOOPTS:
            self.hostset.add(host)
        elif host:
            self.hosts.setdefault(host, []).append(rule)
        elif token:
            self.tokens.setdefault(token, []).append(rule)
        else:
            self.generic.append(rule)
        self.n += 1

    def find(self, url, host, first, rtype, third):
        cand, h = [], host
        while h:
            if h in self.hostset:
                return True
            lst = self.hosts.get(h)
            if lst:
                cand += lst
            h = h.partition(".")[2]
        for t in set(TOKEN_RE.findall(url)):
            lst = self.tokens.get(t)
            if lst:
                cand += lst
        cand += self.generic
        return any(r.check(url, first, rtype, third) for r in cand)


class FilterEngine:
    def __init__(self):
        self.block, self.allow = Index(), Index()
        self.generic_sel, self._seen, self.generic_exc = [], set(), set()
        self.site_sel, self.cosmetic_n, self.generic_css = {}, 0, ""

    def parse(self, text):
        self.parse_lines(text.splitlines())

    def parse_lines(self, lines):
        """Takes any iterable of lines, so a list file can be streamed instead of loaded whole."""
        for i, line in enumerate(lines):
            if i % 4000 == 3999:
                time.sleep(0.002)  # let the UI thread breathe
            self.add_line(line.strip())

    def add_line(self, ln):
        if not ln or ln[0] in "![" or (ln[0] == "#" and not ln.startswith(("##", "#@#"))):
            return
        m = HOSTS_LINE.match(ln)
        if m:
            if m.group(1) not in ("localhost", "local", "broadcasthost"):
                self.block.add_host(m.group(1))
            return
        if any(x in ln for x in SKIP_COS):
            return
        m = COS_RE.match(ln)
        if m:
            doms, kind, sel = m.groups()
            if any(b in sel for b in BAD_SEL):
                return
            if kind == "#@#":
                if not doms:
                    self.generic_exc.add(sel)
            elif not doms:
                if sel not in self._seen and len(self.generic_sel) < 15000:
                    self._seen.add(sel)
                    self.generic_sel.append(sel)
                    self.cosmetic_n += 1
            else:
                ds = doms.lower().split(",")
                if not any(d.startswith("~") or "*" in d for d in ds):
                    for d in ds:
                        self.site_sel.setdefault(d, []).append(sel)
                    self.cosmetic_n += 1
            return
        exc = ln.startswith("@@")
        if exc:
            ln = ln[2:]
        opts, i = "", ln.rfind("$")
        if i >= 0 and OPT_CHARS.match(ln[i + 1:]):
            opts, ln = ln[i + 1:], ln[:i]
        built = build_rule(ln, opts)
        if built:
            (self.allow if exc else self.block).add(*built)

    def finalize(self):
        self.generic_css = "".join(s + "{display:none!important}"
                                   for s in self.generic_sel if s not in self.generic_exc)
        # only the finished CSS is needed from here on: drop the build-time copies
        self.generic_sel, self._seen, self.generic_exc = [], set(), set()
        # one newline-joined string per site instead of a list of separate string objects
        for d in list(self.site_sel):
            self.site_sel[d] = "\n".join(self.site_sel[d])
        _OPTS.clear()
        _OPTS[NOOPTS] = NOOPTS

    def site_css(self, host):
        out, h = [], host
        while h:
            blob = self.site_sel.get(h)
            if blob:
                out += [s + "{display:none!important}" for s in blob.split("\n")]
            h = h.partition(".")[2]
        return "".join(out)

    def should_block(self, url, host, first, rtype):
        url, host, first = url.lower(), host.lower(), first.lower()
        third = bool(first) and base_domain(host) != base_domain(first)
        if self.block.find(url, host, first, rtype, third):
            return not self.allow.find(url, host, first, rtype, third)
        return False


GENERIC_SCRIPT, SITE_SCRIPT = "fjord-adblock-generic", "fjord-adblock-site"
RT = QWebEngineUrlRequestInfo.ResourceType
RT_MAP = {getattr(RT, k): v for k, v in {
    "ResourceTypeSubFrame": "subdocument", "ResourceTypeStylesheet": "stylesheet", "ResourceTypeScript": "script",
    "ResourceTypeImage": "image", "ResourceTypeFontResource": "font", "ResourceTypeMedia": "media",
    "ResourceTypeObject": "object", "ResourceTypePluginResource": "object", "ResourceTypeXhr": "xmlhttprequest",
    "ResourceTypePing": "ping", "ResourceTypeWorker": "script", "ResourceTypeSharedWorker": "script",
    "ResourceTypeServiceWorker": "script"}.items() if hasattr(RT, k)}


def css_js(css, allow):
    """Hide-elements script. Uses a constructable stylesheet so page CSP can't block it."""
    return ("(function(){var A=%s,h=location.hostname.replace(/^www\\./,'');"
            "for(var i=0;i<A.length;i++){if(h===A[i]||h.endsWith('.'+A[i]))return;}"
            "var css=%s;try{var s=new CSSStyleSheet();s.replaceSync(css);"
            "document.adoptedStyleSheets=document.adoptedStyleSheets.concat([s]);}"
            "catch(e){var r=document.head||document.documentElement;if(r){var t=document.createElement('style');"
            "t.textContent=css;r.appendChild(t);}}})();" % (json.dumps(sorted(allow)), json.dumps(css)))


# YouTube serves its ads from its own domains, inside the same player data as the video, so URL filtering can't
# separate them. uBlock Origin handles this with "scriptlets" (+js(...) rules), which this engine can't run, so Fjord
# ships its own: strip the ad data out of the player JSON, skip any ad that still gets through, hide leftover ad UI.
YT_CSS = (
    "#masthead-ad,#player-ads,.ytp-ad-overlay-container,.ytp-ad-overlay-slot,.ytp-ad-image-overlay,"
    ".ytp-ad-text-overlay,ytd-ad-slot-renderer,ytd-in-feed-ad-layout-renderer,ytd-display-ad-renderer,"
    "ytd-promoted-sparkles-web-renderer,ytd-promoted-video-renderer,ytd-companion-slot-renderer,"
    "ytd-action-companion-ad-renderer,ytd-banner-promo-renderer,ytd-statement-banner-renderer,"
    "ytd-primetime-promo-renderer,ytd-player-legacy-desktop-watch-ads-renderer,ytd-brand-video-singleton-renderer"
    "{display:none!important}"
    # :has() rules go in their own rules so an older Chromium that lacks :has() only drops these, not the list above
    "ytd-rich-item-renderer:has(>#content>ytd-ad-slot-renderer){display:none!important}"
    "ytd-rich-section-renderer:has(ytd-statement-banner-renderer){display:none!important}"
    "ytd-rich-item-renderer:has(ytd-in-feed-ad-layout-renderer){display:none!important}"
)
YT_SCRIPT = "fjord-adblock-youtube"
YT_JS = r"""(function(){
var A=__ALLOW__,h=location.hostname.replace(/^www\./,'');
if(!/(^|\.)(youtube\.com|youtube-nocookie\.com)$/.test(h))return;
for(var i=0;i<A.length;i++){if(h===A[i]||h.endsWith('.'+A[i]))return;}
if(window.__fjordYT)return;
try{Object.defineProperty(window,'__fjordYT',{value:1});}catch(e){}

/* 1. remove the ad data from player / watch-page responses so the player never schedules an ad */
var KEYS=['adPlacements','playerAds','adSlots','adBreakHeartbeatParams'];
function prune(o){
  try{
    if(!o||typeof o!=='object')return o;
    if(Array.isArray(o)){if(o.length<=8)for(var i=0;i<o.length;i++)prune(o[i]);return o;}
    for(var k=0;k<KEYS.length;k++){if(KEYS[k] in o)delete o[KEYS[k]];}
    if(o.playerResponse&&typeof o.playerResponse==='object')prune(o.playerResponse);
    if(o.response&&typeof o.response==='object'&&!Array.isArray(o.response))prune(o.response);
  }catch(e){}
  return o;
}
try{var jp=JSON.parse;JSON.parse=function(){return prune(jp.apply(this,arguments));};}catch(e){}
try{
  if(window.Response&&Response.prototype.json){
    var rj=Response.prototype.json;
    Response.prototype.json=function(){return rj.apply(this,arguments).then(prune);};
  }
}catch(e){}
try{
  var ipr;
  Object.defineProperty(window,'ytInitialPlayerResponse',{configurable:true,enumerable:true,
    get:function(){return ipr;},set:function(v){ipr=prune(v);}});
}catch(e){}

/* 2. hide the ad UI that is left over */
try{
  var css=__CSS__,s=new CSSStyleSheet();s.replaceSync(css);
  document.adoptedStyleSheets=document.adoptedStyleSheets.concat([s]);
}catch(e){}

/* 3. safety net: if an ad still plays, skip it; also clear the "ad blockers are not allowed" wall */
var st={ad:false,rate:1,muted:false};
function q(sel){return document.querySelector(sel);}
function tick(){
  try{
    var p=q('.html5-video-player');
    var v=p&&(p.querySelector('video.html5-main-video')||p.querySelector('video'));
    var ad=!!(p&&p.classList.contains('ad-showing'));
    var sk=q('.ytp-skip-ad-button,.ytp-ad-skip-button,.ytp-ad-skip-button-modern');
    if(sk)sk.click();
    var cl=q('.ytp-ad-overlay-close-button');
    if(cl)cl.click();
    if(ad&&v){
      if(!st.ad){st.ad=true;st.rate=v.playbackRate||1;st.muted=v.muted;}
      v.muted=true;
      try{v.playbackRate=16;}catch(e){}
      var d=v.duration;
      if(isFinite(d)&&d>0&&v.currentTime<d-0.05)v.currentTime=d;
    }else if(st.ad){
      st.ad=false;
      if(v){try{v.playbackRate=st.rate;}catch(e){}v.muted=st.muted;}
    }
    var w=q('ytd-enforcement-message-view-model');
    if(w){
      var dlg=w.closest('tp-yt-paper-dialog')||w;dlg.remove();
      var bs=document.querySelectorAll('tp-yt-iron-overlay-backdrop');
      for(var i=0;i<bs.length;i++)bs[i].remove();
      if(document.body)document.body.style.overflow='';
      if(v&&v.paused)v.play();
    }
  }catch(e){}
}
setInterval(tick,250);
})();"""


def yt_js(allow):
    return YT_JS.replace("__ALLOW__", json.dumps(sorted(allow))).replace("__CSS__", json.dumps(YT_CSS))


class AdBlock(QObject):
    ready = pyqtSignal()

    def __init__(self, settings):
        super().__init__()
        self.enabled = bool(settings.get("adblock", True))
        self.allow = set(settings.get("adblock_allow", []))
        self.engine = None
        self.blocked = 0
        self.loading = False
        self.updated = 0.0

    def is_paused(self, host):
        h = host[4:] if host.startswith("www.") else host
        while h:
            if h in self.allow:
                return True
            h = h.partition(".")[2]
        return False

    def load_async(self, force=False):
        if self.loading:
            return
        self.loading = True
        threading.Thread(target=self._work, args=(force,), daemon=True).start()

    def _work(self, force):
        try:
            FILTER_DIR.mkdir(parents=True, exist_ok=True)
            eng, times = FilterEngine(), []
            for name, url in FILTER_LISTS:
                path = FILTER_DIR / (name + ".txt")
                if force or not path.exists() or time.time() - path.stat().st_mtime > 4 * 86400:
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 FjordBrowser"})
                        with urllib.request.urlopen(req, timeout=25) as r:
                            data = r.read()
                        if len(data) > 500:
                            path.write_bytes(data)
                    except Exception:
                        pass  # offline: fall back to the cached copy if there is one
                if path.exists():
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        eng.parse_lines(fh)
                    times.append(path.stat().st_mtime)
            eng.finalize()
            self.engine = eng if times else None
            self.updated = min(times) if times else 0.0
        except Exception:
            pass
        finally:
            self.loading = False
            self.ready.emit()

    @staticmethod
    def _set_script(page, name, source, subframes=False):
        col = page.scripts()
        for sc in col.find(name):
            col.remove(sc)
        if source:
            sc = QWebEngineScript()
            sc.setName(name)
            sc.setSourceCode(source)
            sc.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            sc.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            sc.setRunsOnSubFrames(subframes)
            col.insert(sc)

    def install_global(self, profile):
        """The generic hide-elements script (can be ~1 MB) and the YouTube script are identical for every page,
        so they are registered once on the profile instead of being copied into every tab."""
        e = self.engine
        on = self.enabled and e is not None and bool(e.generic_css)
        self._set_script(profile, GENERIC_SCRIPT, css_js(e.generic_css, self.allow) if on else "")
        self._set_script(profile, YT_SCRIPT, yt_js(self.allow) if self.enabled else "", subframes=True)  # also covers embeds

    def install(self, page):
        if not self.enabled:
            self._set_script(page, SITE_SCRIPT, "")

    def update_site(self, page, host):
        e = self.engine
        css = e.site_css(host.lower()) if (self.enabled and e is not None) else ""
        self._set_script(page, SITE_SCRIPT, css_js(css, self.allow) if css else "")


class AdInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, blocker):
        super().__init__()
        self.blocker = blocker

    def interceptRequest(self, info):
        try:
            b = self.blocker
            eng = b.engine
            if not b.enabled or eng is None:
                return
            rt = info.resourceType()
            if rt == RT.ResourceTypeMainFrame:
                return
            url = info.requestUrl()
            if url.scheme() not in ("http", "https") or url.host() in ("127.0.0.1", "localhost"):
                return
            first = info.firstPartyUrl().host()
            if b.is_paused(first):
                return
            if eng.should_block(url.toString(), url.host(), first, RT_MAP.get(rt, "other")):
                info.block(True)
                b.blocked += 1
        except Exception:
            pass


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # never print tracebacks for dropped connections


def _ver_tuple(v):
    nums = [int(n) for n in re.findall(r"\d+", str(v or ""))[:4]]
    return tuple(nums + [0] * (4 - len(nums)))


def _app_path():
    """The file that gets replaced on update: fjord.py when run as a script, the .exe when packaged."""
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()


def cleanup_old_update():
    """Remove the previous exe / backup left behind by an update."""
    try:
        old = _app_path().with_name(_app_path().name + ".old")
        if old.exists():
            old.unlink()
    except OSError:
        pass


class Updater(QObject):
    """Checks GitHub for a newer release, downloads it and swaps it in. Runs in a worker thread; results arrive as signals."""
    found = pyqtSignal(dict)      # a newer release exists
    uptodate = pyqtSignal()
    failed = pyqtSignal(str)
    installed = pyqtSignal(str)   # new version, ready to restart into

    def _get(self, url, timeout=20):
        req = urllib.request.Request(url, headers={"User-Agent": "FjordBrowser/" + APP_VERSION,
                                                   "Accept": "application/vnd.github+json"})
        return urllib.request.urlopen(req, timeout=timeout)

    def check(self):
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        try:
            if "/" not in GITHUB_REPO or GITHUB_REPO.startswith("your-username"):
                raise RuntimeError("Set GITHUB_REPO at the top of fjord.py to your repository (owner/repo).")
            with self._get("https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO) as r:
                rel = json.loads(r.read().decode("utf-8"))
            tag = str(rel.get("tag_name") or "")
            if _ver_tuple(tag) <= _ver_tuple(APP_VERSION):
                self.uptodate.emit()
                return
            assets = rel.get("assets") or []
            frozen = getattr(sys, "frozen", False)
            pick = None
            if frozen:  # packaged build: needs an .exe asset (Windows one-file build)
                pick = next((a for a in assets if str(a.get("name", "")).lower().endswith(".exe")), None)
                if not pick or sys.platform != "win32":
                    raise RuntimeError("v%s is out, but this build can't self-update. Download it from the releases page." % tag.lstrip("vV"))
            else:
                pick = next((a for a in assets if a.get("name") == UPDATE_ASSET), None)
            if pick:
                url, digest, name = pick.get("browser_download_url"), str(pick.get("digest") or ""), pick["name"]
            else:  # no asset attached: take the source file straight from the tagged commit
                url = "https://raw.githubusercontent.com/%s/%s/%s" % (GITHUB_REPO, quote(tag), UPDATE_ASSET)
                digest, name = "", UPDATE_ASSET
            self.found.emit({"version": tag.lstrip("vV"), "url": url, "digest": digest, "name": name,
                             "notes": str(rel.get("body") or "").strip()[:900], "page": rel.get("html_url", "")})
        except Exception as ex:
            self.failed.emit(str(ex) or ex.__class__.__name__)

    def install(self, info):
        threading.Thread(target=self._install, args=(info,), daemon=True).start()

    def _install(self, info):
        tmp = None
        try:
            target = _app_path()
            tmp = target.with_name(target.name + ".new")
            h, size = hashlib.sha256(), 0
            with self._get(info["url"], timeout=60) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 400 * 1024 * 1024:
                        raise RuntimeError("Download is unexpectedly large.")
                    h.update(chunk)
                    f.write(chunk)
            if size < 1024:
                raise RuntimeError("Downloaded file is empty.")
            want = info.get("digest", "")
            if want.startswith("sha256:") and want[7:].lower() != h.hexdigest():
                raise RuntimeError("Checksum mismatch, update cancelled.")
            if getattr(sys, "frozen", False):
                old = target.with_name(target.name + ".old")
                if old.exists():
                    old.unlink()
                os.replace(target, old)  # a running .exe can be renamed, just not overwritten
                try:
                    os.replace(tmp, target)
                except Exception:
                    os.replace(old, target)
                    raise
            else:
                code = tmp.read_bytes()
                compile(code, UPDATE_ASSET, "exec")  # never install a file that doesn't even parse
                if b"def main" not in code:
                    raise RuntimeError("Downloaded file doesn't look like Fjord.")
                shutil.copy2(target, target.with_name(target.name + ".bak"))
                os.replace(tmp, target)
            self.installed.emit(info["version"])
        except Exception as ex:
            try:
                if tmp and tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            self.failed.emit("Update failed: %s" % (str(ex) or ex.__class__.__name__))


# ----- importing bookmarks & history from other browsers -----
def _is_web(u):
    return isinstance(u, str) and u.lower().startswith(("http://", "https://"))


def _clean(t):
    return re.sub(r"\s+", " ", str(t or "")).strip()


def _chromium_roots():
    """(name, user-data folder, folder-is-itself-the-profile) for every Chromium-based browser we know about."""
    h = Path.home()
    if sys.platform == "win32":
        la = Path(os.environ.get("LOCALAPPDATA", h / "AppData" / "Local"))
        ra = Path(os.environ.get("APPDATA", h / "AppData" / "Roaming"))
        return [("Chrome", la / "Google/Chrome/User Data", False), ("Edge", la / "Microsoft/Edge/User Data", False),
                ("Brave", la / "BraveSoftware/Brave-Browser/User Data", False), ("Vivaldi", la / "Vivaldi/User Data", False),
                ("Chromium", la / "Chromium/User Data", False), ("Opera", ra / "Opera Software/Opera Stable", True),
                ("Opera GX", ra / "Opera Software/Opera GX Stable", True)]
    if sys.platform == "darwin":
        a = h / "Library/Application Support"
        return [("Chrome", a / "Google/Chrome", False), ("Edge", a / "Microsoft Edge", False),
                ("Brave", a / "BraveSoftware/Brave-Browser", False), ("Vivaldi", a / "Vivaldi", False),
                ("Chromium", a / "Chromium", False), ("Arc", a / "Arc/User Data", False),
                ("Opera", a / "com.operasoftware.Opera", True)]
    c = h / ".config"
    return [("Chrome", c / "google-chrome", False), ("Edge", c / "microsoft-edge", False),
            ("Brave", c / "BraveSoftware/Brave-Browser", False), ("Vivaldi", c / "vivaldi", False),
            ("Chromium", c / "chromium", False), ("Opera", c / "opera", True)]


def _firefox_roots():
    h = Path.home()
    if sys.platform == "win32":
        ra = Path(os.environ.get("APPDATA", h / "AppData" / "Roaming"))
        return [("Firefox", ra / "Mozilla/Firefox/Profiles"), ("LibreWolf", ra / "LibreWolf/Profiles"),
                ("Waterfox", ra / "Waterfox/Profiles"), ("Zen", ra / "zen/Profiles")]
    if sys.platform == "darwin":
        a = h / "Library/Application Support"
        return [("Firefox", a / "Firefox/Profiles"), ("LibreWolf", a / "LibreWolf/Profiles"),
                ("Waterfox", a / "Waterfox/Profiles"), ("Zen", a / "zen/Profiles")]
    return [("Firefox", h / ".mozilla/firefox"), ("Firefox", h / "snap/firefox/common/.mozilla/firefox"),
            ("Firefox", h / ".var/app/org.mozilla.firefox/.mozilla/firefox"), ("LibreWolf", h / ".librewolf"),
            ("Waterfox", h / ".waterfox"), ("Zen", h / ".zen")]


def detect_import_sources():
    """Every browser profile on this computer that Fjord can import from."""
    out = []
    for name, root, single in _chromium_roots():
        try:
            if not root.is_dir():
                continue
            names = {}
            try:
                ls = json.loads((root / "Local State").read_text("utf-8"))
                names = {k: v.get("name") for k, v in ls["profile"]["info_cache"].items()}
            except Exception:
                pass
            dirs = [root] if single else sorted(d for d in root.iterdir()
                                                if d.is_dir() and (d.name == "Default" or d.name.startswith("Profile ")))
            dirs = [d for d in dirs if (d / "Bookmarks").exists() or (d / "History").exists()]
            for d in dirs:
                label = name if (single or len(dirs) == 1) else "%s \u00b7 %s" % (name, names.get(d.name) or d.name)
                out.append({"name": label, "kind": "chromium", "path": str(d), "history": True})
        except OSError:
            continue
    found = {}
    for name, root in _firefox_roots():
        try:
            if root.is_dir():
                for d in sorted(root.iterdir()):
                    if d.is_dir() and (d / "places.sqlite").exists():
                        found.setdefault(name, []).append(d)
        except OSError:
            continue
    for name, dirs in found.items():
        for d in dirs:
            label = name if len(dirs) == 1 else "%s \u00b7 %s" % (name, re.sub(r"^[a-z0-9]{8}\.", "", d.name))
            out.append({"name": label, "kind": "firefox", "path": str(d), "history": True})
    if sys.platform == "darwin":
        sp = Path.home() / "Library/Safari/Bookmarks.plist"
        try:
            present = sp.exists()
        except OSError:
            present = True  # exists but macOS is blocking access; the import will explain how to allow it
        if present:
            out.append({"name": "Safari", "kind": "safari", "path": str(sp), "history": False})
    return out


def _sqlite_rows(db, sql, args=()):
    """Query a browser's database from a temporary copy, so a running browser's lock doesn't get in the way."""
    db = Path(db)
    tmpd = tempfile.mkdtemp(prefix="fjord_imp_")
    try:
        shutil.copy2(db, Path(tmpd) / db.name)
        for suffix in ("-wal", "-shm"):  # recent changes may still live in the write-ahead log
            try:
                if db.with_name(db.name + suffix).exists():
                    shutil.copy2(db.with_name(db.name + suffix), Path(tmpd) / (db.name + suffix))
            except OSError:
                pass
        con = sqlite3.connect(str(Path(tmpd) / db.name))
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def read_chromium_bookmarks(profile):
    data = json.loads((Path(profile) / "Bookmarks").read_text("utf-8"))
    out = []

    def walk(n, path):
        if not isinstance(n, dict):
            return
        if n.get("type") == "url":
            if _is_web(n.get("url")):
                out.append({"url": n["url"], "title": _clean(n.get("name")) or n["url"], "folder": " \u203a ".join(path)})
            return
        name = _clean(n.get("name"))
        sub_path = path + [name] if name else path
        for c in n.get("children") or []:
            walk(c, sub_path)
    for node in (data.get("roots") or {}).values():
        if isinstance(node, dict):
            walk(node, [])
    return out


def read_chromium_history(profile, limit=3000):
    rows = _sqlite_rows(Path(profile) / "History",
                        "SELECT url, title, last_visit_time FROM urls WHERE last_visit_time > 0 "
                        "ORDER BY last_visit_time DESC LIMIT ?", (limit,))
    return [{"url": u, "title": _clean(t), "t": lv / 1e6 - 11644473600} for u, t, lv in rows if _is_web(u)]


FF_FOLDERS = {"toolbar": "Bookmarks toolbar", "menu": "Bookmarks menu", "unfiled": "Other bookmarks", "mobile": "Mobile bookmarks"}


def read_firefox_bookmarks(profile):
    rows = _sqlite_rows(Path(profile) / "places.sqlite",
                        "SELECT p.url, b.title, p.title, f.title FROM moz_bookmarks b JOIN moz_places p ON p.id = b.fk "
                        "LEFT JOIN moz_bookmarks f ON f.id = b.parent WHERE b.type = 1")
    return [{"url": u, "title": _clean(bt or pt) or u, "folder": FF_FOLDERS.get(ft or "", _clean(ft))}
            for u, bt, pt, ft in rows if _is_web(u)]


def read_firefox_history(profile, limit=3000):
    rows = _sqlite_rows(Path(profile) / "places.sqlite",
                        "SELECT url, title, last_visit_date FROM moz_places WHERE last_visit_date IS NOT NULL "
                        "AND visit_count > 0 ORDER BY last_visit_date DESC LIMIT ?", (limit,))
    return [{"url": u, "title": _clean(t), "t": lv / 1e6} for u, t, lv in rows if _is_web(u)]


def read_safari_bookmarks(path):
    with open(path, "rb") as f:
        data = plistlib.load(f)
    out = []
    names = {"BookmarksBar": "Favorites", "BookmarksMenu": "Bookmarks menu"}

    def walk(n, trail):
        if not isinstance(n, dict):
            return
        kind = n.get("WebBookmarkType")
        if kind == "WebBookmarkTypeLeaf":
            u = n.get("URLString")
            if _is_web(u):
                out.append({"url": u, "title": _clean((n.get("URIDictionary") or {}).get("title")) or u,
                            "folder": " \u203a ".join(trail)})
        elif kind == "WebBookmarkTypeList":
            title = n.get("Title") or ""
            if title == "com.apple.ReadingList":
                return
            title = names.get(title, _clean(title))
            for c in n.get("Children") or []:
                walk(c, trail + [title] if title else trail)
    walk(data, [])
    return out


class _BmHTML(HTMLParser):
    """Reads the standard bookmarks .html file every browser can export."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.pending, self.mode, self.href, self.buf = [], [], "", None, None, ""

    def handle_starttag(self, tag, attrs):
        if tag == "h3":
            self.mode, self.buf = "h3", ""
        elif tag == "a":
            self.mode, self.buf, self.href = "a", "", dict(attrs).get("href")
        elif tag == "dl":
            self.stack.append(self.pending)
            self.pending = ""

    def handle_endtag(self, tag):
        if tag == "h3":
            self.pending, self.mode = _clean(self.buf), None
        elif tag == "a" and self.mode == "a":
            if _is_web(self.href):
                self.out.append({"url": self.href, "title": _clean(self.buf) or self.href,
                                 "folder": " \u203a ".join(x for x in self.stack if x)})
            self.mode = None
        elif tag == "dl" and self.stack:
            self.stack.pop()

    def handle_data(self, d):
        if self.mode:
            self.buf += d


def read_html_bookmarks(path):
    p = _BmHTML()
    p.feed(Path(path).read_bytes().decode("utf-8", "replace"))
    return p.out


class Importer(QObject):
    """Reads another browser's bookmarks (and history) in a worker thread and hands the result back as a signal."""
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def run(self, src, want_history):
        threading.Thread(target=self._run, args=(src, want_history), daemon=True).start()

    def _run(self, src, want_history):
        res = {"name": src["name"], "bookmarks": [], "history": [], "notes": []}
        kind, path = src["kind"], src["path"]
        try:
            if kind == "chromium":
                try:
                    res["bookmarks"] = read_chromium_bookmarks(path)
                except FileNotFoundError:
                    pass
                if want_history:
                    try:
                        res["history"] = read_chromium_history(path)
                    except Exception:
                        res["notes"].append("History couldn't be read. Close %s completely and try again." % src["name"].split(" \u00b7 ")[0])
            elif kind == "firefox":
                try:
                    res["bookmarks"] = read_firefox_bookmarks(path)
                except Exception:
                    raise RuntimeError("Couldn't read %s's data. Close it completely and try again." % src["name"].split(" \u00b7 ")[0])
                if want_history:
                    try:
                        res["history"] = read_firefox_history(path)
                    except Exception:
                        res["notes"].append("History couldn't be read.")
            elif kind == "safari":
                res["bookmarks"] = read_safari_bookmarks(path)
            elif kind == "html":
                res["bookmarks"] = read_html_bookmarks(path)
            self.done.emit(res)
        except PermissionError:
            if kind == "safari":
                self.failed.emit("macOS is blocking access to Safari's data. Give Fjord (or Terminal) Full Disk Access in "
                                 "System Settings \u203a Privacy & Security, then try again.")
            else:
                self.failed.emit("Fjord isn't allowed to read that file.")
        except Exception as ex:
            self.failed.emit(str(ex) or "Import failed.")


def import_html(sources):
    e = html.escape
    rows = ""
    for i, s in enumerate(sources):
        hist = ('<a class=bt href="fjord://import-run?i=%d&h=1">Bookmarks + history</a>' % i) if s.get("history") else ""
        rows += ('<div class=row><div>%s<small>%s</small></div><span class=btns><a class=bt href="fjord://import-run?i=%d&h=0">Bookmarks</a>%s</span></div>'
                 % (e(s["name"]), "Found on this computer", i, hist))
    if not rows:
        rows = "<div class=row><div>No other browsers found<small>Export a bookmarks file from your browser and import it below.</small></div></div>"
    css = themed(SETTINGS_CSS) + (".btns{display:flex;gap:8px;flex:none}.bt{padding:7px 14px;border-radius:99px;background:rgba(79,176,232,.2);"
                                  "font-size:13px;white-space:nowrap;color:inherit;text-decoration:none}.bt:hover{background:rgba(79,176,232,.36)}"
                                  ".note{opacity:.45;font-size:12px;margin:18px 4px}")
    return ("<!doctype html><meta charset=utf-8><meta name=color-scheme content=dark><title>Import</title><style>" + base_css() + css
            + "</style><main><h1>Import</h1><h2>From a browser</h2><div class=card>" + rows
            + '</div><h2>From a file</h2><div class=card><div class=row><div>Bookmarks file (.html)<small>Works with an export from any browser</small></div>'
            + '<span class=btns><a class=bt href="fjord://import-file">Choose file\u2026</a></span></div></div>'
            + '<p class=note>Importing adds to what you already have; nothing is replaced. Passwords and cookies aren\'t imported. '
              'Close the other browser first if history can\'t be read.</p></main>')


# ----- site time budgets -----
BUDGET_PAUSE_JS = "document.querySelectorAll('video,audio').forEach(function(m){try{m.pause()}catch(e){}})"
BUDGET_QUICK = ("youtube.com", "reddit.com", "x.com", "instagram.com", "tiktok.com", "facebook.com", "netflix.com", "twitch.tv")


def norm_site(s):
    """'https://www.YouTube.com/watch?v=1' -> 'youtube.com'; returns '' if it isn't a plausible site."""
    s = (s or "").strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", s)
    s = re.split(r"[/?#:]", s)[0]
    s = s[4:] if s.startswith("www.") else s
    return s if re.fullmatch(r"[a-z0-9\-]+(\.[a-z0-9\-]+)+", s) else ""


def os_idle_seconds():
    """Seconds since the last keyboard/mouse input. Only known on Windows; elsewhere 0 (= never idle)."""
    if sys.platform != "win32":
        return 0.0
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        li = LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(li)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
            return 0.0
        return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 0.0


class BudgetOverlay(QFrame):
    """Dims the page and asks what to do once a site's daily budget is used up. A gentle stop, not a hard block."""
    more = pyqtSignal()
    skip = pyqtSignal()
    close_tab = pyqtSignal()

    def __init__(self, parent, stack):
        super().__init__(parent)
        self.stack = stack
        self.key = ""
        self._texts = ("", "")
        self.setObjectName("budgetov")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QFrame()
        card.setObjectName("budgetcard")
        card.setFixedWidth(420)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(30, 28, 30, 24)
        cl.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("btitle")
        self.title.setWordWrap(True)
        self.sub = QLabel()
        self.sub.setObjectName("bsub")
        self.sub.setWordWrap(True)
        cl.addWidget(self.title)
        cl.addWidget(self.sub)
        cl.addSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, sig, name in (("Close tab", self.close_tab, "bprimary"), ("5 more minutes", self.more, ""),
                                ("Ignore today", self.skip, "")):
            b = QPushButton(text)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if name:
                b.setObjectName(name)
            b.clicked.connect(lambda _c=False, s=sig: s.emit())
            row.addWidget(b)
        cl.addLayout(row)
        lay.addWidget(card)
        self.setStyleSheet(
            "#budgetov{background:rgba(9,15,22,222)} #budgetcard{background:#132029;border:1px solid #243546;border-radius:22px}"
            "#btitle{color:#eaf3f9;font-size:19px;font-weight:300;background:transparent}"
            "#bsub{color:#8ea3b4;font-size:13px;background:transparent}"
            "#budgetov QPushButton{background:rgba(255,255,255,0.07);border:none;border-radius:12px;padding:9px 14px;color:#cfe0ec}"
            "#budgetov QPushButton:hover{background:rgba(255,255,255,0.13)}"
            "#budgetov QPushButton#bprimary{background:rgba(79,176,232,0.28);color:#e4f4fd}"
            "#budgetov QPushButton#bprimary:hover{background:rgba(79,176,232,0.42)}")
        self.hide()

    def place(self):
        p = self.parentWidget()
        if p is None or self.stack is None:
            return
        tl = self.stack.mapTo(p, QPoint(0, 0))
        self.setGeometry(tl.x(), tl.y(), self.stack.width(), self.stack.height())
        self.raise_()

    def show_for(self, key, used_min, limit_min):
        """Shows the overlay; returns True if it just appeared."""
        newly = not self.isVisible()
        self.key = key
        texts = ("You've reached your limit for %s" % key,
                 "%d minutes today, against a limit of %d. A few more minutes, or call it for now?" % (used_min, limit_min))
        if texts != self._texts:
            self._texts = texts
            self.title.setText(texts[0])
            self.sub.setText(texts[1])
        self.place()
        self.show()
        if newly:
            self.setFocus()
        return newly

    def mousePressEvent(self, e):
        e.accept()

    def wheelEvent(self, e):
        e.accept()

    def keyPressEvent(self, e):
        e.accept()


def budgets_html(b):
    e = html.escape
    bd = b.budgets
    rows = ""
    for k in sorted(bd["limits"]):
        lim = bd["limits"][k]
        used = bd["used"].get(k, 0) / 60.0
        extra = bd["extra"].get(k, 0) / 60.0
        total = lim + extra
        pct = min(100, used / total * 100) if total else 0
        note = "Ignored for today" if k in bd["off"] else ("Limit reached" if used >= total else "")
        txt = "%d of %d min used today" % (int(used), lim) + (" (+%d bonus)" % extra if extra else "") + (" \u00b7 " + note if note else "")
        q = quote(k, safe="")
        rows += ('<div class=row><div style="flex:1;min-width:0">%s<small>%s</small>'
                 '<div style="height:5px;border-radius:99px;background:rgba(255,255,255,.1);margin-top:9px;overflow:hidden">'
                 '<div style="width:%.0f%%;height:100%%;background:linear-gradient(135deg,#4fb0e8,#7ef0d0)"></div></div></div>'
                 '<span><a class=x href="fjord://budget-set?h=%s&d=-5">\u22125</a><a class=x href="fjord://budget-set?h=%s&d=5">+5</a>'
                 '<a class=x href="fjord://budget-remove?h=%s">Remove</a></span></div>' % (e(k), e(txt), pct, q, q, q))
    if not rows:
        rows = "<div class=row><div>No budgets yet<small>Add a site below to set a daily limit for it.</small></div></div>"
    sites = [s for s in ([b._b_suggest] if b._b_suggest else []) + list(BUDGET_QUICK) if s not in bd["limits"]]
    chips = "".join('<a class=chip href="fjord://budget-add?site=%s&min=30">+ %s%s</a>'
                    % (quote(s, safe=""), e(s), " (this site)" if s == b._b_suggest else "") for s in dict.fromkeys(sites))
    return ("<!doctype html><meta charset=utf-8><meta name=color-scheme content=dark><title>Site time budgets</title><style>"
            + base_css() + themed(SETTINGS_CSS)
            + "</style><main><h1>Site time budgets</h1><h2>Daily limits</h2><div class=card>" + rows + "</div>"
            + '<h2>Add a site</h2><div class=card><form class=pf action="fjord://budget-add"><div class=fr>'
              '<input name=site placeholder="Site, e.g. youtube.com"><input name=min placeholder="Minutes per day" '
              'style="max-width:170px" inputmode=numeric><button>Add</button></div></form>'
            + ('<div class=chips>%s</div>' % chips if chips else "") + "</div>"
            + '<p class=hint style="padding:18px 4px">Fjord counts the time a site is in front of you, plus any tab playing audio. '
              'At the limit it dims the page and pauses media; you can take 5 more minutes, ignore the limit for today, or close the tab. '
              'Counts reset at midnight and stay on this computer. Time only counts while you\'re active (idle detection works on Windows).</p></main>')


class BgServer(QObject):
    event = pyqtSignal(object)  # query dict sent by the new-tab page

    # Tiny 127.0.0.1-only file server for the background image/video. The unguessable token keeps other sites out.
    def __init__(self):
        super().__init__()
        self.token = secrets.token_urlsafe(9)
        self.port = 0

    def start(self):
        BG_DIR.mkdir(parents=True, exist_ok=True)
        token = self.token
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def handle(self):
                try:
                    super().handle()
                except (ConnectionError, OSError):  # the browser dropped an idle/finished connection: not an error
                    pass

            def do_HEAD(self):
                self._send(False)

            def do_GET(self):
                self._send(True)

            def _empty(self, code, extra=None):
                self.send_response(code)
                self.send_header("Content-Length", "0")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()

            def _send(self, body):
                try:
                    path = unquote(urlparse(self.path).path)
                    prefix = "/%s/" % token
                    if path == prefix + "_ev":
                        owner.event.emit(parse_qs(urlparse(self.path).query))
                        return self._empty(204, {"Access-Control-Allow-Origin": "*"})
                    f = BG_DIR / os.path.basename(path[len(prefix):]) if path.startswith(prefix) else None
                    if f is None or not f.is_file():
                        return self._empty(404)
                    size = f.stat().st_size
                    start, end, status = 0, size - 1, 200
                    m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", "").strip())
                    if m and (m.group(1) or m.group(2)):
                        if m.group(1):
                            start = int(m.group(1))
                            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                        else:
                            start, end = max(0, size - int(m.group(2))), size - 1
                        if start > end or start >= size:
                            return self._empty(416, {"Content-Range": "bytes */%d" % size})
                        status = 206
                    length = end - start + 1
                    self.send_response(status)
                    self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Length", str(length))
                    if status == 206:
                        self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                    self.send_header("Cache-Control", "max-age=3600")
                    self.end_headers()
                    if body:
                        with open(f, "rb") as fh:
                            fh.seek(start)
                            left = length
                            while left > 0:
                                chunk = fh.read(min(65536, left))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                left -= len(chunk)
                except (OSError, ValueError):
                    pass

        try:
            self.httpd = QuietServer(("127.0.0.1", 0), Handler)
            self.port = self.httpd.server_address[1]
            threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        except OSError:
            self.port = 0

    def url(self, name):
        return "http://127.0.0.1:%d/%s/%s" % (self.port, self.token, quote(name))

    def event_url(self):
        return "http://127.0.0.1:%d/%s/_ev" % (self.port, self.token)


# ---------- search engine logos + tab group colors ----------
ENGINE_BADGE = {"Google": ("#4285F4", "G"), "DuckDuckGo": ("#DE5833", "D"), "Bing": ("#008373", "b"),
                "Brave": ("#FB542B", "B")}
ENGINE_DOMAIN = {"Google": "google.com", "DuckDuckGo": "duckduckgo.com", "Bing": "bing.com",
                 "Brave": "search.brave.com"}
GROUP_COLORS = [("Blue", "#4fb0e8"), ("Teal", "#7ef0d0"), ("Pink", "#ff7a90"), ("Yellow", "#ffd166"),
                ("Green", "#7ee081"), ("Orange", "#ff9f6b")]


def badge_icon(letter, color):
    """Fallback logo: a colored disc with the engine's initial (used until the real logo is downloaded)."""
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(1, 1, 30, 30)
    f = QFont()
    f.setBold(True)
    f.setPixelSize(19)
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(QRectF(0, 0, 32, 32), Qt.AlignmentFlag.AlignCenter, letter)
    p.end()
    return QIcon(pm)


ENGINE_SVG = {
    "Google": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
               '<path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>'
               '<path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>'
               '<path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>'
               '<path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>'),
    "DuckDuckGo": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
                   '<circle cx="24" cy="24" r="23" fill="#DE5833"/>'
                   '<path fill="#fff" d="M24 8c-7.200 0-12.500 5.300-12.500 12.500 0 4.800 1.900 8.600 4.800 11.500 1.600 1.600 2.700 3.500 2.700 6h10c0-2.500 1.100-4.400 2.700-6 2.900-2.900 4.800-6.700 4.800-11.500C36.500 13.300 31.200 8 24 8z"/>'
                   '<circle cx="19" cy="19.500" r="3.200" fill="#2D4F8E"/><circle cx="29" cy="19.500" r="3.200" fill="#2D4F8E"/>'
                   '<circle cx="20" cy="18.500" r="1.100" fill="#fff"/><circle cx="30" cy="18.500" r="1.100" fill="#fff"/>'
                   '<path fill="#FDD20A" d="M24 22.500c3.300 0 5.500 1.600 5.500 3.800 0 2.600-2.400 4.700-5.500 4.700s-5.500-2.100-5.500-4.700c0-2.200 2.200-3.800 5.500-3.800z"/>'
                   '<path fill="#65BC46" d="M15.500 40c2.300 2.700 5.200 4 8.500 4s6.200-1.300 8.500-4c-1.500-1.200-2.600-2.300-3.300-3.500h-10.400c-.7 1.200-1.800 2.300-3.300 3.500z"/></svg>'),
    "Bing": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
             '<path fill="#008373" d="M10 2l9 3.200v28.500l12-6.800-5.800-2.700-3.700-9.300 21.500 7.600v10.500L19 46l-9-4.600z"/>'
             '<path fill="#00A99D" d="M19 33.700l12-6.800 11.200 4.200L19 46z" opacity=".55"/></svg>'),
    "Brave": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
              '<path fill="#FB542B" d="M24 2l7 .2L36.500.5 43 7l-2.500 6.500 2 6c.5 1.500.2 3-.8 4.300l-8 9.500L29 41l-5 4.500L19 41l-4.700-7.700-8-9.500c-1-1.300-1.300-2.800-.8-4.300l2-6L5 7 11.500.5 17 2.200z"/>'
              '<path fill="#fff" d="M24 13.500l5.500 1.500 5-.5-2.500 3.500 1.500 5-4 5-3 5.500L24 36l-3-3-3-5.500-4-5 1.500-5-2.500-3.500 5 .5z"/>'
              '<circle cx="19.800" cy="21.500" r="1.600" fill="#FB542B"/><circle cx="28.200" cy="21.500" r="1.600" fill="#FB542B"/>'
              '<path fill="#FB542B" d="M21 29h6l-3 4z"/></svg>'),
}


def engine_svg_pixmap(name, size=64):
    """Crisp, offline vector logo rendered to a pixmap (None if QtSvg or the logo is unavailable)."""
    svg = ENGINE_SVG.get(name)
    if not svg or QSvgRenderer is None:
        return None
    r = QSvgRenderer(svg.encode())
    if not r.isValid():
        return None
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def engine_png_bytes(name):
    """PNG bytes of the best available logo: downloaded hi-res > embedded vector > None."""
    path = ICON_DIR / ("engine-hq-%s.png" % name)
    if path.exists():
        try:
            return path.read_bytes()
        except OSError:
            pass
    pm = engine_svg_pixmap(name)
    if pm is not None:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        return bytes(ba)
    return None


def engine_icon(name):
    path = ICON_DIR / ("engine-hq-%s.png" % name)
    if path.exists():
        pm = QPixmap(str(path))
        if not pm.isNull():
            return QIcon(pm)
    pm = engine_svg_pixmap(name)
    if pm is not None:
        return QIcon(pm)
    color, letter = ENGINE_BADGE.get(name, ("#4fb0e8", name[:1]))
    return badge_icon(letter, color)


class IconFetcher(QObject):
    """Downloads each search engine's real logo once and caches it in ~/.fjord_browser/icons."""
    ready = pyqtSignal()

    def fetch(self):
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        got = False
        try:
            ICON_DIR.mkdir(parents=True, exist_ok=True)
            for name, dom in ENGINE_DOMAIN.items():
                path = ICON_DIR / ("engine-hq-%s.png" % name)
                if path.exists():
                    continue
                try:
                    req = urllib.request.Request("https://www.google.com/s2/favicons?domain=%s&sz=128" % dom,
                                                 headers={"User-Agent": "Mozilla/5.0 FjordBrowser"})
                    with urllib.request.urlopen(req, timeout=15) as r:
                        data = r.read()
                    if data[:4] == b"\x89PNG" and len(data) > 1500:  # skip the tiny generic globe placeholder
                        path.write_bytes(data)
                        got = True
                except Exception:
                    pass
        finally:
            if got:
                self.ready.emit()


# ---------- widgets ----------
# ----- sticky notes -----
# A small note button sits at the bottom right of every web page. Notes are saved per site (by host name) in
# ~/.fjord_browser/notes.json, so they are still there next time you visit, even weeks later.
NOTES_SCRIPT = "fjord-notes"
NOTES_MSG = "__FJORD_NOTES__"
NOTES_GET = "__FJORD_NOTES_GET__"
NOTE_COLORS = ["#ffe45e", "#ffb067", "#ff9ec4", "#c4a5ff", "#7fd1ff", "#8ee59a"]
OLD_NOTE_COLORS = ["#fff176", "#ffab91", "#a5d6a7", "#81d4fa", "#ce93d8", "#f8bbd0",   # earlier palettes, still accepted
                   "#fff3a3", "#bfe3ff", "#c6f0c2", "#ffc9dc", "#dcc8ff", "#e5e5ea"]
NOTES_MAX = 60
NOTES_JS = r"""(function(){
if(window.top!==window||window.__fjNotes||!/^https?:$/.test(location.protocol))return;window.__fjNotes=1;
var log=console.log.bind(console),PFX="__FJORD_NOTES__",notes=[],COLORS=__COLORS__,timer=null,layer,root,rootEl,loaded=false;
function save(now){clearTimeout(timer);var go=function(){try{log(PFX+JSON.stringify(notes));}catch(e){}};if(now)go();else timer=setTimeout(go,350);}
function uid(){return Date.now().toString(36)+Math.random().toString(36).slice(2,6);}
/* Fjord forces dark mode on pages, which would also darken the notes. FIX undoes that for the note colours, so they show
   as bright as authored. Pages that are already dark opt out of the forcing, so they get no correction. */
var FIX="invert(1) hue-rotate(180deg) brightness(1.08) saturate(1.2)",FOLD=32;
function nativeDark(){try{var m=document.querySelector('meta[name="color-scheme"]');if(m&&/dark/i.test(m.content))return true;
 var cs=getComputedStyle(document.documentElement).colorScheme;return !!cs&&/dark/i.test(cs);}catch(e){return false;}}
var CSS=":host{all:initial}*{box-sizing:border-box;font-family:system-ui,'Segoe UI',sans-serif}"+
".add{position:fixed;right:32px;bottom:32px;width:60px;height:60px;border-radius:50%;border:0;cursor:pointer;"+
"background-image:linear-gradient(#ffe45e,#ffe45e);color:#3a3000;padding:0;display:flex;align-items:center;justify-content:center;"+
"filter:"+FIX+" drop-shadow(0 6px 10px rgba(0,0,0,.4));transition:transform .15s}"+
".add svg{width:32px;height:32px;display:block}"+
".add:hover{transform:scale(1.1) rotate(-6deg)}"+
".plain .add{filter:drop-shadow(0 6px 10px rgba(0,0,0,.4))}"+
".note{position:absolute;filter:drop-shadow(0 14px 16px rgba(0,0,0,.26)) drop-shadow(0 2px 3px rgba(0,0,0,.2))}"+
".face{position:absolute;left:0;top:0;right:0;bottom:0;border-radius:20px;display:flex;flex-direction:column;overflow:hidden;color:#1d1d1f;"+
"filter:"+FIX+";clip-path:polygon(0 0,100% 0,100% calc(100% - "+FOLD+"px),calc(100% - "+FOLD+"px) 100%,0 100%);"+
"font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',system-ui,sans-serif}"+
".plain .face{filter:none}"+
".flap{position:absolute;right:0;bottom:0;width:"+FOLD+"px;height:"+FOLD+"px;cursor:nwse-resize;touch-action:none;border-radius:0 0 0 6px;"+
"clip-path:polygon(0 0,100% 0,0 100%);background-image:linear-gradient(135deg,rgba(255,255,255,.14),rgba(255,255,255,.55) 55%,rgba(0,0,0,.2))}"+
".bar{display:flex;align-items:center;gap:9px;padding:11px 14px 9px;cursor:grab;touch-action:none;user-select:none;background-image:linear-gradient(rgba(0,0,0,.045),rgba(0,0,0,.045))}"+
".bar:active{cursor:grabbing}.sp{flex:1}"+
".dot{width:20px;height:20px;flex:none;border-radius:50%;border:2px solid rgba(0,0,0,.14);padding:0;cursor:pointer;transition:transform .12s}"+
".dot:hover{transform:scale(1.15)}.dot.on{border-color:#1d1d1f}"+
".x{flex:none;width:22px;height:22px;border:0;border-radius:50%;background-image:linear-gradient(rgba(0,0,0,.08),rgba(0,0,0,.08));color:#3a3a3c;font-size:13px;line-height:22px;text-align:center;cursor:pointer;padding:0}"+
".x:hover{background-image:linear-gradient(rgba(0,0,0,.18),rgba(0,0,0,.18))}"+
"textarea{flex:1;width:100%;border:0;outline:0;resize:none;background:none;padding:10px 18px 24px;font-size:15px;line-height:1.45;color:#1d1d1f;font-family:inherit}"+
"textarea::placeholder{color:rgba(0,0,0,.4)}";
function paint(el,c){el.style.backgroundImage="linear-gradient("+c+","+c+")";}
function build(n){
 var el=document.createElement("div");el.className="note";
 el.style.left=n.x+"px";el.style.top=n.y+"px";el.style.width=n.w+"px";el.style.height=n.h+"px";
 var face=document.createElement("div");face.className="face";paint(face,n.color);
 var bar=document.createElement("div");bar.className="bar";
 var dots=[];
 COLORS.forEach(function(c){var d=document.createElement("button");d.className="dot"+(c===n.color?" on":"");d.title="Colour";paint(d,c);
  d.addEventListener("click",function(){n.color=c;paint(face,c);dots.forEach(function(o){o.classList.toggle("on",o===d);});save();});
  dots.push(d);bar.appendChild(d);});
 var sp=document.createElement("span");sp.className="sp";bar.appendChild(sp);
 var x=document.createElement("button");x.className="x";x.textContent="\u2715";x.title="Delete note";
 x.addEventListener("click",function(){if(n.text&&n.text.trim()&&!confirm("Delete this note?"))return;
  notes=notes.filter(function(o){return o!==n;});el.remove();save(true);});
 bar.appendChild(x);
 var ta=document.createElement("textarea");ta.value=n.text||"";ta.placeholder="Write something\u2026";ta.spellcheck=true;
 ["keydown","keyup","keypress"].forEach(function(t){ta.addEventListener(t,function(e){e.stopPropagation();});});
 ta.addEventListener("input",function(){n.text=ta.value;save();});
 ta.addEventListener("blur",function(){save(true);});
 var flap=document.createElement("div");flap.className="flap";flap.title="Drag to resize";
 face.appendChild(bar);face.appendChild(ta);face.appendChild(flap);el.appendChild(face);
 var sx,sy,ox,oy,drag=false;
 bar.addEventListener("pointerdown",function(e){if(e.target.closest(".dot,.x"))return;drag=true;sx=e.pageX;sy=e.pageY;ox=n.x;oy=n.y;bar.setPointerCapture(e.pointerId);e.preventDefault();});
 bar.addEventListener("pointermove",function(e){if(!drag)return;n.x=Math.max(0,Math.round(ox+e.pageX-sx));n.y=Math.max(0,Math.round(oy+e.pageY-sy));el.style.left=n.x+"px";el.style.top=n.y+"px";});
 bar.addEventListener("pointerup",function(){if(drag){drag=false;save(true);}});
 var rs=false,rw,rh;
 flap.addEventListener("pointerdown",function(e){rs=true;sx=e.pageX;sy=e.pageY;rw=n.w;rh=n.h;flap.setPointerCapture(e.pointerId);e.preventDefault();});
 flap.addEventListener("pointermove",function(e){if(!rs)return;n.w=Math.min(1200,Math.max(210,Math.round(rw+e.pageX-sx)));n.h=Math.min(1200,Math.max(150,Math.round(rh+e.pageY-sy)));el.style.width=n.w+"px";el.style.height=n.h+"px";});
 flap.addEventListener("pointerup",function(){if(rs){rs=false;save(true);}});
 layer.appendChild(el);return ta;
}
function init(){
 if(!document.documentElement)return;
 var host=document.createElement("div");
 host.style.cssText="all:initial;position:absolute;top:0;left:0;width:0;height:0;z-index:2147483647";
 root=host.attachShadow({mode:"open"});
 var st=document.createElement("style");st.textContent=CSS;root.appendChild(st);
 rootEl=document.createElement("div");rootEl.className=nativeDark()?"plain":"";root.appendChild(rootEl);
 layer=document.createElement("div");rootEl.appendChild(layer);
 var b=document.createElement("button");b.className="add";
 var NS="http://www.w3.org/2000/svg",sv=document.createElementNS(NS,"svg");
 [["viewBox","0 0 24 24"],["fill","none"],["stroke","currentColor"],["stroke-width","1.9"],["stroke-linecap","round"],["stroke-linejoin","round"]].forEach(function(a){sv.setAttribute(a[0],a[1]);});
 ["M4 4h16v10l-6 6H4z","M14 20v-6h6","M8 9h8M8 13h3"].forEach(function(d){var p=document.createElementNS(NS,"path");p.setAttribute("d",d);sv.appendChild(p);});
 b.appendChild(sv);b.title="Add a sticky note";
 b.addEventListener("click",function(){
  var k=notes.length%6*18;
  var n={id:uid(),text:"",color:COLORS[0],x:Math.max(0,Math.round(window.scrollX+window.innerWidth-300-k)),y:Math.max(0,Math.round(window.scrollY+window.innerHeight-320-k)),w:250,h:210};
  notes.push(n);build(n).focus();save(true);});
 rootEl.appendChild(b);
 notes.forEach(build);
 window.__fjNotesLoad=function(arr){
  if(loaded||!Array.isArray(arr))return;loaded=true;
  var local=notes;notes=arr.concat(local);layer.textContent="";notes.forEach(build);};
 document.documentElement.appendChild(host);
 try{log("__FJORD_NOTES_GET__");}catch(e){}
 setInterval(function(){if(document.documentElement&&host.parentNode!==document.documentElement)document.documentElement.appendChild(host);
  rootEl.className=nativeDark()?"plain":"";},2000);
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init);else init();
})();"""


# ---------- browser extensions: Chrome, Firefox and Safari ----------
# Fjord runs on Chromium, so Chrome extensions load as they are. Firefox add-ons (.xpi) and Safari Web Extensions use the
# same WebExtensions format. They are unpacked into ~/.fjord_browser/extensions and adjusted a little (adapt_manifest) so
# Chromium accepts them, and a small script gives them Firefox's promise-based `browser.*` API. Qt only gained extension
# loading in WebEngine 6.10, so everything that touches Qt is feature-checked and Fjord still runs without it.
EXT_DIR = DATA_DIR / "extensions"
EXT_SHIM = "fjord-webext-shim.js"
EXT_MAX_BYTES = 400 * 1024 * 1024
EXT_KINDS = {"chrome": "Chrome", "firefox": "Firefox", "safari": "Safari"}
EXT_FIREFOX_KEYS = ("browser_specific_settings", "applications", "sidebar_action", "chrome_settings_overrides",
                    "protocol_handlers", "theme_experiment", "user_scripts", "dictionaries", "langpack_id", "experiment_apis")
EXT_FIREFOX_PERMS = {"contextualIdentities", "browserSettings", "captivePortal", "dns", "find", "geckoProfiler",
                     "menus.overrideContext", "theme", "telemetry", "trialML", "activityLog", "pkcs11",
                     "webRequestFilterResponse", "webRequestFilterResponse.serviceWorkerScript"}
EXT_HOST_RE = re.compile(r"^(<all_urls>|(\*|https?|wss?|ftp|file)://)")
EXT_ALL_SITES_RE = re.compile(r"^(<all_urls>|(\*|https?|wss?|ftp)://\*/.*)$")
EXT_PERM_TEXT = {
    "tabs": "See the address and title of your open tabs",
    "history": "Read and change your browsing history",
    "bookmarks": "Read and change your bookmarks",
    "cookies": "Read and change cookies",
    "downloads": "Start and manage downloads",
    "clipboardRead": "Read what you copied",
    "clipboardWrite": "Change what you copied",
    "webRequest": "See the network requests pages make",
    "webRequestBlocking": "Block or change network requests",
    "declarativeNetRequest": "Block network requests",
    "nativeMessaging": "Talk to apps on your computer (not supported in Fjord)",
    "management": "See and manage your other extensions",
    "privacy": "Change privacy settings",
    "proxy": "Control proxy settings",
    "geolocation": "Know your location",
    "notifications": "Show notifications",
    "scripting": "Run scripts on pages it can access",
    "topSites": "See your most visited sites",
    "sessions": "See your recently closed tabs",
    "browsingData": "Clear your browsing data",
}

# Gives Firefox-style code a `browser` object. In Manifest V2 it also turns callback APIs into promises (what Firefox does
# natively); in Manifest V3 Chromium already returns promises, so it only adds the alias. Always lets a runtime.onMessage
# listener answer by returning a promise, as Firefox allows.
EXT_SHIM_JS = r"""(function(g){
var c=g.chrome;
if(!c||!c.runtime||g.__fjShim)return;
try{Object.defineProperty(g,"__fjShim",{value:1});}catch(e){}
var mv=2;try{mv=c.runtime.getManifest().manifest_version||2;}catch(e){}
var SYNC={"runtime.getURL":1,"runtime.getManifest":1,"runtime.connect":1,"runtime.connectNative":1,"runtime.reload":1,
"extension.getURL":1,"extension.getViews":1,"extension.getBackgroundPage":1,"i18n.getMessage":1,"i18n.getUILanguage":1,
"tabs.connect":1,"contextMenus.create":1,"menus.create":1,"alarms.create":1,"identity.getRedirectURL":1};
var ALIAS={menus:"contextMenus",browserAction:"action",action:"browserAction"};
var wraps={},evts={};
function evt(ev,path){
  if(path!=="runtime.onMessage")return ev;
  if(evts[path])return evts[path];
  var map=new WeakMap();
  return evts[path]=new Proxy(ev,{get:function(t,k){
    if(k==="addListener")return function(fn){
      var w=function(m,s,send){var r=fn(m,s,send);
        if(r&&typeof r.then==="function"){r.then(function(v){send(v);},function(){send();});return true;}
        return r;};
      map.set(fn,w);return t.addListener.apply(t,[w].concat([].slice.call(arguments,1)));};
    if(k==="removeListener")return function(fn){return t.removeListener(map.get(fn)||fn);};
    if(k==="hasListener")return function(fn){return t.hasListener(map.get(fn)||fn);};
    var v=t[k];return typeof v==="function"?v.bind(t):v;}});
}
function wrap(obj,path){
  if(wraps[path])return wraps[path];
  return wraps[path]=new Proxy(obj,{get:function(t,k){
    if(typeof k!=="string")return t[k];
    var v=t[k];
    if(v===undefined&&!path&&ALIAS[k]&&t[ALIAS[k]]!==undefined)v=t[ALIAS[k]];
    var p=path?path+"."+k:k;
    if(typeof v==="function"){
      if(mv>=3||SYNC[p])return v.bind(t);
      return function(){var a=[].slice.call(arguments);
        if(typeof a[a.length-1]==="function")return v.apply(t,a);
        return new Promise(function(res,rej){
          a.push(function(r){var e=c.runtime.lastError;if(e)rej(new Error(e.message));else res(r);});
          try{v.apply(t,a);}catch(x){rej(x);}});};
    }
    if(v&&typeof v==="object"&&!Array.isArray(v)){
      if(typeof v.addListener==="function")return evt(v,p);
      return wrap(v,p);
    }
    return v;}});
}
if(typeof g.browser==="undefined")g.browser=wrap(c,"");
})(typeof globalThis!=="undefined"?globalThis:self);
"""


class ExtError(Exception):
    """A problem with an extension file, worded so it can be shown to the person as it is."""


def _strip_json_comments(s):
    """Chrome and Firefox both accept // and /* */ comments in manifest.json, which json.loads does not."""
    out, i, n, in_str = [], 0, len(s), False
    while i < n:
        ch = s[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(s[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif s.startswith("//", i):
            while i < n and s[i] != "\n":
                i += 1
            continue
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def load_manifest(path):
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    try:
        m = json.loads(text)
    except ValueError:
        m = json.loads(_strip_json_comments(text))
    if not isinstance(m, dict):
        raise ValueError("manifest is not an object")
    return m


def ext_label(root, m):
    """The extension's name, looking it up in _locales when the manifest only holds a __MSG_name__ placeholder."""
    name = str(m.get("name") or "Extension")
    mt = re.fullmatch(r"__MSG_(.+)__", name)
    if not mt:
        return name
    for loc in (m.get("default_locale"), "en", "en_US", "en_GB"):
        if not loc:
            continue
        try:
            msgs = load_manifest(Path(root) / "_locales" / str(loc) / "messages.json")
        except Exception:
            continue
        for k, v in msgs.items():
            if k.lower() == mt.group(1).lower() and isinstance(v, dict) and v.get("message"):
                return str(v["message"])
    return mt.group(1)


def _rel(p):
    """A path from a manifest as a clean relative posix path, or "" when it would leave the extension folder."""
    if not isinstance(p, str) or not p.strip():
        return ""
    r = posixpath.normpath(p.strip().lstrip("/"))
    return "" if r.startswith("..") or r == "." else r


def safe_extract(zf, dest):
    dest = Path(dest).resolve()
    infos = zf.infolist()
    if len(infos) > 30000:
        raise ExtError("That archive has too many files to be an extension.")
    total = 0
    for zi in infos:
        total += zi.file_size
        if total > EXT_MAX_BYTES:
            raise ExtError("That archive is too large to be an extension.")
        target = (dest / zi.filename).resolve()
        if target != dest and dest not in target.parents:
            raise ExtError("That archive contains an unsafe file path, so Fjord won't open it.")
    zf.extractall(dest)


def crx_zip_bytes(data):
    """A .crx is a zip with a signed header in front. Returns just the zip part (plain zips pass through)."""
    if data[:4] != b"Cr24":
        return data
    try:
        ver = struct.unpack("<I", data[4:8])[0]
        if ver == 2:
            pk, sg = struct.unpack("<II", data[8:16])
            return data[16 + pk + sg:]
        if ver == 3:
            return data[12 + struct.unpack("<I", data[8:12])[0]:]
    except struct.error:
        pass
    raise ExtError("That .crx file is damaged or uses a format Fjord doesn't know.")


def find_manifest_dir(base):
    """The folder holding manifest.json: the folder itself, or inside a Safari .app / .appex (or an .ipa's Payload)."""
    base = Path(base)
    if (base / "manifest.json").is_file():
        return base
    for pat in ("Contents/PlugIns/*.appex/Contents/Resources", "Contents/Resources", "*.appex/Contents/Resources",
                "*.app/Contents/PlugIns/*.appex/Contents/Resources", "PlugIns/*.appex", "Payload/*.app/PlugIns/*.appex",
                "*.appex", "Resources"):
        for d in sorted(base.glob(pat)):
            if (d / "manifest.json").is_file():
                return d
    best = None  # last resort: a shallow search, preferring anything inside an .appex
    base_depth = len(base.parts)
    for dirpath, dirnames, filenames in os.walk(base):
        depth = len(Path(dirpath).parts) - base_depth
        if depth >= 7:
            dirnames[:] = []
        if "manifest.json" not in filenames:
            continue
        try:
            m = load_manifest(Path(dirpath) / "manifest.json")
        except Exception:
            continue
        if "manifest_version" in m and "name" in m:
            score = (0 if ".appex" in dirpath else 1, depth)
            if best is None or score < best[0]:
                best = (score, Path(dirpath))
    return best[1] if best else None


def detect_kind(src, root, m):
    low = (str(src) + "|" + str(root)).lower()
    bss = m.get("browser_specific_settings") or m.get("applications") or {}
    bss = bss if isinstance(bss, dict) else {}
    if ".appex" in low or "safari" in bss or str(src).lower().endswith(".app"):
        return "safari"
    if str(src).lower().endswith(".xpi") or "gecko" in bss or "gecko_android" in bss:
        return "firefox"
    return "chrome"


def adapt_manifest(m, root):
    """Make a Firefox or Safari manifest something Chromium accepts, and give its code the `browser` API. Returns notes."""
    root, notes = Path(root), []
    for k in EXT_FIREFOX_KEYS:
        m.pop(k, None)
    for k in ("options_ui", "browser_action", "action", "page_action"):
        if isinstance(m.get(k), dict):
            for junk in ("browser_style", "default_area", "theme_icons"):
                m[k].pop(junk, None)
    mv = m.get("manifest_version", 2)
    mv = mv if isinstance(mv, int) else 2

    def clean_perms(key):
        out = []
        for p in m.get(key) or []:
            p = "contextMenus" if p == "menus" else p
            if isinstance(p, str) and p not in EXT_FIREFOX_PERMS and p not in out:
                out.append(p)
        return out
    perms, optional = clean_perms("permissions"), clean_perms("optional_permissions")
    if "nativeMessaging" in perms or "nativeMessaging" in optional:
        notes.append("It uses native messaging (talking to an app on your computer), which Fjord can't do, so some features won't work.")
    if mv >= 3:  # Manifest V3 keeps website access in host_permissions
        for src_key, host_key, plist in (("permissions", "host_permissions", perms),
                                         ("optional_permissions", "optional_host_permissions", optional)):
            hosts = [p for p in plist if EXT_HOST_RE.match(p)]
            if hosts:
                plist[:] = [p for p in plist if p not in hosts]
                have = m.get(host_key) if isinstance(m.get(host_key), list) else []
                m[host_key] = have + [h for h in hosts if h not in have]
    if "permissions" in m or perms:
        m["permissions"] = perms
    if "optional_permissions" in m or optional:
        m["optional_permissions"] = optional

    bg = m.get("background")
    if isinstance(bg, dict):
        scripts = [_rel(s) for s in bg.get("scripts") or [] if _rel(s)]
        sw = _rel(bg.get("service_worker"))
        module = bg.get("type") == "module"
        if mv >= 3:
            if sw:  # run the original worker after the shim, from a wrapper beside it so relative importScripts() still works
                d = posixpath.dirname(sw)
                wrapper = posixpath.join(d, "fjord-sw.js")
                body = ("import '/%s';\nimport './%s';\n" if module else "importScripts('/%s', './%s');\n") % (EXT_SHIM, posixpath.basename(sw))
                (root / wrapper).write_text(body, encoding="utf-8")
                bg.pop("scripts", None)
                bg["service_worker"] = wrapper
            elif scripts:  # Firefox event pages become a service worker that loads the same scripts in order
                if module:
                    body = "import '/%s';\n" % EXT_SHIM + "".join("import '/%s';\n" % s for s in scripts)
                else:
                    body = "importScripts(%s);\n" % ", ".join(json.dumps("/" + s) for s in [EXT_SHIM] + scripts)
                (root / "fjord-sw.js").write_text(body, encoding="utf-8")
                m["background"] = dict({"service_worker": "fjord-sw.js"}, **({"type": "module"} if module else {}))
                notes.append("It was written for a Firefox background page. Fjord runs it as a service worker, so parts that use the page itself may not work.")
        else:
            bg.pop("service_worker", None)
            if scripts:
                bg["scripts"] = [EXT_SHIM] + scripts
    if mv < 3 and isinstance(m.get("action"), dict) and "browser_action" not in m:
        m["browser_action"] = m.pop("action")
    for cs in m.get("content_scripts") or []:
        if isinstance(cs, dict) and isinstance(cs.get("js"), list) and cs["js"] and cs.get("world") != "MAIN":
            cs["js"] = [EXT_SHIM] + cs["js"]
    war = m.get("web_accessible_resources")
    if mv >= 3 and isinstance(war, list) and any(isinstance(x, str) for x in war):  # V3 wants {resources, matches} objects
        m["web_accessible_resources"] = [x for x in war if isinstance(x, dict)] + [
            {"resources": [x for x in war if isinstance(x, str)], "matches": ["<all_urls>"]}]
    if mv >= 3 and isinstance(m.get("content_security_policy"), str):
        m["content_security_policy"] = {"extension_pages": m["content_security_policy"]}

    (root / EXT_SHIM).write_text(EXT_SHIM_JS, encoding="utf-8")
    tag, count = '<script src="/%s"></script>' % EXT_SHIM, 0
    for f in sorted(root.rglob("*.htm*")):  # popups, options pages and background pages need the shim too
        if count >= 300 or not f.is_file() or f.stat().st_size > 3_000_000:
            continue
        try:
            s = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if EXT_SHIM in s:
            continue
        mt = re.search(r"<head(?:\s[^>]*)?>", s, re.I) or re.search(r"<html(?:\s[^>]*)?>", s, re.I)
        s = (s[:mt.end()] + tag + s[mt.end():]) if mt else tag + s
        f.write_text(s, encoding="utf-8")
        count += 1
    return notes


def describe_permissions(m):
    """Plain-language list of what an extension can do, for the confirmation dialog."""
    perms = [p for p in (m.get("permissions") or []) if isinstance(p, str)]
    hosts = [p for p in (m.get("host_permissions") or []) if isinstance(p, str)] + [p for p in perms if EXT_HOST_RE.match(p)]
    for cs in m.get("content_scripts") or []:
        if isinstance(cs, dict):
            hosts += [x for x in cs.get("matches") or [] if isinstance(x, str)]
    out = []
    if any(EXT_ALL_SITES_RE.match(h) for h in hosts):
        out.append("Read and change your data on all websites")
    elif hosts:
        n = len(set(hosts))
        out.append("Read and change your data on %d site%s" % (n, "" if n == 1 else "s"))
    for p in perms:
        if p in EXT_PERM_TEXT:
            out.append(EXT_PERM_TEXT[p])
    return out


def pick_icon(root, m):
    icons = m.get("icons")
    if not isinstance(icons, dict):
        a = m.get("action") or m.get("browser_action") or {}
        icons = a.get("default_icon") if isinstance(a, dict) else None
        icons = {"48": icons} if isinstance(icons, str) else icons
    best = None
    for k, v in (icons.items() if isinstance(icons, dict) else []):
        rel = _rel(v)
        try:
            n = int(k)
        except (TypeError, ValueError):
            continue
        if rel.lower().endswith(".png") and (Path(root) / rel).is_file() and (best is None or abs(n - 64) < best[0]):
            best = (abs(n - 64), rel)
    return best[1] if best else ""


def stage_extension(src):
    """Unpack an extension (.crx, .xpi, .zip, .ipa, a folder, or a Safari .app/.appex) into EXT_DIR, converting Firefox and
    Safari ones for Chromium. Returns a record describing it. Raises ExtError with a message fit to show."""
    src = Path(src).expanduser()
    low = src.name.lower()
    if not src.exists():
        raise ExtError("Fjord can't find that file.")
    if low.endswith(".safariextz"):
        raise ExtError("That is an old-style Safari extension (.safariextz). Apple retired them in 2018, so they can't run "
                       "outside Safari. Safari Web Extensions work: pick the Mac app that contains the extension.")
    if low.endswith((".dmg", ".pkg")):
        raise ExtError("Fjord can't open disk images or installers. Open it first, then pick the .app inside.")
    EXT_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="stage-", dir=str(EXT_DIR)))
    try:
        if src.is_dir():
            base = src
        else:
            if src.stat().st_size > EXT_MAX_BYTES:
                raise ExtError("That file is too large to be an extension.")
            try:
                zf = zipfile.ZipFile(io.BytesIO(crx_zip_bytes(src.read_bytes())))
            except zipfile.BadZipFile:
                raise ExtError("That file isn't an extension package. Fjord opens .crx, .xpi and .zip files, folders, and Safari apps.")
            with zf:
                safe_extract(zf, work / "src")
            base = work / "src"
        root = find_manifest_dir(base)
        if root is None:
            raise ExtError("Fjord couldn't find a manifest.json in there. Old-style Safari App Extensions (native Swift or "
                           "Objective-C ones) can't run outside Safari; only Safari Web Extensions can.")
        try:
            m = load_manifest(root / "manifest.json")
        except Exception:
            raise ExtError("That extension's manifest.json can't be read.")
        if "theme" in m and not any(k in m for k in ("background", "content_scripts", "action", "browser_action", "page_action")):
            raise ExtError("That is a browser theme, not an extension. Fjord doesn't support browser themes.")
        kind = detect_kind(src, root, m)
        name = ext_label(root, m)
        dest = EXT_DIR / ("%s-%s" % (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "extension", secrets.token_hex(3)))
        shutil.copytree(root, dest, ignore=shutil.ignore_patterns(".DS_Store", "__MACOSX"))
        notes = []
        if isinstance(m.get("manifest_version"), int) and m["manifest_version"] < 3:
            notes.append("It uses the older Manifest V2 format. Chromium is phasing that out, so it may not load on newer versions of Qt.")
        if kind in ("firefox", "safari"):
            m2 = load_manifest(dest / "manifest.json")
            notes += adapt_manifest(m2, dest)
            (dest / "manifest.json").write_text(json.dumps(m2, indent=2), encoding="utf-8")
        return {"dir": str(dest), "name": name, "version": str(m.get("version", "")), "kind": kind, "enabled": True,
                "notes": notes, "can": describe_permissions(m), "icon": pick_icon(dest, m),
                "options": _rel((m.get("options_ui") or {}).get("page") if isinstance(m.get("options_ui"), dict) else None)
                or _rel(m.get("options_page"))}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def ext_crx_url(ext_id, chrome_ver="130.0.0.0"):
    return ("https://clients2.google.com/service/update2/crx?response=redirect&prodversion=%s&acceptformat=crx2,crx3"
            "&x=id%%3D%s%%26installsource%%3Dondemand%%26uc" % (chrome_ver, ext_id))


def resolve_ext_url(text, chrome_ver="130.0.0.0"):
    """Turn something pasted by the person into (what, url): a Chrome Web Store page or ID, a Firefox Add-ons page, or a
    direct .crx/.xpi/.zip link. what is "crx", "amo" (an API address that names the .xpi) or "file"."""
    t = (text or "").strip()
    if re.fullmatch(r"[a-p]{32}", t):
        return "crx", ext_crx_url(t, chrome_ver)
    u = urlparse(t)
    if u.scheme in ("http", "https"):
        host = (u.hostname or "").lower()
        if host in ("chromewebstore.google.com", "chrome.google.com"):
            mt = re.search(r"/([a-p]{32})(?:/|$)", u.path)
            if mt:
                return "crx", ext_crx_url(mt.group(1), chrome_ver)
        elif host == "addons.mozilla.org":
            mt = re.search(r"/addon/([^/]+)", u.path)
            if mt:
                return "amo", "https://addons.mozilla.org/api/v5/addons/addon/%s/" % quote(unquote(mt.group(1)), safe="")
        if u.path.lower().endswith((".crx", ".xpi", ".zip")):
            return "file", t
    raise ExtError("Paste a Chrome Web Store or Firefox Add-ons link, or the address of a .crx or .xpi file.")


def ext_fetch(url, st, limit=EXT_MAX_BYTES):
    """Download through urllib, honouring Fjord's proxy when it is on (so the download doesn't skip the VPN)."""
    handlers = []
    if st.get("vpn") and st.get("proxy_host") and st.get("proxy_port"):
        if st.get("proxy_type", "socks5") != "http":
            raise ExtError("Fjord's proxy is a %s one, which the extension downloader can't use. Download the file in a tab "
                           "and choose Add from a file." % str(st.get("proxy_type", "socks5")).upper())
        cred = ""
        if st.get("proxy_user"):
            cred = "%s:%s@" % (quote(st.get("proxy_user", ""), safe=""), quote(st.get("proxy_pw", ""), safe=""))
        proxy = "http://%s%s:%s" % (cred, st["proxy_host"], st["proxy_port"])
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 FjordBrowser"})
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=40) as r:
            data = r.read(limit + 1)
    except Exception as ex:
        raise ExtError("The download failed (%s). Check your connection and the link." % (getattr(ex, "reason", None) or ex))
    if len(data) > limit:
        raise ExtError("That download is too large to be an extension.")
    if not data:
        raise ExtError("The store has no download for that extension.")
    return data


def ext_download(text, st, chrome_ver):
    """Fetch what resolve_ext_url describes into a temp file and return its path (caller deletes it)."""
    what, url = resolve_ext_url(text, chrome_ver)
    suffix = ".crx"
    if what == "amo":
        try:
            info = json.loads(ext_fetch(url, st, 5 * 1024 * 1024).decode("utf-8", "replace"))
            cur = info.get("current_version") or {}
            f = cur.get("file") or (cur.get("files") or [{}])[0]
            url = f.get("url")
        except ExtError:
            raise
        except Exception:
            url = None
        if not url:
            raise ExtError("Firefox Add-ons has no download for that add-on.")
        suffix = ".xpi"
    elif what == "file":
        suffix = Path(urlparse(url).path).suffix.lower() or ".zip"
    data = ext_fetch(url, st)
    EXT_DIR.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="dl-", dir=str(EXT_DIR))
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


class ExtPopup(QWebEngineView):
    """An extension's toolbar popup: a small window under the toolbar button that closes when you click away."""

    def __init__(self, browser, url):
        super().__init__(browser)
        self.browser = browser
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setPage(QWebEnginePage(browser.profile, self))
        self.page().windowCloseRequested.connect(self.close)
        self.page().contentsSizeChanged.connect(self._fit)
        self.resize(280, 160)
        self._right = None
        self.load(url)

    def createWindow(self, _type):
        return self.browser.new_tab(blank=True)

    def _fit(self, size):
        w, h = int(min(max(size.width(), 240), 800)), int(min(max(size.height(), 120), 600))
        if (w, h) != (self.width(), self.height()):
            self.resize(w, h)
            if self._right is not None:
                self.move(self._right.x() - w, self._right.y())

    def show_under(self, button):
        p = button.mapToGlobal(button.rect().bottomRight())
        self._right = QPoint(p.x(), p.y() + 4)
        self.move(self._right.x() - self.width(), self._right.y())
        self.show()


class ExtensionHub(QObject):
    """Keeps the list of installed extensions (~/.fjord_browser/extensions.json) and loads them into the profile through Qt
    WebEngine's extension manager. That manager only exists in PyQt6-WebEngine 6.10+, so `available` says whether it does."""
    changed = pyqtSignal()
    staged = pyqtSignal(object)   # (record or None, error text), sent from the worker thread that unpacked a package

    def __init__(self, browser):
        super().__init__(browser)
        self.b = browser
        self.records = [r for r in jload("extensions.json", []) if isinstance(r, dict) and r.get("dir")]
        self.errors = {}
        self.mgr = None
        try:
            if QWebEngineExtensionManager is not None:
                self.mgr = browser.profile.extensionManager()
        except Exception:
            self.mgr = None
        if self.mgr is not None:
            for sig in ("installFinished", "loadFinished", "unloadFinished", "uninstallFinished"):
                try:
                    getattr(self.mgr, sig).connect(lambda *_a: self.changed.emit())
                except Exception:
                    pass
        self.staged.connect(self._staged)

    @property
    def available(self):
        return self.mgr is not None

    def save(self):
        jsave("extensions.json", self.records)

    def record(self, name):
        return next((r for r in self.records if Path(r["dir"]).name == name), None)

    def info_for(self, rec):
        if self.mgr is None:
            return None
        want = os.path.normcase(os.path.realpath(rec["dir"]))
        try:
            for info in self.mgr.extensions():
                try:
                    if os.path.normcase(os.path.realpath(info.path())) == want:
                        return info
                except Exception:
                    pass
        except Exception:
            pass
        return None

    def error_for(self, rec):
        err = self.errors.get(rec["dir"], "")
        info = self.info_for(rec)
        if not err and info is not None:
            try:
                err = str(info.error() or "")
            except Exception:
                err = ""
        return err

    def _call(self, method, *args):
        fn = getattr(self.mgr, method, None)
        if fn is None:
            return False
        try:
            fn(*args)
            return True
        except Exception:
            return False

    def load_all(self):
        for r in self.records:
            if r.get("enabled", True) and Path(r["dir"]).is_dir():
                self._load(r)

    def _load(self, rec):
        if self.mgr is None:
            return False
        self.errors.pop(rec["dir"], None)
        if not self._call("loadExtension", rec["dir"]):
            self.errors[rec["dir"]] = "This version of Qt couldn't load it."
            return False
        return True

    def _unload(self, rec):
        info = self.info_for(rec)
        if info is not None:
            self._call("unloadExtension", info)

    # ----- adding -----
    def _ready(self):
        if self.available:
            return True
        from PyQt6.QtCore import QT_VERSION_STR
        self.b.toast("Extensions need PyQt6-WebEngine 6.10 or newer (this is Qt %s)" % QT_VERSION_STR, 7000)
        return False

    def add_path(self, path):
        if not self._ready():
            return
        self.b.toast("Unpacking extension…", 4000)

        def work():
            try:
                self.staged.emit((stage_extension(path), ""))
            except ExtError as ex:
                self.staged.emit((None, str(ex)))
            except Exception as ex:
                self.staged.emit((None, "Fjord couldn't read that extension (%s)." % ex))
        threading.Thread(target=work, daemon=True).start()

    def add_url(self, text):
        if not self._ready():
            return
        try:
            resolve_ext_url(text)  # fail fast on something that isn't a store or file link
        except ExtError as ex:
            self.b.toast(str(ex), 7000)
            return
        self.b.toast("Downloading extension…", 6000)
        st = dict(self.b.settings)
        m = re.search(r"Chrome/([\d.]+)", self.b.profile.httpUserAgent())

        def work():
            tmp, result = None, (None, "")
            try:
                tmp = ext_download(text, st, m.group(1) if m else "130.0.0.0")
                result = (stage_extension(tmp), "")
            except ExtError as ex:
                result = (None, str(ex))
            except Exception as ex:
                result = (None, "Fjord couldn't add that extension (%s)." % ex)
            finally:
                if tmp:  # the download is unpacked (or failed), so it is no longer needed
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            self.staged.emit(result)
        threading.Thread(target=work, daemon=True).start()

    def _staged(self, payload):
        rec, err = payload
        if rec is None:
            self.b.toast(err or "Fjord couldn't add that extension.", 8000)
            self.changed.emit()
            return
        can = rec.get("can") or []
        text = ('Add "%s" (%s)?\n\n' % (rec["name"], EXT_KINDS.get(rec["kind"], "Chrome"))
                + ("It will be able to:\n" + "\n".join("  \u2022 " + c for c in can[:8]) + ("\n  \u2022 and %d more" % (len(can) - 8) if len(can) > 8 else "")
                   if can else "It doesn't ask for any special access.")
                + ("\n\n" + "\n".join(rec.get("notes", [])) if rec.get("notes") else ""))
        if QMessageBox.question(self.b, "Add extension", text) != QMessageBox.StandardButton.Yes:
            shutil.rmtree(rec["dir"], ignore_errors=True)
            return
        self.records.append(rec)
        self.save()
        self._load(rec)
        self.b.toast('Added "%s". Reload open tabs to use it.' % rec["name"], 5000)
        self.changed.emit()

    # ----- managing -----
    def toggle(self, name):
        rec = self.record(name)
        if rec is None:
            return
        rec["enabled"] = not rec.get("enabled", True)
        self.save()
        if rec["enabled"]:
            self._load(rec)
        else:
            self._unload(rec)
        self.changed.emit()

    def remove(self, name):
        rec = self.record(name)
        if rec is None:
            return
        self._unload(rec)
        self.records = [r for r in self.records if r is not rec]
        self.save()
        shutil.rmtree(rec["dir"], ignore_errors=True)
        self.changed.emit()

    def popup(self, name):
        rec = self.record(name)
        info = self.info_for(rec) if rec else None
        url = None
        try:
            url = info.actionPopupUrl() if info is not None else None
        except Exception:
            pass
        if url is None or url.isEmpty():
            self.b.toast("%s has no popup%s" % (rec["name"] if rec else "That extension",
                                                  "" if info is not None else " (it isn't loaded)"), 3500)
            return
        self._popup = ExtPopup(self.b, url)
        self._popup.show_under(self.b.btn_ext)

    def options(self, name):
        rec = self.record(name)
        info = self.info_for(rec) if rec else None
        if rec is None or info is None or not rec.get("options"):
            self.b.toast("%s has no settings page" % (rec["name"] if rec else "That extension"), 3500)
            return
        self.b.new_tab(QUrl("chrome-extension://%s/%s" % (info.id(), rec["options"])))


def ext_icon_uri(rec):
    try:
        p = Path(rec["dir"]) / rec["icon"]
        if rec.get("icon") and p.stat().st_size < 200_000:
            return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
    except (OSError, KeyError):
        pass
    return ""


def extensions_html(b):
    e = html.escape
    hub = b.extensions
    rows = ""
    for r in hub.records:
        d = quote(Path(r["dir"]).name, safe="")
        on = bool(r.get("enabled", True))
        err = hub.error_for(r) if on else ""
        sub = e("%s \u00b7 version %s" % (EXT_KINDS.get(r.get("kind"), "Chrome"), r.get("version") or "?"))
        if err:
            sub += '<br><span style="color:#ff9a9a">%s</span>' % e(err)
        for n in r.get("notes") or []:
            sub += "<br>" + e(n)
        uri = ext_icon_uri(r)
        icon = ('<img src="%s" style="width:28px;height:28px;border-radius:7px;margin-right:12px;flex:none">' % uri) if uri else ""
        links = ""
        if on and r.get("options"):
            links += '<a class=x href="fjord://ext-options?d=%s">Settings</a>' % d
        if on:
            links += '<a class=x href="fjord://ext-popup?d=%s">Open</a>' % d
        links += '<a class=x href="fjord://ext-remove?d=%s">Remove</a>' % d
        rows += ('<div class=row><div style="display:flex;align-items:center;min-width:0">%s<div>%s<small style="white-space:normal">%s</small></div></div>'
                 '<div style="display:flex;align-items:center;gap:4px;flex:none">%s<a class="sw%s" href="fjord://ext-toggle?d=%s"><i></i></a></div></div>'
                 % (icon, e(r["name"]), sub, links, " on" if on else "", d))
    if not rows:
        rows = '<div class=hint style="padding-top:14px">No extensions yet. Add one below.</div>'
    warn = ""
    if not hub.available:
        from PyQt6.QtCore import QT_VERSION_STR
        warn = ('<h2>Not available yet</h2><div class=card><div class=row><div>This copy of Qt WebEngine can\'t run extensions'
                '<small>Extensions need PyQt6-WebEngine 6.10 or newer, and this one is Qt %s. Run: pip install -U PyQt6 '
                'PyQt6-WebEngine, then restart Fjord.</small></div></div></div>' % e(QT_VERSION_STR))
    add = ('<h2>Add an extension</h2><div class=card>'
           '<form class=pf action="fjord://ext-url"><div class=fr><input name=u placeholder="Chrome Web Store or Firefox Add-ons link">'
           '<button>Add</button></div></form>'
           '<div class=row><div>From a file<small>A .crx, .xpi or .zip file</small></div><a class=x href="fjord://ext-add?m=file">Choose file…</a></div>'
           '<div class=row><div>From a folder or Mac app<small>An unpacked extension, or a Safari extension\'s .app (usually in Applications)</small></div>'
           '<a class=x href="fjord://ext-add?m=folder">Choose folder…</a></div>'
           '<div class=hint style="padding-top:12px">Chrome extensions run as they are. Firefox and Safari extensions are converted when you add them, '
           'so a few that rely on browser-specific features may not work. Safari\'s older native App Extensions can\'t run outside Safari.</div></div>')
    return ("<!doctype html><meta charset=utf-8><meta name=color-scheme content=dark><title>Extensions</title><style>" + base_css()
            + themed(SETTINGS_CSS) + "</style><main><h1>Extensions</h1>" + warn + "<h2>Installed</h2><div class=card>" + rows
            + "</div>" + add + "</main>")


class Page(QWebEnginePage):
    def __init__(self, profile, tab):
        super().__init__(profile, tab)
        self.tab = tab
        tab.browser.adblock.install(self)

    def acceptNavigationRequest(self, url, nav_type, is_main):
        if is_main and url.scheme() == "fjord" and url.host() in ("clear-history", "remove-bookmark", "search", "set", "ess-remove", "ess-add", "vpn", "adblock-update", "allow-remove", "top-add", "top-remove", "bg",
                                                                "ext-open", "ext-add", "ext-url", "ext-toggle", "ext-remove", "ext-popup", "ext-options",
                                                                "import-open", "import-run", "import-file",
                                                                "budget-add", "budget-set", "budget-remove"):
            QTimer.singleShot(0, lambda: self.tab.browser.internal_action(url))
            return False
        if is_main and url.scheme() in ("http", "https"):
            self.tab.browser.adblock.update_site(self, url.host())  # site-specific hiding for the page about to load
        return super().acceptNavigationRequest(url, nav_type, is_main)

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message == NOTES_GET:  # the sticky-note script is up and asking for this site's notes
            self.tab.browser.send_notes(self)
            return
        if message.startswith(NOTES_MSG):  # the sticky-note script reporting its notes: save them, keep the console clean
            self.tab.browser.save_notes(self.url().host(), message[len(NOTES_MSG):])
            return
        super().javaScriptConsoleMessage(level, message, line, source)


class Tab(QWebEngineView):
    def __init__(self, profile, browser):
        super().__init__()
        self.browser = browser
        self.loading = False
        self.closing = False
        self.group = None
        self.prog = 0
        self.row = None
        self.last_active = time.time()
        self.pending = None   # URL of a restored tab that hasn't been loaded yet (loads when first selected)
        self.setPage(Page(profile, self))

    def createWindow(self, _type):
        return self.browser.new_tab(blank=True, opener=self)


class AddressBar(QLineEdit):
    """The address field. macOS style centres the text while idle (like Safari) and rings it with a soft glow on focus;
    Windows style grows an accent underline from the middle on focus (like a Windows 11 text box)."""
    def __init__(self):
        super().__init__()
        self._glow = 0.0
        self._glow_anim = QVariantAnimation(self)
        self._glow_anim.setDuration(340)
        self._glow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._glow_anim.valueChanged.connect(self._set_glow)

    def _set_glow(self, v):
        self._glow = float(v)
        self.update()

    def _go_glow(self, to):
        self._glow_anim.stop()
        self._glow_anim.setStartValue(self._glow)
        self._glow_anim.setEndValue(to)
        self._glow_anim.start()

    def refresh_style(self, focused=None):
        if focused is None:
            focused = self.hasFocus()
        centred = UI["mode"] == "mac" and not focused
        h = Qt.AlignmentFlag.AlignHCenter if centred else Qt.AlignmentFlag.AlignLeft
        self.setAlignment(h | Qt.AlignmentFlag.AlignVCenter)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.refresh_style(True)
        self._go_glow(1.0)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.refresh_style(False)
        self._go_glow(0.0)

    def mousePressEvent(self, e):
        had = self.hasFocus()
        super().mousePressEvent(e)
        if not had:
            self.selectAll()

    def paintEvent(self, e):
        super().paintEvent(e)
        mode = UI["mode"]
        if mode == "default":
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if mode == "mac":
            rad = r.height() / 2.0
            hi = QLinearGradient(r.topLeft(), r.topRight())
            hi.setColorAt(0.0, QColor(255, 255, 255, 0))
            hi.setColorAt(0.5, QColor(255, 255, 255, 46))
            hi.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(QPen(QBrush(hi), 1.0))
            p.drawLine(QPointF(r.left() + rad * 0.6, r.top() + 0.5), QPointF(r.right() - rad * 0.6, r.top() + 0.5))
            if self._glow > 0.01:
                a = self._glow
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(accent_color(int(28 * a)), 3.0))
                p.drawRoundedRect(r.adjusted(2, 2, -2, -2), rad - 2, rad - 2)
                p.setPen(QPen(accent_color(int(150 * a)), 1.2))
                p.drawRoundedRect(r.adjusted(0.7, 0.7, -0.7, -0.7), rad - 0.7, rad - 0.7)
        elif self._glow > 0.01:
            clip = QPainterPath()
            clip.addRoundedRect(r, 5, 5)
            p.setClipPath(clip)
            w = r.width() * self._glow
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent_color())
            p.drawRect(QRectF(r.center().x() - w / 2, r.bottom() - 2, w, 2))
        p.end()


class FadeButton(QToolButton):
    """Tool button with a smooth hover highlight. macOS style adds a glass hover and a ripple on press."""
    RIPPLE = True

    def __init__(self, radius=10):
        super().__init__()
        self._h = 0.0
        self.radius = radius
        self._rip, self._rip_pt = 1.0, QPointF()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_h)
        self._rip_anim = QVariantAnimation(self)
        self._rip_anim.setDuration(520)
        self._rip_anim.setStartValue(0.0)
        self._rip_anim.setEndValue(1.0)
        self._rip_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._rip_anim.valueChanged.connect(self._set_rip)

    def _set_h(self, v):
        self._h = float(v)
        self.update()

    def _set_rip(self, v):
        self._rip = float(v)
        self.update()

    def _go(self, to):
        self._anim.stop()
        self._anim.setStartValue(self._h)
        self._anim.setEndValue(to)
        self._anim.start()

    def enterEvent(self, e):
        self._go(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._go(0.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if self.RIPPLE and UI["mode"] == "mac" and self.isEnabled() and e.button() == Qt.MouseButton.LeftButton:
            self._rip_pt = QPointF(e.position())
            self._rip_anim.stop()
            self._rip_anim.start()
        super().mousePressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        mode = UI["mode"]
        rip = mode == "mac" and self._rip < 1.0
        if not ((self._h > 0.01 or rip) and self.isEnabled()):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        r = QRectF(self.rect())
        rad = shape_radius(self.radius, r)
        if self._h > 0.01:
            if mode == "mac":  # glass: a lit top edge fading down, with a thin rim
                g = QLinearGradient(r.topLeft(), r.bottomLeft())
                g.setColorAt(0.0, glass_rgba(30 * self._h))
                g.setColorAt(1.0, glass_rgba(10 * self._h))
                p.setBrush(QBrush(g))
                p.drawRoundedRect(r, rad, rad)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(glass_rgba(24 * self._h), 1))
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
                p.setPen(Qt.PenStyle.NoPen)
            else:
                p.setBrush(glass_rgba((20 if mode == "windows" else 24) * self._h))
                p.drawRoundedRect(r, rad, rad)
        if rip:  # a soft circle spreading out from where it was pressed
            clip = QPainterPath()
            clip.addRoundedRect(r, rad, rad)
            p.setClipPath(clip)
            k = max(r.width(), r.height()) * (0.35 + 0.9 * self._rip)
            p.setBrush(QColor(255, 255, 255, int(44 * (1.0 - self._rip))))
            p.drawEllipse(self._rip_pt, k, k)
        p.end()


class NewGroupButton(FadeButton):
    """The "+ Group" button. Its dashed outline is painted here with antialiasing, because Qt's own dashed
    stylesheet border with rounded corners comes out jagged and broken at the corners."""
    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(255, 255, 255, 64 if self._h > 0.5 else 46), 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setDashPattern([3.0, 3.0])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.8, 0.8, -0.8, -0.8), rr(self.radius), rr(self.radius))
        p.end()


TAB_MIME = "application/x-fjord-tab"
GAP = 8


# custom item-data roles (plain ints: Qt.UserRole is 0x100)
ROLE_EXTRA = 257    # extra size reserved at the start of a row for an island header
ROLE_ANIM = 258     # row is mid open/close animation, so layout_islands() leaves its size alone
ROLE_GROUPED = 259  # row sits inside an island


class DropTarget:
    """Mixin: besides whatever the widget already accepts, let it take anything that isn't one of Fjord's own
    tab / scratchpad drags and hand it to the Scratchpad. `scratch` is the ScratchDrawer, set once the UI exists."""
    scratch = None

    def _sp_ok(self, e):
        return DropTarget.scratch is not None and DropTarget.scratch.accepts(e.mimeData())

    def dragEnterEvent(self, e):
        if self._sp_ok(e):
            DropTarget.scratch.drop_hint(True)
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self._sp_ok(e):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dragLeaveEvent(self, e):
        if DropTarget.scratch is not None:
            DropTarget.scratch.drop_hint(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        if self._sp_ok(e):
            DropTarget.scratch.drop_hint(False)
            DropTarget.scratch.ingest(e.mimeData())
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class GroupChip(DropTarget, QWidget):
    """Island header: a coloured name pill with a chevron.
    Click = slide the island's tabs out / back in, right-click = menu, drop a tab on it = add."""
    clicked = pyqtSignal()

    def __init__(self, gid, on_drop):
        super().__init__()
        self.gid, self.on_drop = gid, on_drop
        self.name, self.color, self.count = "", "#4fb0e8", 0
        self.compact = self.horiz = False
        self.open_p = 1.0
        self.active = self.hover = self.drop = self._press = False
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def configure(self, name, color, count, compact, horiz):
        self.name, self.color, self.count = name, color, count
        self.compact, self.horiz = compact, horiz
        self.setToolTip("%s (%d tab%s)" % (name, count, "" if count == 1 else "s"))
        self.update()

    def set_open(self, p):
        if abs(p - self.open_p) > 0.001:
            self.open_p = p
            self.update()

    def set_active(self, on):
        if on != self.active:
            self.active = on
            self.update()

    def enterEvent(self, e):
        self.hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._press = e.button() == Qt.MouseButton.LeftButton
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._press and e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        self._press = False
        super().mouseReleaseEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(TAB_MIME):
            self.drop = True
            self.update()
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragLeaveEvent(self, e):
        self.drop = False
        self.update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(TAB_MIME):
            super().dropEvent(e)
            return
        self.drop = False
        self.update()
        self.on_drop(int(bytes(e.mimeData().data(TAB_MIME)).decode()), self.gid)
        e.acceptProposedAction()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col, dark = QColor(self.color), QColor(11, 20, 29)
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setBold(True)
        fm = QFontMetrics(f)
        ph, chev, pad = 20.0, 12.0, 8.0
        collapsed = self.open_p < 0.5
        if self.compact:
            label, pw, x0 = "", 30.0, (self.width() - 30.0) / 2
        else:
            room = self.width() - 6 - pad * 2 - chev - 4 - (30 if collapsed else 8)
            label = fm.elidedText(self.name, Qt.TextElideMode.ElideRight, max(20, int(room)))
            pw, x0 = pad * 2 + chev + 4 + fm.horizontalAdvance(label), 6.0
        y0 = (self.height() - ph) / 2
        alpha = 255 if (self.hover or self.drop) else 215
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col.red(), col.green(), col.blue(), alpha))
        p.drawRoundedRect(QRectF(x0, y0, pw, ph), ph / 2, ph / 2)
        if self.drop or (self.active and collapsed):
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 230), 1.6))
            p.drawRoundedRect(QRectF(x0 + 0.8, y0 + 0.8, pw - 1.6, ph - 1.6), ph / 2, ph / 2)
        # chevron: points down when open, turns to point right when the island is slid shut
        p.save()
        p.translate(x0 + pad + chev / 2 - 1, y0 + ph / 2)
        p.rotate(-90.0 * (1.0 - self.open_p))
        pen = QPen(dark, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(-3.2, -1.6), QPointF(0.0, 1.6), QPointF(3.2, -1.6)]))
        p.restore()
        if label:
            p.setFont(f)
            p.setPen(dark)
            p.drawText(QRectF(x0 + pad + chev + 3, y0, pw, ph),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
        if not self.compact and self.open_p < 0.999:  # tab count fades in as the island closes
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor(col.red(), col.green(), col.blue(), int(210 * (1.0 - self.open_p))))
            p.drawText(QRectF(x0 + pw + 7, 0, 40, self.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, str(self.count))
        p.end()


MAX_PANES = 4


class SplitHandle(QWidget):
    """Draggable divider between two neighbouring panes (double-click = equal widths)."""
    def __init__(self, area, idx):
        super().__init__(area)
        self.area, self.idx = area, idx
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.hide()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.area.drag_handle(self.idx, self.mapToParent(e.position().toPoint()).x())

    def mouseDoubleClickEvent(self, e):
        n = len(self.area.weights)
        self.area.weights = [1.0 / n] * n
        self.area.relayout()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent_color(110))
        p.drawRoundedRect(QRectF(self.width() / 2 - 1.5, self.height() / 2 - 22, 3, 44), 1.5, 1.5)
        p.end()


PAGE_RADIUS = 16


_NOISE = {}


def noise_pixmap():
    """A tiny tile of faint noise, laid over soft gradients so they don't show colour banding."""
    pm = _NOISE.get("pm")
    if pm is None:
        import random
        rnd = random.Random(7)
        img = QImage(96, 96, QImage.Format.Format_ARGB32)
        for y in range(96):
            for x in range(96):
                img.setPixelColor(x, y, QColor(255, 255, 255, rnd.randint(0, 9)))
        pm = _NOISE["pm"] = QPixmap.fromImage(img)
    return pm


def paint_backdrop(p, rect):
    """The window's background. The macOS style adds two faint glows of the accent colour (one hue only), just enough
    for the glass to have something to pick up."""
    r = QRectF(rect)
    p.fillRect(r, QColor(themed("#0b141d")))
    if UI["mode"] != "mac":
        return
    big = max(r.width(), r.height())
    for cx, cy, rad, al in ((0.04, 0.0, 0.75, 46), (0.98, 1.0, 0.70, 30)):
        g = QRadialGradient(QPointF(r.x() + r.width() * cx, r.y() + r.height() * cy), big * rad)
        c0, c1 = QColor(ACCENT["main"]), QColor(ACCENT["main"])
        c0.setAlpha(al)
        c1.setAlpha(0)
        g.setColorAt(0.0, c0)
        g.setColorAt(1.0, c1)
        p.fillRect(r, QBrush(g))
    p.fillRect(r, QBrush(noise_pixmap()))


def paint_glass(p, rect, radius, strength=1.0):
    """Restrained liquid glass: a barely-there body lit from above, a hairline rim that is brightest at the top-left and
    bottom-right, and one fine highlight just inside the top edge. No heavy bevels, no lenses."""
    r = QRectF(rect)
    s = strength
    radius = max(0.0, min(rr(radius), r.width() / 2.0, r.height() / 2.0))

    def white(a):
        return glass_rgba(a * s)
    path = QPainterPath()
    path.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    body = QLinearGradient(r.topLeft(), r.bottomLeft())
    body.setColorAt(0.0, white(20))
    body.setColorAt(1.0, white(5))
    p.fillPath(path, QBrush(body))
    rim = QLinearGradient(r.topLeft(), r.bottomRight())
    rim.setColorAt(0.0, white(120))
    rim.setColorAt(0.25, white(36))
    rim.setColorAt(0.75, white(26))
    rim.setColorAt(1.0, white(80))
    p.setPen(QPen(QBrush(rim), 1.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    inset = min(radius * 0.7, r.width() / 3.0)
    hi = QLinearGradient(QPointF(r.left() + inset, 0), QPointF(r.right() - inset, 0))
    hi.setColorAt(0.0, white(0))
    hi.setColorAt(0.5, white(55))
    hi.setColorAt(1.0, white(0))
    p.setPen(QPen(QBrush(hi), 1.0))
    p.drawLine(QPointF(r.left() + inset, r.top() + 1.5), QPointF(r.right() - inset, r.top() + 1.5))


class GlassBar(QWidget):
    """The toolbar. In the macOS style its buttons sit in separate glass capsules, like Safari's toolbar; the groups
    (lists of widgets) are set by ToolbarEditor.apply(). Otherwise it is flat and transparent."""
    def __init__(self):
        super().__init__()
        self.caps = []

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.update()

    def paintEvent(self, e):
        if UI["mode"] != "mac" or not self.caps:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for grp in self.caps:
            boxes = [w.geometry() for w in grp if not w.isHidden()]
            if not boxes:
                continue
            u = boxes[0]
            for b in boxes[1:]:
                u = u.united(b)
            u = u.adjusted(-4, -3, 4, 3)
            paint_glass(p, QRectF(u), u.height() / 2.0, 0.9)
        p.end()


class Backdrop(QWidget):
    """The window's root widget; paints the background (plus the macOS style's glows)."""
    def paintEvent(self, e):
        p = QPainter(self)
        paint_backdrop(p, self.rect())
        p.end()


class PageCorners(QWidget):
    """Mouse-transparent overlay that paints the window background over the corners of each page,
    giving the web view antialiased rounded corners (and a hairline rim in the macOS / Windows styles)."""
    def __init__(self, area):
        super().__init__(area)
        self.area = area
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def paintEvent(self, e):
        rects = [w.geometry() for w in self.area.items if w.isVisible()]
        if not rects:
            return
        rad = page_radius()
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        for r in rects:
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(r), rad, rad)
            path = path.subtracted(hole)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        win = self.window()
        root = win.centralWidget() if hasattr(win, "centralWidget") else None
        if UI["mode"] == "mac" and root is not None:
            # the corners must match the glowing backdrop behind them, so paint that into an image and cut the corners out of it
            dpr = self.devicePixelRatioF()
            img = QImage(QSize(int(math.ceil(self.width() * dpr)), int(math.ceil(self.height() * dpr))),
                         QImage.Format.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(dpr)
            img.fill(Qt.GlobalColor.transparent)
            ip = QPainter(img)
            ip.setRenderHint(QPainter.RenderHint.Antialiasing)
            off = self.mapTo(root, QPoint(0, 0))
            ip.translate(-off.x(), -off.y())
            paint_backdrop(ip, root.rect())
            ip.resetTransform()
            holes = QPainterPath()
            for r in rects:
                holes.addRoundedRect(QRectF(r), rad, rad)
            ip.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            ip.fillPath(holes, QColor(0, 0, 0, 255))
            ip.end()
            p.drawImage(0, 0, img)
        else:
            p.setBrush(QColor(themed("#0b141d")))
            p.drawPath(path)
        if UI["mode"] != "default":
            p.setBrush(Qt.BrushStyle.NoBrush)
            for r in rects:
                rf = QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5)
                if UI["mode"] == "mac":
                    g = QLinearGradient(rf.topLeft(), rf.bottomRight())
                    g.setColorAt(0.0, QColor(255, 255, 255, 62))
                    g.setColorAt(0.3, QColor(255, 255, 255, 16))
                    g.setColorAt(0.7, QColor(255, 255, 255, 12))
                    g.setColorAt(1.0, QColor(255, 255, 255, 38))
                    p.setPen(QPen(QBrush(g), 1))
                else:
                    p.setPen(QPen(QColor(255, 255, 255, 20), 1))
                p.drawRoundedRect(rf, rad, rad)
        p.end()


class TabArea(QWidget):
    """Replacement for QStackedWidget: shows one tab, or 2-4 tabs side by side in split view."""
    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.items, self.cur = [], -1
        self.split, self.weights, self.handles = None, [], []
        self.corners = PageCorners(self)

    def count(self):
        return len(self.items)

    def widget(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def indexOf(self, w):
        return self.items.index(w) if w in self.items else -1

    def currentIndex(self):
        return self.cur

    def currentWidget(self):
        return self.widget(self.cur)

    def in_split(self, w):
        return bool(self.split) and any(w is t for t in self.split)

    def addWidget(self, w):
        return self.insertWidget(len(self.items), w)

    def insertWidget(self, i, w):
        cw = self.currentWidget()
        w.setParent(self)
        w.hide()
        self.items.insert(i, w)
        self.cur = self.items.index(cw) if cw is not None else 0
        self.relayout()
        return i

    def removeWidget(self, w):
        i = self.indexOf(w)
        if i < 0:
            return
        cw = self.currentWidget()
        self.items.pop(i)
        w.hide()
        if self.in_split(w):
            k = next(j for j, t in enumerate(self.split) if t is w)
            self.split.pop(k)
            self.weights.pop(k)
            if len(self.split) < 2:
                self.split, self.weights = None, []
            else:
                tot = sum(self.weights)
                self.weights = [x / tot for x in self.weights]
        if cw is w or cw is None:
            self.cur = min(i, len(self.items) - 1)
        else:
            self.cur = self.items.index(cw)
        self.relayout()

    def reorder(self, i, to):
        cw = self.currentWidget()
        self.items.insert(to, self.items.pop(i))
        self.cur = self.items.index(cw) if cw is not None else -1

    def setCurrentIndex(self, i):
        if 0 <= i < len(self.items):
            self.cur = i
            self.relayout()

    def set_split(self, tabs, weights=None):
        """Show `tabs` side by side (2 or more). An empty/short list leaves split view."""
        if len(tabs) < 2:
            self.split, self.weights = None, []
        else:
            n = len(tabs)
            self.split = list(tabs)
            self.weights = list(weights) if weights and len(weights) == n else [1.0 / n] * n
        self.relayout()

    def drag_handle(self, idx, x):
        n = len(self.split)
        total = self.width() - GAP * (n - 1)
        start = sum(self.weights[j] * total + GAP for j in range(idx))
        pair = (self.weights[idx] + self.weights[idx + 1]) * total
        mn = min(120, pair / 3)
        left = max(mn, min(pair - mn, x - GAP / 2 - start))
        self.weights[idx] = left / total
        self.weights[idx + 1] = (pair - left) / total
        self.relayout()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.relayout()

    def relayout(self):
        r = self.rect()
        cw = self.currentWidget()
        visible = []
        sp = self.split
        if sp and cw is not None and self.in_split(cw):
            n = len(sp)
            while len(self.handles) < n - 1:
                self.handles.append(SplitHandle(self, len(self.handles)))
            total = r.width() - GAP * (n - 1)
            x = 0
            for k, t in enumerate(sp):
                w = int(total * self.weights[k]) if k < n - 1 else r.width() - x
                t.setGeometry(x, 0, w, r.height())
                visible.append(t)
                x += w
                if k < n - 1:
                    h = self.handles[k]
                    h.setGeometry(x, 0, GAP, r.height())
                    h.show()
                    h.raise_()
                    x += GAP
            for h in self.handles[n - 1:]:
                h.hide()
        else:
            for h in self.handles:
                h.hide()
            if cw is not None:
                cw.setGeometry(r)
                visible = [cw]
        for w in self.items:
            w.setVisible(any(w is v for v in visible))
        self.corners.setGeometry(self.rect())
        self.corners.raise_()
        for h in self.handles:
            if h.isVisible():
                h.raise_()
        self.corners.update()


class SideFrame(DropTarget, QFrame):
    def __init__(self, on_leave, on_geom=None):
        super().__init__()
        self.on_leave = on_leave
        self.on_geom = on_geom
        self.drop_on = False  # something droppable is being dragged over (Scratchpad hint)
        self.setAcceptDrops(True)

    def _geom(self):
        if self.on_geom:
            self.on_geom()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._geom()

    def moveEvent(self, e):
        super().moveEvent(e)
        self._geom()

    def showEvent(self, e):
        super().showEvent(e)
        self._geom()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._geom()

    def leaveEvent(self, e):
        self.on_leave()
        super().leaveEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        glass = UI["mode"] == "mac"
        if glass or self.drop_on:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            if glass:
                paint_glass(p, self.rect(), 20)
            if self.drop_on:
                p.setPen(QPen(accent_color(190), 1.5, Qt.PenStyle.DashLine))
                p.setBrush(accent_color(16))
                p.drawRoundedRect(QRectF(self.rect()).adjusted(2.5, 2.5, -2.5, -2.5), rr(14), rr(14))
            p.end()


class HoverZone(QWidget):
    def __init__(self, on_enter):
        super().__init__()
        self.on_enter = on_enter

    def enterEvent(self, e):
        self.on_enter()
        super().enterEvent(e)


class TabList(DropTarget, QListWidget):
    """Sidebar tab list. Drag a tab onto another tab to open them in split view."""
    def __init__(self, on_middle):
        super().__init__()
        self.on_middle = on_middle
        self.on_drop = None
        self.drag_start = None
        self._hover_row = -1
        self.islands = []  # [(colour, [row indexes])] painted as rounded "tab islands" behind the rows
        self.setHorizontalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        m = self.model()
        for sig in (m.rowsInserted, m.rowsRemoved, m.dataChanged, m.layoutChanged):
            sig.connect(lambda *a: self.updateGeometry())

    def sizeHint(self):
        if self.flow() != QListView.Flow.LeftToRight:
            return super().sizeHint()
        w = 2 * self.frameWidth() + self.spacing()
        for i in range(self.count()):
            it = self.item(i)
            if not it.isHidden():
                w += it.sizeHint().width() + self.spacing()
        return QSize(w, self.maximumHeight())

    def minimumSizeHint(self):
        if self.flow() != QListView.Flow.LeftToRight:
            return super().minimumSizeHint()
        return QSize(60, self.maximumHeight())

    def paintEvent(self, e):
        if self.islands:
            horiz = self.flow() == QListView.Flow.LeftToRight
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            for color, rows in self.islands:
                u = None
                for r in rows:
                    it = self.item(r)
                    if it is None or it.isHidden():
                        continue
                    rc = self.visualItemRect(it)
                    if rc.width() <= 0 or rc.height() <= 0:
                        continue
                    u = rc if u is None else u.united(rc)
                if u is None:
                    continue
                c = QColor(color)
                box = QRectF(u).adjusted(-1, 1, 1, -1) if horiz else QRectF(u).adjusted(1, 1, -1, -1)
                p.setBrush(QColor(c.red(), c.green(), c.blue(), 32))
                p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 95), 1))
                p.drawRoundedRect(box, rr(14), rr(14))
            p.end()
        super().paintEvent(e)

    def wheelEvent(self, e):
        if self.flow() == QListView.Flow.LeftToRight:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - e.angleDelta().y() - e.angleDelta().x())
            e.accept()
        else:
            super().wheelEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.drag_start = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (e.buttons() & Qt.MouseButton.LeftButton) and self.drag_start is not None:
            if (e.position().toPoint() - self.drag_start).manhattanLength() > QApplication.startDragDistance() * 2:
                it = self.itemAt(self.drag_start)
                self.drag_start = None
                if it is not None:
                    self._start_drag(self.row(it), it)
                    return
        super().mouseMoveEvent(e)

    def _start_drag(self, row, item):
        md = QMimeData()
        md.setData(TAB_MIME, str(row).encode())
        d = QDrag(self)
        d.setMimeData(md)
        pm = self.viewport().grab(self.visualItemRect(item))
        d.setPixmap(pm)
        d.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
        d.exec(Qt.DropAction.MoveAction)
        self.set_hover(-1)

    def mouseReleaseEvent(self, e):
        self.drag_start = None
        if e.button() == Qt.MouseButton.MiddleButton:
            it = self.itemAt(e.position().toPoint())
            if it:
                self.on_middle(self.row(it))
                return
        super().mouseReleaseEvent(e)

    # ----- drag & drop target -----
    def _src(self, e):
        return int(bytes(e.mimeData().data(TAB_MIME)).decode())

    def _row_at(self, e):
        it = self.itemAt(e.position().toPoint())
        return self.row(it) if it is not None else -1

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(TAB_MIME):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(TAB_MIME):
            r = self._row_at(e)
            self.set_hover(r if r >= 0 and r != self._src(e) else -1)
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dragLeaveEvent(self, e):
        self.set_hover(-1)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasFormat(TAB_MIME):
            src, dst = self._src(e), self._row_at(e)
            self.set_hover(-1)
            if dst >= 0 and dst != src and self.on_drop:
                self.on_drop(src, dst)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)

    def set_hover(self, r):
        if r == self._hover_row:
            return
        for row, on in ((self._hover_row, False), (r, True)):
            it = self.item(row) if row >= 0 else None
            w = self.itemWidget(it) if it is not None else None
            if w is not None:
                w.set_drop(on)
        self._hover_row = r


class TabDelegate(QStyledItemDelegate):
    """Draws a tab's hover/selected highlight below the island header its row may carry, inset inside islands."""
    def paint(self, painter, option, index):
        extra = index.data(ROLE_EXTRA) or 0
        grouped = bool(index.data(ROLE_GROUPED))
        if extra or grouped:
            opt = QStyleOptionViewItem(option)
            horiz = option.widget is not None and option.widget.flow() == QListView.Flow.LeftToRight
            if horiz:
                opt.rect = opt.rect.adjusted(extra, 3 if grouped else 0, 0, -3 if grouped else 0)
            else:
                opt.rect = opt.rect.adjusted(4 if grouped else 0, extra, -4 if grouped else 0, 0)
            if extra and option.widget is not None:  # hovering the header shouldn't light up the tab under it
                pos = option.widget.viewport().mapFromGlobal(QCursor.pos())
                if (pos.x() < opt.rect.left()) if horiz else (pos.y() < opt.rect.top()):
                    opt.state = opt.state & ~QStyle.StateFlag.State_MouseOver
            option = opt
        super().paint(painter, option, index)
        if UI["mode"] == "windows" and option.state & QStyle.StateFlag.State_Selected:
            # Windows 11 marks the current tab with a small accent pill
            horiz = option.widget is not None and option.widget.flow() == QListView.Flow.LeftToRight
            rc = QRectF(option.rect)
            pill = QRectF(rc.center().x() - 8, rc.bottom() - 4, 16, 3) if horiz else QRectF(rc.left() + 2, rc.center().y() - 8, 3, 16)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent_color())
            painter.drawRoundedRect(pill, 1.5, 1.5)
            painter.restore()
        if UI["mode"] == "mac" and option.state & QStyle.StateFlag.State_Selected:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint_glass(painter, QRectF(option.rect).adjusted(0, 2, 0, -2), 12, 0.8)
            painter.restore()


class TabRow(QWidget):
    def __init__(self, tab, on_close):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 6, 0)
        lay.setSpacing(6)
        self.icon = QLabel()
        self.icon.setFixedSize(16, 16)
        self.title = QLabel("New Tab")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.audio = QLabel()
        self.audio.hide()
        self.split_mark = QLabel("⧉")
        self.split_mark.hide()
        self.split_on = False
        self.group_color = None
        self.drop = False
        self.hdr_chip, self.hdr_extra, self.hdr_horiz, self.grouped = None, 0, False, False
        self.icon_pm, self.sleeping = None, False
        for w in (self.icon, self.title, self.audio, self.split_mark):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        close = FadeButton(8)
        close.setObjectName("close")
        close.setText("✕")
        close.clicked.connect(lambda: on_close(tab))
        # compact mode: small hover-only close badge in the corner (not part of the layout)
        self.xbtn = FadeButton(7)
        self.xbtn.setParent(self)
        self.xbtn.setObjectName("miniclose")
        self.xbtn.setText("✕")
        self.xbtn.setFixedSize(14, 14)
        self.xbtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.xbtn.clicked.connect(lambda: on_close(tab))
        self.xbtn.hide()
        self.lay = lay
        self.close_btn = close
        self.compact = False
        self.padl, self.padr = QWidget(), QWidget()
        for pw in (self.padl, self.padr):
            pw.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            pw.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            pw.hide()
        for w, s in ((self.padl, 1), (self.icon, 0), (self.title, 1), (self.split_mark, 0), (self.audio, 0), (self.padr, 1), (close, 0)):
            lay.addWidget(w, s)

    def set_compact(self, c):
        self.compact = c
        self.split_mark.setVisible(self.split_on and not c)
        for w in (self.title, self.close_btn):
            w.setVisible(not c)
        for w in (self.padl, self.padr):
            w.setVisible(c)
        if c:
            self.audio.hide()
        self._apply_margins()
        if not c:
            self.xbtn.hide()

    def _apply_margins(self):
        l, t, r, b = (0, 0, 0, 0) if self.compact else ((14, 0, 10, 0) if self.grouped else (10, 0, 6, 0))
        if self.hdr_chip is not None:  # room for the island header at the start of the row
            if self.hdr_horiz:
                l += self.hdr_extra
            else:
                t += self.hdr_extra
        self.lay.setContentsMargins(l, t, r, b)

    def set_grouped(self, on):
        if on != self.grouped:
            self.grouped = on
            self._apply_margins()

    def set_header(self, chip, extra=0, horiz=False):
        """Make this row the carrier of an island header (or pass None to release it)."""
        old = self.hdr_chip
        if old is not None and old is not chip:
            try:
                if old.parent() is self:
                    old.hide()
                    old.setParent(None)
            except RuntimeError:  # already deleted along with a previous row
                pass
        self.hdr_chip, self.hdr_extra, self.hdr_horiz = chip, (extra if chip is not None else 0), horiz
        self._apply_margins()
        if chip is not None:
            chip.setParent(self)
            self._place_header()
            chip.show()
            chip.raise_()
        self.update()

    def _place_header(self):
        if self.hdr_chip is None:
            return
        if self.hdr_horiz:
            self.hdr_chip.setGeometry(0, 0, self.hdr_extra, self.height())
        else:
            self.hdr_chip.setGeometry(0, 0, self.width(), self.hdr_extra)

    def set_group(self, color):
        self.group_color = color
        self.update()

    def set_split(self, on):
        self.split_on = on
        self.split_mark.setVisible(on and not self.compact)

    def set_drop(self, on):
        self.drop = on
        self.update()

    def paintEvent(self, e):
        if not self.drop:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ox = self.hdr_extra if (self.hdr_chip is not None and self.hdr_horiz) else 0
        oy = self.hdr_extra if (self.hdr_chip is not None and not self.hdr_horiz) else 0
        p.setPen(QPen(accent_color(255), 2))
        p.setBrush(accent_color(50))
        p.drawRoundedRect(QRectF(2 + ox, 2 + oy, self.width() - 4 - ox, self.height() - 4 - oy), rr(12), rr(12))
        p.end()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.xbtn.move(self.width() - 16, 2)
        self._place_header()

    def enterEvent(self, e):
        if self.compact:
            self.xbtn.show()
            self.xbtn.raise_()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self.xbtn.hide()
        super().leaveEvent(e)

    def set_icon(self, icon):
        self.icon_pm = icon.pixmap(16, 16)
        self._show_icon()

    def set_sleeping(self, on):
        """A sleeping (frozen/discarded) tab shows a greyed-out icon."""
        if on != self.sleeping:
            self.sleeping = on
            self._show_icon()

    def _show_icon(self):
        pm = self.icon_pm
        if pm is None or pm.isNull():
            self.icon.setPixmap(QPixmap())
            return
        if self.sleeping:
            img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
            for y in range(img.height()):
                for x in range(img.width()):
                    c = img.pixelColor(x, y)
                    g = int(0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue())
                    img.setPixelColor(x, y, QColor(g, g, g, int(c.alpha() * 0.6)))
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(self.icon_pm.devicePixelRatio())
        self.icon.setPixmap(pm)

    def update_audio(self, tab):
        p = tab.page()
        self.audio.setText("🔇" if p.isAudioMuted() else "🔊")
        self.audio.setVisible((p.isAudioMuted() or p.recentlyAudible()) and not self.compact)


# ---------- sidebar resize grip + media player ----------
SIDE_DEFAULT_W, SIDE_MIN_W, SIDE_MAX_W = 230, 180, 480
VIZ_BARS = 24
VIZ_TICK_MS = 25                      # how often the visualiser samples the audio and redraws
VIZ_NEUTRAL = ("#e4edf3", "#9fb3c2")  # bar colours for colourless (greyscale) artwork


def clamp_side_w(v):
    try:
        return max(SIDE_MIN_W, min(SIDE_MAX_W, int(v)))
    except (TypeError, ValueError):
        return SIDE_DEFAULT_W


# Injected into every page: remembers the page's Media Session handlers so the sidebar player can call
# "next track" / "previous track" on Spotify, YouTube Music, SoundCloud and anything else that registers them.
MEDIA_HOOK_JS = """(function(){try{var ms=navigator.mediaSession;if(!ms||ms.__fj)return;
var H=window.__fjH={},orig=ms.setActionHandler.bind(ms);
ms.setActionHandler=function(a,fn){H[a]=fn;return orig(a,fn);};ms.__fj=1;}catch(e){}})();"""

_MEDIA_PICK = ("var els=[].slice.call(document.querySelectorAll('video,audio'));"
               "var m=els.filter(function(e){return !e.paused&&!e.ended})[0]"
               "||els.filter(function(e){return e.currentTime>0&&e.readyState>0})[0]||els[0];")

MEDIA_STATE_JS = "(function(){try{" + _MEDIA_PICK + """
if(!m)return JSON.stringify({none:1});
var md=null,art='';
try{md=navigator.mediaSession&&navigator.mediaSession.metadata;}catch(e){}
if(md&&md.artwork&&md.artwork.length){var best=md.artwork[0],bs=0;
 md.artwork.forEach(function(a){var s=parseInt((a.sizes||'0').split('x')[0])||0;if(s>=bs){bs=s;best=a;}});art=best.src||'';}
var d=m.duration;
return JSON.stringify({paused:m.paused,t:m.currentTime||0,d:isFinite(d)?d:0,
 title:(md&&md.title)||'',artist:(md&&(md.artist||md.album))||'',art:art});
}catch(e){return JSON.stringify({none:1});}})()"""

_PREV_SEL = ".ytp-prev-button,.previous-button,.skipControl__previous,[data-testid=\"control-button-skip-back\"]"
_NEXT_SEL = ".ytp-next-button,.next-button,.skipControl__next,[data-testid=\"control-button-skip-forward\"]"


def media_js(action, arg=0.0):
    body = {
        "toggle": "if(m){if(m.paused){m.play();}else{m.pause();}}",
        "pause": "if(m&&!m.paused){m.pause();}",
        "seek": "if(m&&isFinite(m.duration)){m.currentTime=m.duration*%f;}" % arg,
        # prefer the page's own Media Session handler, then known site buttons, then a plain +10s skip
        "next": ("if(h.nexttrack){h.nexttrack({action:'nexttrack'});}"
                 "else if(!clk('%s')&&m&&isFinite(m.duration)){m.currentTime=Math.min(m.duration,m.currentTime+10);}" % _NEXT_SEL),
        # like most players: restart the track if it's been playing a few seconds, otherwise go to the previous one
        "prev": ("if(m&&m.currentTime>=4){m.currentTime=0;}"
                 "else if(h.previoustrack){h.previoustrack({action:'previoustrack'});}"
                 "else{clk('%s');}" % _PREV_SEL),
    }[action]
    return ("(function(){try{" + _MEDIA_PICK + "var h=window.__fjH||{};"
            "function clk(s){var b=document.querySelector(s);if(b){b.click();return true;}return false;}"
            + body + "}catch(e){}})()")


# Taps the playing media element with a Web Audio analyser (not routed to the speakers, so playback is untouched).
# Returns 'null' when the page won't allow it (DRM / cross-origin media) and the player falls back to ambient bars.
VIZ_JS = ("(function(){try{" + _MEDIA_PICK + """
if(!m)return '[]';
var V=window.__fjV;
if(!V||V.m!==m){
 if(V){try{V.ctx.close();}catch(e){}}
 window.__fjV=null;
 var AC=window.AudioContext||window.webkitAudioContext,cap=m.captureStream||m.mozCaptureStream;
 if(!AC||!cap)return 'null';
 var ctx=new AC({latencyHint:'interactive'}),st=cap.call(m);
 if(!st.getAudioTracks().length){ctx.close();return '[]';}
 var an=ctx.createAnalyser();
 an.fftSize=1024;an.smoothingTimeConstant=0.3;an.minDecibels=-85;an.maxDecibels=-20;
 ctx.createMediaStreamSource(st).connect(an);
 V=window.__fjV={m:m,ctx:ctx,an:an,buf:new Uint8Array(an.frequencyBinCount)};
}
if(V.ctx.state==='suspended')V.ctx.resume();
V.an.getByteFrequencyData(V.buf);
var n=NBANDS,out=[],buf=V.buf,len=buf.length,sr=V.ctx.sampleRate,hz=sr/(2*len),
 lo=60,hi=Math.min(14000,sr*0.42),k=Math.pow(hi/lo,1/n);
for(var i=0;i<n;i++){
 var f0=lo*Math.pow(k,i),f1=f0*k,a=Math.floor(f0/hz),b=Math.ceil(f1/hz),v;
 if(b-a<=1){ /* band narrower than one FFT bin (the bass): interpolate so neighbouring bars differ */
  var c=Math.sqrt(f0*f1)/hz,i0=Math.floor(c),fr=c-i0;
  v=(buf[i0]*(1-fr)+buf[Math.min(len-1,i0+1)]*fr)/255;
 }else{
  var s=0,mx=0;
  for(var j=a;j<b&&j<len;j++){s+=buf[j];if(buf[j]>mx)mx=buf[j];}
  v=(s/(b-a)*0.5+mx*0.5)/255;
 }
 v*=0.85+0.45*i/n; /* offset the natural roll-off of the highs */
 out.push(Math.round(Math.min(1,v)*100)/100);
}
return JSON.stringify(out);
}catch(e){return 'null';}})()""").replace("NBANDS", str(VIZ_BARS))
VIZ_STOP_JS = "(function(){try{var V=window.__fjV;if(V){V.ctx.close();window.__fjV=null;}}catch(e){}})()"


def rounded_pixmap(pm, size, radius):
    """Centre-crop to a square and round the corners (rendered at 2x so it stays crisp on hi-dpi)."""
    s2 = size * 2
    pm = pm.scaled(s2, s2, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(s2, s2)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, s2, s2), radius * 2, radius * 2)
    p.setClipPath(path)
    p.drawPixmap((s2 - pm.width()) // 2, (s2 - pm.height()) // 2, pm)
    p.end()
    out.setDevicePixelRatio(2)
    return out


def safe_art_url(url):
    """Artwork URLs come from the page, so only fetch plain https hosts (never IPs / localhost / LAN)."""
    u = QUrl(url)
    host = u.host().lower()
    if u.scheme() != "https" or not host or host == "localhost" or host.endswith((".local", ".internal")):
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
        return False
    except ValueError:
        return True


def art_palette(pm):
    """A vivid (main, alt) colour pair taken from artwork or an icon; None if the image is basically greyscale."""
    if pm is None or pm.isNull():
        return None
    img = pm.toImage().scaled(48, 48, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    bins = [[0.0, 0.0, 0.0, 0.0] for _ in range(24)]  # per hue slice: weight, r, g, b
    total = vivid = 0
    for y in range(img.height()):
        for x in range(img.width()):
            r, g, b, a = img.pixelColor(x, y).getRgb()
            if a < 128:
                continue
            total += 1
            h, sat, val = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            if sat < 0.2 or val < 0.2:
                continue
            vivid += 1
            wgt = sat * val
            bk = bins[int(h * 24) % 24]
            bk[0] += wgt
            bk[1] += r * wgt
            bk[2] += g * wgt
            bk[3] += b * wgt
    if not total or vivid < total * 0.03:
        return None
    best = max(range(24), key=lambda k: bins[k][0] + 0.5 * (bins[(k - 1) % 24][0] + bins[(k + 1) % 24][0]))
    wsum = rs = gs = bs = 0.0
    for j in (-1, 0, 1):
        bk = bins[(best + j) % 24]
        wsum, rs, gs, bs = wsum + bk[0], rs + bk[1], gs + bk[2], bs + bk[3]
    if wsum <= 0:
        return None
    return derive_accent("#%02x%02x%02x" % (int(rs / wsum), int(gs / wsum), int(bs / wsum)))


class SideGrip(QWidget):
    """Invisible drag handle on the sidebar's right edge. Drag = resize, double-click = reset."""
    def __init__(self, parent, get_w, set_w, done, reset):
        super().__init__(parent)
        self.get_w, self.set_w, self.done, self.reset = get_w, set_w, done, reset
        self.dragging, self._hover = False, False
        self._x0 = self._w0 = 0
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.hide()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self._x0, self._w0 = e.globalPosition().x(), self.get_w()
            self.update()

    def mouseMoveEvent(self, e):
        if self.dragging:
            self.set_w(int(self._w0 + e.globalPosition().x() - self._x0))

    def mouseReleaseEvent(self, e):
        if self.dragging:
            self.dragging = False
            self.update()
            self.done()

    def mouseDoubleClickEvent(self, e):
        self.reset()

    def paintEvent(self, e):
        if not (self._hover or self.dragging):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent_color(210 if self.dragging else 120))
        p.drawRoundedRect(QRectF(6.5, self.height() / 2 - 20, 3, 40), 1.5, 1.5)
        p.end()


class ClickLabel(QLabel):
    clicked = pyqtSignal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class ElidedLabel(ClickLabel):
    def __init__(self, text=""):
        super().__init__()
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, t):
        self._full = t or ""
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()


class MediaButton(FadeButton):
    """Round transport button with a hand-drawn icon (no emoji / font dependence)."""
    def __init__(self, kind, size=28, solid=False):
        super().__init__(size // 2)
        self.kind, self.solid = kind, solid
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_kind(self, k):
        if k != self.kind:
            self.kind = k
            self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width() / 2, self.height() / 2
        if self.solid:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawEllipse(QRectF(1, 1, self.width() - 2, self.height() - 2))
        col = QColor(228, 237, 243, int(185 + 70 * self._h))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        k = self.kind

        def tri(*pts):
            p.drawPolygon(QPolygonF([QPointF(w + x, h + y) for x, y in pts]))
        if k == "play":
            tri((-4, -6.5), (-4, 6.5), (6, 0))
        elif k == "pause":
            p.drawRoundedRect(QRectF(w - 5, h - 6, 3.6, 12), 1, 1)
            p.drawRoundedRect(QRectF(w + 1.4, h - 6, 3.6, 12), 1, 1)
        elif k == "next":
            tri((-6, -5.5), (-6, 5.5), (3, 0))
            p.drawRoundedRect(QRectF(w + 4, h - 5.5, 2, 11), 1, 1)
        elif k == "prev":
            tri((6, -5.5), (6, 5.5), (-3, 0))
            p.drawRoundedRect(QRectF(w - 6, h - 5.5, 2, 11), 1, 1)
        elif k in ("sound", "mute"):
            tri((-7, -2.5), (-4, -2.5), (0, -6), (0, 6), (-4, 2.5), (-7, 2.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(col, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            if k == "sound":
                p.drawArc(QRectF(w - 3, h - 4, 8, 8), -50 * 16, 100 * 16)
                p.drawArc(QRectF(w - 6, h - 7, 14, 14), -50 * 16, 100 * 16)
            else:
                p.drawLine(QPointF(w + 3, h - 3), QPointF(w + 8, h + 3))
                p.drawLine(QPointF(w + 8, h - 3), QPointF(w + 3, h + 3))
        p.end()


class SeekBar(QWidget):
    """Thin progress line; click or drag to seek."""
    seek = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(10)
        self.frac, self.ok, self._drag = 0.0, False, False
        self.color = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_value(self, f, ok):
        if self._drag:
            return
        self.frac, self.ok = max(0.0, min(1.0, f)), ok
        self.update()

    def set_color(self, hexcol):
        self.color = hexcol
        self.update()

    def _fill(self):
        c = QColor(self.color) if self.color else accent_color(235)
        c.setAlpha(235)
        return c

    def _at(self, e):
        return max(0.0, min(1.0, e.position().x() / max(1, self.width())))

    def mousePressEvent(self, e):
        if self.ok and e.button() == Qt.MouseButton.LeftButton:
            self._drag, self.frac = True, self._at(e)
            self.update()

    def mouseMoveEvent(self, e):
        if self._drag:
            self.frac = self._at(e)
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.seek.emit(self._at(e))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        y, th = self.height() / 2 - 1.5, 3
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(QRectF(0, y, self.width(), th), 1.5, 1.5)
        if self.ok and self.frac > 0:
            p.setBrush(self._fill())
            p.drawRoundedRect(QRectF(0, y, max(3.0, self.width() * self.frac), th), 1.5, 1.5)
        p.end()


class Visualizer(QWidget):
    """Spectrum bars. Colours follow the playing track's artwork and fade smoothly between tracks."""
    def __init__(self):
        super().__init__()
        self.setFixedHeight(20)
        self.vals = [0.0] * VIZ_BARS
        self.pal = None  # (main, alt) hex pair from the artwork; None = follow the app accent
        self.c1 = [float(x) for x in _rgb(ACCENT["main"])]
        self.c2 = [float(x) for x in _rgb(ACCENT["alt"])]
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def _target(self):
        return self.pal or (ACCENT["main"], ACCENT["alt"])

    def set_palette(self, pal, snap=False):
        self.pal = pal
        if snap:
            m, a = self._target()
            self.c1, self.c2 = [float(x) for x in _rgb(m)], [float(x) for x in _rgb(a)]
            self.update()

    def step_colors(self, k=0.08):
        m, a = self._target()
        for cur, tgt in ((self.c1, _rgb(m)), (self.c2, _rgb(a))):
            for i in range(3):
                cur[i] += (tgt[i] - cur[i]) * k

    def paintEvent(self, e):
        w, hh, gap = self.width(), self.height(), 2.0
        bw = max(1.5, (w - gap * (VIZ_BARS - 1)) / VIZ_BARS)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(self.vals):
            t = i / max(1, VIZ_BARS - 1)
            col = QColor(*(int(self.c1[j] + (self.c2[j] - self.c1[j]) * t) for j in range(3)))
            v = max(0.0, min(1.0, v))
            col.setAlpha(int(120 + 135 * v))  # louder bars glow brighter
            p.setBrush(col)
            bh = max(2.0, v * hh)
            p.drawRoundedRect(QRectF(i * (bw + gap), hh - bh, bw, bh), min(bw / 2, 2), min(bw / 2, 2))
        p.end()


class MediaPlayer(QFrame):
    """Zen-style mini player pinned to the bottom of the sidebar. Follows whichever tab last played audio."""
    def __init__(self, browser, parent):
        super().__init__(parent)
        self.b = browser
        self.setObjectName("media")
        self.tab = None
        self.compact = False
        self.paused = True
        self.viz_on = bool(browser.settings.get("visualizer"))
        self._misses = 0
        self._art_key, self._art_cache, self._art_pending = None, {}, set()
        self._real, self._real_ts = [], -999.0
        self._art_pal, self._viz_sent = {}, 0.0
        self.net = QNetworkAccessManager(self)

        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(10, 10, 10, 8)
        self.lay.setSpacing(6)
        self.top = QHBoxLayout()
        self.top.setSpacing(9)
        self.art = ClickLabel()
        self.art.setFixedSize(36, 36)
        self.art.setCursor(Qt.CursorShape.PointingHandCursor)
        self.art.clicked.connect(self._art_clicked)
        self.textw = QWidget()
        tl = QVBoxLayout(self.textw)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(1)
        self.title, self.sub = ElidedLabel(), ElidedLabel()
        self.title.setObjectName("mediatitle")
        self.sub.setObjectName("mediasub")
        self.title.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title.clicked.connect(self.goto)
        self.sub.clicked.connect(self.goto)
        tl.addStretch(1)
        tl.addWidget(self.title)
        tl.addWidget(self.sub)
        tl.addStretch(1)
        self.close_btn = FadeButton(8)
        self.close_btn.setObjectName("close")
        self.close_btn.setText("✕")
        self.close_btn.setToolTip("Stop and hide")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.stop_and_hide)
        self.top.addWidget(self.art)
        self.top.addWidget(self.textw, 1)
        self.top.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.lay.addLayout(self.top)

        self.viz = Visualizer()
        self.viz.hide()
        self.lay.addWidget(self.viz)
        self.seekbar = SeekBar()
        self.seekbar.seek.connect(lambda f: self._run(media_js("seek", f)))
        self.lay.addWidget(self.seekbar)

        self.ctl_wrap = QWidget()
        cl = QHBoxLayout(self.ctl_wrap)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)
        self.prev_btn, self.play_btn = MediaButton("prev", 28), MediaButton("play", 32, solid=True)
        self.next_btn, self.mute_btn = MediaButton("next", 28), MediaButton("sound", 28)
        self.prev_btn.clicked.connect(lambda: self.control("prev"))
        self.play_btn.clicked.connect(lambda: self.control("toggle"))
        self.next_btn.clicked.connect(lambda: self.control("next"))
        self.mute_btn.clicked.connect(self.toggle_mute)
        cl.addStretch(1)
        for w in (self.prev_btn, self.play_btn, self.next_btn, self.mute_btn):
            cl.addWidget(w)
        cl.addStretch(1)
        self.lay.addWidget(self.ctl_wrap)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(700)
        self.poll_timer.timeout.connect(self.poll)
        self.viz_timer = QTimer(self)
        self.viz_timer.setInterval(VIZ_TICK_MS)
        self.viz_timer.timeout.connect(self._viz_tick)
        self.hide()

    # ----- lifecycle -----
    def on_audible(self, tab, audible):
        if audible and not tab.closing and tab is not self.tab:
            self.attach(tab)

    def attach(self, tab):
        if self.tab is not None:
            self._stop_viz_js(self.tab)
        self.tab, self.paused, self._misses, self._art_key = tab, False, 0, None
        self._real, self._real_ts = [], -999.0
        self._viz_sent = 0.0
        self.play_btn.set_kind("pause")
        self.mute_btn.set_kind("mute" if tab.page().isAudioMuted() else "sound")
        self.title.setText(tab.title() or self.b.host_of(tab.url()) or "Playing")
        self.sub.setText(self.b.host_of(tab.url()))
        self.seekbar.set_value(0, False)
        self._set_art("")
        self.viz.setVisible(self.viz_on and not self.compact)
        self.show()
        self.poll_timer.start()
        self.poll()
        self._sync_viz()

    def clear(self):
        if self.tab is not None:
            self._stop_viz_js(self.tab)
        self.tab = None
        self.poll_timer.stop()
        self.viz_timer.stop()
        self.hide()

    def stop_and_hide(self):
        self._run(media_js("pause"))
        self.clear()

    def set_compact(self, c):
        self.compact = c
        for w in (self.textw, self.close_btn, self.seekbar, self.ctl_wrap):
            w.setVisible(not c)
        self.lay.setContentsMargins(*((6, 6, 6, 6) if c else (10, 10, 10, 8)))
        self.top.setSpacing(0 if c else 9)
        self.top.setAlignment(self.art, Qt.AlignmentFlag.AlignHCenter if c else Qt.AlignmentFlag.AlignLeft)
        self.viz.setVisible(self.viz_on and not c and self.tab is not None)
        self._sync_viz()

    # ----- state polling -----
    def _run(self, js):
        if self.tab is None:
            return
        try:
            self.tab.page().runJavaScript(js)
        except RuntimeError:
            self.clear()

    def poll(self):
        t = self.tab
        if t is None:
            return
        try:
            if self.b.stack.indexOf(t) < 0 or t.closing:
                self.clear()
                return
            t.page().runJavaScript(MEDIA_STATE_JS, lambda r, t=t: self._on_state(t, r))
        except RuntimeError:
            self.clear()

    def _on_state(self, t, raw):
        if t is not self.tab:
            return
        try:
            d = json.loads(raw) if raw else {"none": 1}
        except ValueError:
            d = {"none": 1}
        if d.get("none"):
            self._misses += 1
            if self._misses >= 4:  # tab navigated away / media element is gone
                self.clear()
            return
        self._misses = 0
        self.update_state(d)

    def update_state(self, d):
        t = self.tab
        title = d.get("title") or t.title() or "Playing"
        sub = d.get("artist") or self.b.host_of(t.url())
        self.title.setText(title)
        self.sub.setText(sub)
        self.setToolTip(title)
        paused = bool(d.get("paused"))
        if paused != self.paused:
            self.paused = paused
            self.play_btn.set_kind("play" if paused else "pause")
            self._sync_viz()
        dur = d.get("d") or 0
        self.seekbar.set_value((d.get("t") or 0) / dur if dur > 0 else 0, dur > 0)
        self.mute_btn.set_kind("mute" if t.page().isAudioMuted() else "sound")
        self._set_art(d.get("art") or "")

    # ----- controls -----
    def control(self, action):
        if action == "toggle":  # optimistic UI; the next poll confirms
            self.paused = not self.paused
            self.play_btn.set_kind("play" if self.paused else "pause")
            self._sync_viz()
        self._run(media_js(action))
        QTimer.singleShot(250, self.poll)

    def toggle_mute(self):
        if self.tab is None:
            return
        pg = self.tab.page()
        pg.setAudioMuted(not pg.isAudioMuted())
        self.tab.row.update_audio(self.tab)
        self.mute_btn.set_kind("mute" if pg.isAudioMuted() else "sound")

    def goto(self):
        if self.tab is not None:
            i = self.b.stack.indexOf(self.tab)
            if i >= 0:
                self.b.tabs.setCurrentRow(i)

    def _art_clicked(self):
        if self.compact:
            self.control("toggle")
        else:
            self.goto()

    def contextMenuEvent(self, e):
        if self.tab is None:
            return
        m = QMenu(self)
        m.addAction("Go to tab", self.goto)
        muted = self.tab.page().isAudioMuted()
        m.addAction("Unmute tab" if muted else "Mute tab", self.toggle_mute)
        a = m.addAction("Audio visualiser")
        a.setCheckable(True)
        a.setChecked(self.viz_on)
        a.triggered.connect(lambda on: self.b.set_visualizer(bool(on)))
        m.addSeparator()
        m.addAction("Stop and hide", self.stop_and_hide)
        m.exec(e.globalPos())

    # ----- artwork -----
    def _fallback_art(self):
        pm = QPixmap(72, 72)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent_color(45))
        p.drawRoundedRect(QRectF(0, 0, 72, 72), rr(20), rr(20))
        ic = self.tab.icon() if self.tab is not None else QIcon()
        if not ic.isNull():
            ip = ic.pixmap(40, 40)
            p.drawPixmap(16, 16, 40, 40, ip)
        else:
            p.setPen(accent_color(255))
            f = QFont(self.font())
            f.setPixelSize(34)
            p.setFont(f)
            p.drawText(QRectF(0, 0, 72, 72), Qt.AlignmentFlag.AlignCenter, "♪")
        p.end()
        pm.setDevicePixelRatio(2)
        return pm

    def _icon_palette(self):
        ic = self.tab.icon() if self.tab is not None else QIcon()
        return art_palette(ic.pixmap(32, 32)) if not ic.isNull() else None

    def _apply_palette(self, pal):
        self.viz.set_palette(pal, snap=not self.viz_timer.isActive())
        self.seekbar.set_color(pal[0] if pal else None)

    def _set_art(self, url):
        icon_key = self.tab.icon().cacheKey() if (self.tab is not None and not url) else 0
        key = (url, icon_key)
        if key == self._art_key:
            return
        self._art_key = key
        pm = self._art_cache.get(url) if url else None
        if pm is not None:
            self.art.setPixmap(pm)
            self._apply_palette(self._art_pal.get(url, VIZ_NEUTRAL))
            return
        self.art.setPixmap(self._fallback_art())
        # the Qt network stack doesn't go through the browser's proxy, so skip remote artwork while the VPN is on
        can_fetch = bool(url) and not self.b.settings.get("vpn") and safe_art_url(url)
        if can_fetch and url not in self._art_pending:
            self._art_pending.add(url)
            req = QNetworkRequest(QUrl(url))
            req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
            rep = self.net.get(req)
            rep.downloadProgress.connect(lambda got, _tot, r=rep: r.abort() if got > 3_000_000 else None)
            rep.finished.connect(lambda r=rep, u=url: self._art_done(r, u))
        if not can_fetch:  # no artwork to read: tint from the site's icon, or the app accent if that is colourless
            self._apply_palette(self._icon_palette())

    def _art_done(self, rep, url):
        self._art_pending.discard(url)
        data = bytes(rep.readAll()) if rep.error() == QNetworkReply.NetworkError.NoError else b""
        rep.deleteLater()
        pm = QPixmap()
        if data and pm.loadFromData(data):
            if len(self._art_cache) >= 8:
                self._art_cache.pop(next(iter(self._art_cache)))
            if len(self._art_pal) >= 16:
                self._art_pal.pop(next(iter(self._art_pal)))
            self._art_cache[url] = rounded_pixmap(pm, 36, 9)
            self._art_pal[url] = art_palette(pm) or VIZ_NEUTRAL
            self._art_key = None  # re-apply (picture and colours) on the next poll
            self.poll()
        elif self._art_key and self._art_key[0] == url:
            self._apply_palette(self._icon_palette())

    # ----- visualiser -----
    def set_viz(self, on):
        self.viz_on = on
        if not on and self.tab is not None:
            self._stop_viz_js(self.tab)
        self.viz.vals = [0.0] * VIZ_BARS
        self.viz.setVisible(on and not self.compact and self.tab is not None)
        self._sync_viz()

    def _stop_viz_js(self, tab):
        try:
            tab.page().runJavaScript(VIZ_STOP_JS)
        except RuntimeError:
            pass

    def _sync_viz(self):
        run = (self.viz_on and self.tab is not None and not self.compact and self.isVisible()
               and (not self.paused or max(self.viz.vals) > 0.01))
        if run and not self.viz_timer.isActive():
            self.viz_timer.start()
        elif not run:
            self.viz_timer.stop()

    def showEvent(self, e):
        super().showEvent(e)
        self._sync_viz()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.viz_timer.stop()

    def _viz_data(self, t, raw):
        self._viz_sent = 0.0  # reply received, so the next tick may ask again
        if t is not self.tab or not raw:
            return
        try:
            data = json.loads(raw)
        except ValueError:
            return
        if isinstance(data, list) and len(data) == VIZ_BARS and max(data) > 0.03:
            self._real, self._real_ts = data, time.monotonic()

    @staticmethod
    def _ambient(now):
        out = []
        for i in range(VIZ_BARS):
            a = math.sin(now * (2.1 + 0.37 * i) + i * 1.7)
            b = math.sin(now * (1.3 + 0.21 * i) + i * 0.6)
            pulse = 0.7 + 0.3 * math.sin(now * 2.4)
            out.append(max(0.06, min(1.0, (0.62 - 0.014 * i) * (0.4 + 0.4 * a * a + 0.2 * b) * pulse)))
        return out

    def _viz_tick(self):
        now = time.monotonic()
        vals = self.viz.vals
        if self.tab is not None and not self.paused:
            # one request in flight at a time: stacked-up calls used to arrive late and in bursts
            if self._viz_sent == 0.0 or now - self._viz_sent > 0.3:
                self._viz_sent = now
                try:
                    self.tab.page().runJavaScript(VIZ_JS, lambda r, t=self.tab: self._viz_data(t, r))
                except RuntimeError:
                    self._viz_sent = 0.0
                    return
            # real spectrum when the page lets us tap it, otherwise gentle ambient motion
            targets = self._real if (now - self._real_ts) < 1.5 and len(self._real) == VIZ_BARS else self._ambient(now)
        else:
            targets = [0.0] * VIZ_BARS
        for i in range(VIZ_BARS):
            vals[i] += (targets[i] - vals[i]) * (0.85 if targets[i] > vals[i] else 0.3)  # snap up on beats, fall quickly
        self.viz.step_colors()
        self.viz.update()
        if self.paused and max(vals) < 0.01:
            self.viz_timer.stop()

# ---------- main window ----------
class HoverBar(QFrame):
    """Floating pill (link URL / short messages) drawn over the page, so showing it never resizes the window."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("hoverbar")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 14, 5)
        lay.setSpacing(8)
        self.icon = QLabel()
        self.icon.setFixedSize(16, 16)
        self.icon.setScaledContents(True)
        self.text = QLabel()
        lay.addWidget(self.icon)
        lay.addWidget(self.text)
        self.setStyleSheet("#hoverbar{background:rgba(16,28,40,238);border:1px solid rgba(255,255,255,0.12);"
                           "border-radius:13px} #hoverbar QLabel{background:transparent;color:#e4edf3;font-size:12px}")
        self.hide()

    def set_content(self, text, pm=None):
        parent = self.parentWidget()
        maxw = max(160, (parent.width() if parent else 800) - 120)
        self.text.setText(self.text.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, maxw))
        if pm is not None and not pm.isNull():
            self.icon.setPixmap(pm)
            self.icon.show()
        else:
            self.icon.hide()
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()

    def place(self):
        parent = self.parentWidget()
        if parent is not None:
            self.move(16, parent.height() - self.height() - 16)


# ---------- scratchpad: drop anything on the sidebar to keep a copy, then copy / save it again later ----------
SCRATCH_DIR = DATA_DIR / "scratchpad"
SCRATCH_MIME = "application/x-fjord-scratch"   # marks drags that start from a Scratchpad card (never re-added)
SCRATCH_W = 340
SCRATCH_MAX_ITEMS = 200
SCRATCH_MAX_TEXT = 200_000
SCRATCH_MAX_FILE = 100 * 1024 * 1024
SCRATCH_MAX_IMG = 25 * 1024 * 1024
IMG_URL_RE = re.compile(r"\.(?:png|jpe?g|gif|webp|bmp)(?:[?#]|$)", re.I)
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)


def draw_glyph(p, kind, rect, color, width=1.6):
    """Hand-drawn line icons on a 24x24 grid, so no icon font is needed. `width` is the stroke in pixels."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.x(), rect.y())
    p.scale(rect.width() / 24.0, rect.height() / 24.0)
    pen = QPen(QColor(color))
    pen.setWidthF(width * 24.0 / max(1.0, rect.width()))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()

    def line(*pts):
        path.moveTo(*pts[0])
        for q in pts[1:]:
            path.lineTo(*q)

    if kind == "link":
        p.translate(12, 12)
        p.rotate(-45)
        p.drawRoundedRect(QRectF(-9.5, -3.6, 11, 7.2), 3.6, 3.6)
        p.drawRoundedRect(QRectF(-1.5, -3.6, 11, 7.2), 3.6, 3.6)
        p.restore()
        return
    if kind in ("scratch", "file"):  # a note with a folded corner
        path.moveTo(7, 3.5)
        path.lineTo(15.2, 3.5)
        path.lineTo(20.5, 8.8)
        path.lineTo(20.5, 17)
        path.quadTo(20.5, 20.5, 17, 20.5)
        path.lineTo(7, 20.5)
        path.quadTo(3.5, 20.5, 3.5, 17)
        path.lineTo(3.5, 7)
        path.quadTo(3.5, 3.5, 7, 3.5)
        path.moveTo(15.2, 3.5)
        path.lineTo(15.2, 7.3)
        path.quadTo(15.2, 8.8, 16.7, 8.8)
        path.lineTo(20.5, 8.8)
        if kind == "scratch":
            line((7.5, 13), (16.5, 13))
            line((7.5, 16.6), (12.5, 16.6))
    elif kind == "copy":
        path.addRoundedRect(QRectF(4.5, 9.5, 10, 10), 2.4, 2.4)
        path.moveTo(9.5, 8.2)
        path.lineTo(9.5, 6.9)
        path.quadTo(9.5, 4.5, 11.9, 4.5)
        path.lineTo(17.1, 4.5)
        path.quadTo(19.5, 4.5, 19.5, 6.9)
        path.lineTo(19.5, 12.1)
        path.quadTo(19.5, 14.5, 17.1, 14.5)
        path.lineTo(15.8, 14.5)
    elif kind == "save":
        line((12, 4), (12, 14.8))
        line((7.6, 10.6), (12, 15), (16.4, 10.6))
        path.moveTo(4.5, 15.5)
        path.lineTo(4.5, 17.3)
        path.quadTo(4.5, 19.5, 6.7, 19.5)
        path.lineTo(17.3, 19.5)
        path.quadTo(19.5, 19.5, 19.5, 17.3)
        path.lineTo(19.5, 15.5)
    elif kind == "trash":
        line((4.8, 7), (19.2, 7))
        line((9.5, 7), (9.5, 4.8), (14.5, 4.8), (14.5, 7))
        path.moveTo(6.5, 7)
        path.lineTo(7.4, 17.8)
        path.quadTo(7.5, 19.5, 9.2, 19.5)
        path.lineTo(14.8, 19.5)
        path.quadTo(16.5, 19.5, 16.6, 17.8)
        path.lineTo(17.5, 7)
        line((10.2, 10.8), (10.2, 16))
        line((13.8, 10.8), (13.8, 16))
    elif kind == "text":
        line((5, 7), (19, 7))
        line((5, 12), (19, 12))
        line((5, 17), (13, 17))
    elif kind == "sidebar":
        path.addRoundedRect(QRectF(3.5, 5, 17, 14), 3.2, 3.2)
        line((9.5, 5.4), (9.5, 18.6))
    elif kind in ("back", "forward"):
        s = -1 if kind == "back" else 1
        line((12 - 7 * s, 12), (12 + 7 * s, 12))
        line((12 + 1.5 * s, 5.5), (12 + 7.5 * s, 12), (12 + 1.5 * s, 18.5))
    elif kind == "reload":
        rr = QRectF(4.5, 4.5, 15, 15)
        path.arcMoveTo(rr, 40)
        path.arcTo(rr, 40, -280)
        end = path.currentPosition()
        d = (math.sin(math.radians(120)), math.cos(math.radians(120)))  # clockwise tangent at the arc's end
        for a in (145, -145):
            ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
            bx, by = d[0] * ca - d[1] * sa, d[0] * sa + d[1] * ca
            path.moveTo(end)
            path.lineTo(end.x() + bx * 4.6, end.y() + by * 4.6)
    elif kind == "stop":
        line((7, 7), (17, 17))
        line((17, 7), (7, 17))
    elif kind in ("star", "star_on"):
        pts = []
        for i in range(10):
            rad = 9.2 if i % 2 == 0 else 4.1
            ang = math.radians(-90 + i * 36)
            pts.append((12 + rad * math.cos(ang), 12.7 + rad * math.sin(ang)))
        line(*pts)
        path.closeSubpath()
        if kind == "star_on":
            p.setBrush(QColor(color))
    elif kind in ("shield", "shield_on"):
        path.moveTo(12, 3.4)
        path.lineTo(19.2, 6.2)
        path.lineTo(19.2, 11.6)
        path.cubicTo(19.2, 16.2, 16, 19.2, 12, 20.8)
        path.cubicTo(8, 19.2, 4.8, 16.2, 4.8, 11.6)
        path.lineTo(4.8, 6.2)
        path.closeSubpath()
        if kind == "shield_on":
            line((8.7, 12), (11.1, 14.4), (15.4, 9.6))
    elif kind == "speed_eco":  # a leaf with its stem and centre vein
        path.moveTo(5.5, 18.5)
        path.cubicTo(4.5, 10, 9, 5.5, 19, 5)
        path.cubicTo(19.5, 14, 15, 19.5, 5.5, 18.5)
        line((3.5, 20.5), (12, 12))
    elif kind == "speed_turbo":  # lightning bolt
        line((13.5, 3), (6, 13.5), (12, 13.5), (10.5, 21), (18, 10), (12, 10))
        path.closeSubpath()
    elif kind == "speed":  # speedometer: an arc with a needle pointing straight up
        rr = QRectF(3.5, 3.8, 17, 17)
        path.arcMoveTo(rr, 210)
        path.arcTo(rr, 210, -240)
        line((12, 12.3), (12, 6.1))
        path.addEllipse(QPointF(12, 12.3), 1.1, 1.1)
    elif kind == "puzzle":  # a jigsaw piece: square body with a knob on top and one on the right
        path.moveTo(4.5, 8.5)
        path.lineTo(9.6, 8.5)
        path.cubicTo(8.2, 3.8, 15.8, 3.8, 14.4, 8.5)
        path.lineTo(17, 8.5)
        path.lineTo(17, 10.6)
        path.cubicTo(21.7, 9.2, 21.7, 16.8, 17, 15.4)
        path.lineTo(17, 20)
        path.lineTo(4.5, 20)
        path.closeSubpath()
    elif kind == "dots":
        p.setBrush(QColor(color))
        for cx in (5.5, 12, 18.5):
            path.addEllipse(QPointF(cx, 12), 1.0, 1.0)
    else:  # image
        path.addRoundedRect(QRectF(3.5, 4.5, 17, 15), 3, 3)
        line((4.5, 17), (9.5, 12), (13, 15.5), (15.5, 13), (19.5, 17))
        path.addEllipse(QPointF(15.8, 9.2), 1.5, 1.5)
    p.drawPath(path)
    p.restore()


def safe_name(name, default="file"):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(name)).strip(" .")
    return name[:120] or default


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return "%d B" % n if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f GB" % n


def ago(ts):
    s = max(0, time.time() - (ts or 0))
    if s < 60:
        return "just now"
    if s < 3600:
        return "%dm ago" % (s // 60)
    if s < 86400:
        return "%dh ago" % (s // 3600)
    return "%dd ago" % (s // 86400)


def sniff_ext(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    return None


def data_url_bytes(url):
    head, _, payload = url.partition(",")
    return base64.b64decode(payload) if head.endswith(";base64") else urllib.parse.unquote_to_bytes(payload)


def thumb_pixmap(path, size=44):
    r = QImageReader(str(path))
    r.setAutoTransform(True)
    sz = r.size()
    if sz.isValid() and sz.width() > 0 and sz.height() > 0:
        k = (size * 2) / float(min(sz.width(), sz.height()))
        if k < 1:
            r.setScaledSize(QSize(max(1, int(sz.width() * k)), max(1, int(sz.height() * k))))
    img = r.read()
    if img.isNull():
        return None
    return rounded_pixmap(QPixmap.fromImage(img), size, 10)


class ScratchStore:
    """The Scratchpad's contents. Text and links live in scratchpad.json; files and images are copied into
    ~/.fjord_browser/scratchpad/<id>/ so they survive the original being moved or deleted."""
    def __init__(self):
        SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        data = jload("scratchpad.json", [])
        self.items = []
        for it in data if isinstance(data, list) else []:
            if not isinstance(it, dict) or it.get("kind") not in ("text", "link", "image", "file"):
                continue
            if not re.fullmatch(r"[0-9a-f]{12}", str(it.get("id", ""))):
                continue
            if it["kind"] in ("text", "link") and not isinstance(it.get("text"), str):
                continue
            if it["kind"] in ("image", "file") and self.path(it) is None:
                continue
            self.items.append(it)

    def save(self):
        jsave("scratchpad.json", self.items)

    def path(self, it):
        f = it.get("file")
        if not f:
            return None
        try:
            p = (SCRATCH_DIR / f).resolve()
            return p if SCRATCH_DIR.resolve() in p.parents and p.is_file() else None
        except OSError:
            return None

    def _push(self, it):
        it["ts"] = time.time()
        self.items.insert(0, it)
        while len(self.items) > SCRATCH_MAX_ITEMS:
            self.remove(self.items[-1]["id"], save=False)
        self.save()
        return it

    def _dir(self):
        rid = secrets.token_hex(6)
        (SCRATCH_DIR / rid).mkdir(parents=True, exist_ok=True)
        return rid, SCRATCH_DIR / rid

    def remove(self, rid, save=True):
        self.items = [i for i in self.items if i["id"] != rid]
        if re.fullmatch(r"[0-9a-f]{12}", rid):
            shutil.rmtree(SCRATCH_DIR / rid, ignore_errors=True)
        if save:
            self.save()

    def clear(self):
        for it in list(self.items):
            self.remove(it["id"], save=False)
        self.save()

    def add_text(self, text):
        text = (text or "").strip()[:SCRATCH_MAX_TEXT]
        if not text:
            return None
        kind = "text"
        if URL_RE.fullmatch(text):
            kind = "link"
            if text.lower().startswith("www."):
                text = "https://" + text
        for it in self.items:  # same thing again: bring it to the top instead of duplicating
            if it["kind"] == kind and it.get("text") == text:
                self.items.remove(it)
                return self._push(it)
        return self._push({"id": secrets.token_hex(6), "kind": kind, "text": text})

    def add_file(self, src):
        src = Path(src)
        if not src.is_file():
            raise ValueError("Only files can be added, not folders")
        size = src.stat().st_size
        if size > SCRATCH_MAX_FILE:
            raise ValueError("%s is too large (100 MB max)" % src.name)
        rid, folder = self._dir()
        name = safe_name(src.name)
        dest = folder / name
        try:
            shutil.copy2(str(src), str(dest))
        except OSError:
            shutil.rmtree(folder, ignore_errors=True)
            raise ValueError("Couldn't copy %s" % src.name)
        kind = "image" if QImageReader(str(dest)).canRead() else "file"
        return self._push({"id": rid, "kind": kind, "file": rid + "/" + name, "size": size})

    def add_image_bytes(self, data, name=""):
        if not data or len(data) > SCRATCH_MAX_FILE:
            raise ValueError("That image couldn't be read")
        img = QImage.fromData(data)
        if img.isNull():
            raise ValueError("That image couldn't be read")
        ext = sniff_ext(data)
        stem = safe_name(Path(name).stem, "") if name else ""
        stem = stem or time.strftime("image-%H%M%S")
        rid, folder = self._dir()
        fname = stem + (ext or ".png")
        dest = folder / fname
        try:
            if ext:
                dest.write_bytes(data)
            elif not img.save(str(dest), "PNG"):
                raise OSError("encode failed")
        except OSError:
            shutil.rmtree(folder, ignore_errors=True)
            raise ValueError("Couldn't save that image")
        return self._push({"id": rid, "kind": "image", "file": rid + "/" + fname, "size": dest.stat().st_size})

    def add_qimage(self, img):
        rid, folder = self._dir()
        fname = time.strftime("image-%H%M%S.png")
        dest = folder / fname
        if not img.save(str(dest), "PNG"):
            shutil.rmtree(folder, ignore_errors=True)
            raise ValueError("Couldn't save that image")
        return self._push({"id": rid, "kind": "image", "file": rid + "/" + fname, "size": dest.stat().st_size})


class GlyphTile(QWidget):
    """A glyph in the accent colour, optionally on a soft rounded tile."""
    def __init__(self, kind, size, tile=True):
        super().__init__()
        self.kind, self.tile = kind, tile
        self.setFixedSize(size, size)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.tile:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent_color(36))
            p.drawRoundedRect(r, rr(11), rr(11))
        g = r.width() * (0.5 if self.tile else 1.0)
        draw_glyph(p, self.kind, QRectF(r.center().x() - g / 2, r.center().y() - g / 2, g, g), accent_color(), 1.6)
        p.end()


class GlyphButton(FadeButton):
    """Small round icon button (copy / save / remove) with the same soft hover as the rest of the UI."""
    def __init__(self, kind, tip, warn=False):
        super().__init__(9)
        self.kind = kind
        self.to = (240, 138, 138) if warn else (255, 255, 255)
        self.setToolTip(tip)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, e):
        super().paintEvent(e)
        h = self._h
        base = (142, 163, 180)
        col = QColor(*[int(base[i] + (self.to[i] - base[i]) * h) for i in range(3)])
        p = QPainter(self)
        side = 15.0
        draw_glyph(p, self.kind, QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side), col, 1.7)
        p.end()


def _mix(a, b, t):
    return QColor(*[int(a.getRgb()[i] + (b.getRgb()[i] - a.getRgb()[i]) * t) for i in range(3)])


class ToolIcon(FadeButton):
    """Toolbar button that paints a line icon. Every one has the same box, glyph size and stroke, so they line up."""
    SIZE, GLYPH, STROKE = 36, 18, 1.7

    def __init__(self, kind, tip="", slot=None):
        super().__init__(12)
        self.kind, self.active, self.tint = kind, False, None
        self.setObjectName("tbicon")
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        if slot:
            self.clicked.connect(slot)

    def set_kind(self, kind):
        if kind != self.kind:
            self.kind = kind
            self.update()

    def set_active(self, on, tint=None):
        """tint: optional colour (e.g. "#4ade80") for the highlight square and glyph; None uses the accent."""
        self.active = bool(on)
        self.tint = tint if on else None
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.kind == "star_on":
            col = QColor("#ffd166")
        elif self.active:
            col = QColor(self.tint or ACCENT["alt"])
            p.setPen(Qt.PenStyle.NoPen)
            bg = QColor(col)
            bg.setAlpha(40)
            p.setBrush(bg)
            rad = shape_radius(12, r)
            p.drawRoundedRect(r, rad, rad)
        elif not self.isEnabled():
            col = QColor(themed("#3f5163"))
        else:
            col = _mix(QColor(themed("#b5c6d4")), QColor("#ffffff"), self._h)
        g = float(self.GLYPH)
        if UI["mode"] == "mac":  # the icon swells a little under the pointer and dips when pressed
            g *= 1.0 + 0.12 * self._h - (0.08 if self.isDown() else 0.0)
        draw_glyph(p, self.kind, QRectF(r.center().x() - g / 2, r.center().y() - g / 2, g, g), col, self.STROKE)
        p.end()


# ----- customisable toolbar -----
TB_MIME = "application/x-fjord-tbitem"
# id -> label. Order here is the order hidden items show up in the customise tray.
TB_ITEMS = {"side": "Sidebar", "back": "Back", "fwd": "Forward", "reload": "Reload", "engine": "Search engine",
            "speed": "Speed", "vpn": "VPN / Proxy", "ext": "Extensions", "star": "Bookmark", "scratch": "Scratchpad",
            "menu": "Menu"}
TB_GLYPHS = {"side": "sidebar", "back": "back", "fwd": "forward", "reload": "reload", "speed": "speed", "vpn": "shield",
             "ext": "puzzle", "star": "star", "scratch": "scratch", "menu": "dots"}
TB_DEFAULT = ["side", "back", "fwd", "reload", "addr", "engine", "speed", "vpn", "ext", "star", "scratch", "menu"]
TB_FIXED = ("addr", "menu")  # these always stay on the toolbar, so you can never lock yourself out
TB_STRETCH = {"addr": 5, "space": 1}
_TB_INST = re.compile(r"^(sep|space)#\d+$")


def tb_base(iid):
    return iid.split("#")[0]


def clean_toolbar(raw):
    """Turn whatever settings.json holds into a valid toolbar order (unknown/duplicate ids dropped, addr + menu kept)."""
    if not isinstance(raw, list):
        return list(TB_DEFAULT)
    out = []
    for x in raw:
        if isinstance(x, str) and x not in out and (x in TB_ITEMS or x == "addr" or _TB_INST.match(x)):
            out.append(x)
    if "addr" not in out:
        out.insert(out.index("reload") + 1 if "reload" in out else 0, "addr")
    if "menu" not in out:
        out.append("menu")
    return out


class TbSeparator(QWidget):
    """A thin vertical divider line between toolbar buttons."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(11, ToolIcon.SIZE)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(QPen(QColor(255, 255, 255, 46), 1))
        x = self.width() // 2
        p.drawLine(x, 9, x, self.height() - 9)
        p.end()


class TbSpacer(QWidget):
    """Flexible empty space that pushes the buttons on either side apart. Only visible while customising."""
    def __init__(self):
        super().__init__()
        self.editing = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(ToolIcon.SIZE)
        self.setMinimumWidth(6)

    def set_editing(self, on):
        self.editing = on
        self.setMinimumWidth(34 if on else 6)
        self.update()

    def paintEvent(self, e):
        if not self.editing:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(255, 255, 255, 70)
        p.setPen(QPen(c, 1.3))
        y, x0, x1 = self.height() / 2.0, 8.0, self.width() - 8.0
        if x1 - x0 > 14:
            p.drawLine(QPointF(x0, y), QPointF(x1, y))
            p.drawLine(QPointF(x0, y), QPointF(x0 + 5, y - 4))
            p.drawLine(QPointF(x0, y), QPointF(x0 + 5, y + 4))
            p.drawLine(QPointF(x1, y), QPointF(x1 - 5, y - 4))
            p.drawLine(QPointF(x1, y), QPointF(x1 - 5, y + 4))
        p.end()


class TbOverlay(QWidget):
    """Sits on top of the toolbar while customising. It swallows clicks (so buttons don't fire), draws the dashed
    outlines and the drop marker, starts drags, and is the drop target for rearranging."""
    def __init__(self, ed):
        super().__init__(ed.win.toolbar)
        self.ed = ed
        self.hover = self.press = self.press_pos = self.marker = self.src = None
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.hide()

    def item_at(self, pos):
        for _i, iid, w in self.ed.visible_items():
            if iid != "addr" and w.geometry().contains(pos):
                return iid
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.press_pos = e.position().toPoint()
        self.press = self.item_at(self.press_pos)
        if self.press is None:  # bare toolbar still drags the window
            h = self.window().windowHandle()
            if h and not self.window().isFullScreen():
                h.startSystemMove()

    def mouseReleaseEvent(self, e):
        self.press = None

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if (e.buttons() & Qt.MouseButton.LeftButton) and self.press is not None \
                and (pos - self.press_pos).manhattanLength() >= QApplication.startDragDistance():
            iid, self.press = self.press, None
            self.ed.drag_item(iid, self.ed.widget(iid))
            return
        h = self.item_at(pos)
        if h != self.hover:
            self.hover = h
            self.setCursor(Qt.CursorShape.OpenHandCursor if h else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def contextMenuEvent(self, e):
        iid = self.item_at(e.pos())
        m = QMenu(self)
        if iid and tb_base(iid) not in TB_FIXED:
            m.addAction("Remove from toolbar", lambda: self.ed.remove(iid))
        m.addAction("Reset toolbar", self.ed.reset)
        m.addAction("Done", self.ed.stop)
        m.exec(e.globalPos())

    def _mime_id(self, e):
        if not e.mimeData().hasFormat(TB_MIME):
            return None
        return bytes(e.mimeData().data(TB_MIME)).decode("utf-8", "ignore")

    def dragEnterEvent(self, e):
        self.dragMoveEvent(e)

    def dragMoveEvent(self, e):
        if self._mime_id(e) is None:
            e.ignore()
            return
        e.acceptProposedAction()
        self.marker = self.ed.slot_at(e.position().toPoint().x())[1]
        self.update()

    def dragLeaveEvent(self, e):
        self.marker = None
        self.update()

    def dropEvent(self, e):
        iid = self._mime_id(e)
        if iid is None:
            return
        idx = self.ed.slot_at(e.position().toPoint().x())[0]
        self.marker = None
        e.acceptProposedAction()
        self.ed.pending = lambda: self.ed.place(iid, idx)  # applied once the drag has fully ended
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for _i, iid, w in self.ed.visible_items():
            if iid == "addr":
                continue
            r = QRectF(w.geometry()).adjusted(0.5, 0.5, -0.5, -0.5)
            hot = iid == self.hover
            p.setPen(Qt.PenStyle.NoPen)
            if iid == self.src:
                p.setBrush(QColor(0, 0, 0, 120))
            elif hot:
                p.setBrush(accent_color(34))
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, rr(12), rr(12))
            pen = QPen(accent_color(170 if hot else 90), 1.2)
            pen.setDashPattern([3.0, 3.0])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, rr(12), rr(12))
        if self.marker is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent_color())
            p.drawRoundedRect(QRectF(self.marker - 1.5, 3, 3, self.height() - 6), 1.5, 1.5)
        p.end()


class TbTile(QWidget):
    """A hidden toolbar item (or a new separator / flexible space) in the customise tray; drag it onto the toolbar."""
    W, H = 74, 62

    def __init__(self, ed, iid):
        super().__init__()
        self.ed, self.iid, self.press_pos = ed, iid, None
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag onto the toolbar")

    def label(self):
        return {"sep": "Separator", "space": "Flexible space"}.get(self.iid) or TB_ITEMS.get(self.iid, self.iid)

    def mousePressEvent(self, e):
        self.press_pos = e.position().toPoint() if e.button() == Qt.MouseButton.LeftButton else None

    def mouseMoveEvent(self, e):
        if (e.buttons() & Qt.MouseButton.LeftButton) and self.press_pos is not None \
                and (e.position().toPoint() - self.press_pos).manhattanLength() >= QApplication.startDragDistance():
            self.press_pos = None
            self.ed.drag_item(self.iid, self)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 16))
        p.drawRoundedRect(QRectF(self.rect()), rr(12), rr(12))
        cx, cy, col = self.W / 2.0, 22.0, QColor(themed("#b5c6d4"))
        if self.iid == "sep":
            p.setPen(QPen(col, 1.5))
            p.drawLine(QPointF(cx, cy - 9), QPointF(cx, cy + 9))
        elif self.iid == "space":
            p.setPen(QPen(col, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx - 12, cy), QPointF(cx + 12, cy))
            for s in (-1, 1):
                p.drawLine(QPointF(cx + 12 * s, cy), QPointF(cx + 7 * s, cy - 4))
                p.drawLine(QPointF(cx + 12 * s, cy), QPointF(cx + 7 * s, cy + 4))
        elif self.iid == "engine":
            pm = engine_icon(self.ed.win.engine).pixmap(20, 20)
            p.drawPixmap(int(cx - 10), int(cy - 10), pm)
        else:
            draw_glyph(p, TB_GLYPHS.get(self.iid, "dots"), QRectF(cx - 10, cy - 10, 20, 20), col, 1.7)
        p.setPen(QColor(themed("#8ea3b4")))
        f = p.font()
        f.setPixelSize(10)
        p.setFont(f)
        txt = QFontMetrics(f).elidedText(self.label(), Qt.TextElideMode.ElideRight, self.W - 8)
        p.drawText(QRectF(0, 38, self.W, 18), Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


class TbPanel(QFrame):
    """The tray under the toolbar while customising: hidden items live here. Drop a toolbar button on it to remove it."""
    def __init__(self, ed):
        super().__init__()
        self.ed, self.tiles, self.cols, self.over = ed, [], 0, False
        self.setObjectName("tbpanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("Customize toolbar")
        title.setObjectName("tbtitle")
        reset = QPushButton("Reset")
        reset.setObjectName("tbreset")
        done = QPushButton("Done")
        done.setObjectName("tbdone")
        for b in (reset, done):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        reset.clicked.connect(lambda: ed.reset())
        done.clicked.connect(lambda: ed.stop())
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(reset)
        head.addWidget(done)
        hint = QLabel("Drag buttons on the toolbar to rearrange them. Drop them here to remove them, "
                      "or drag items from here onto the toolbar to add them.")
        hint.setObjectName("tbhint")
        hint.setWordWrap(True)
        self.grid = QGridLayout()
        self.grid.setSpacing(8)
        lay.addLayout(head)
        lay.addWidget(hint)
        lay.addLayout(self.grid)
        self.hide()

    def refresh(self):
        for t in self.tiles:
            t.hide()
            t.deleteLater()
        self.tiles = [TbTile(self.ed, i) for i in self.ed.hidden_ids() + ["sep", "space"]]
        self.cols = 0
        self.arrange()

    def arrange(self):
        cols = max(1, (self.width() - 32) // (TbTile.W + 8))
        if cols == self.cols:
            return
        self.cols = cols
        while self.grid.count():
            self.grid.takeAt(0)
        for c in range(40):
            self.grid.setColumnStretch(c, 0)
        for n, t in enumerate(self.tiles):
            self.grid.addWidget(t, n // cols, n % cols, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            t.show()
        self.grid.setColumnStretch(cols, 1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.arrange()

    def dragEnterEvent(self, e):
        self.dragMoveEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(TB_MIME):
            e.acceptProposedAction()
            if not self.over:
                self.over = True
                self.update()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.over = False
        self.update()

    def dropEvent(self, e):
        iid = bytes(e.mimeData().data(TB_MIME)).decode("utf-8", "ignore")
        self.over = False
        self.update()
        e.acceptProposedAction()
        self.ed.pending = lambda: self.ed.remove(iid)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.over:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(accent_color(190), 1.4)
            pen.setDashPattern([4.0, 4.0])
            p.setPen(pen)
            p.setBrush(accent_color(24))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), rr(16), rr(16))
            p.end()


class ToolbarEditor(QObject):
    """Owns the toolbar's item order (saved in settings.json) and the drag-and-drop customise mode."""
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.editing = False
        self.pending = None
        self.order = clean_toolbar(win.settings.get("toolbar"))
        self.seq = 1 + max([int(x.split("#")[1]) for x in self.order if "#" in x] or [0])
        self.widgets = {"addr": win.addr, "side": win.btn_side, "back": win.btn_back, "fwd": win.btn_fwd,
                        "reload": win.btn_reload, "engine": win.btn_engine, "speed": win.btn_speed,
                        "vpn": win.btn_vpn, "ext": win.btn_ext, "star": win.btn_star,
                        "scratch": win.scratch_btn, "menu": win.btn_menu}
        self.overlay = TbOverlay(self)
        self.panel = TbPanel(self)
        self._tick = QTimer(self)  # keeps the overlay glued to the layout while buttons move around
        self._tick.setInterval(40)
        self._tick.timeout.connect(self._sync)

    # --- layout ---
    def widget(self, iid):
        w = self.widgets.get(iid)
        if w is None:
            w = TbSeparator() if tb_base(iid) == "sep" else TbSpacer()
            w.setParent(self.win.toolbar)
            self.widgets[iid] = w
        return w

    def apply(self):
        """Put the toolbar widgets in the saved order. Items that are off the toolbar are hidden."""
        win, lay = self.win, self.win.tb_lay
        horiz = bool(getattr(win, "horiz", False))
        for w in self.widgets.values():
            lay.removeWidget(w)
        lay.removeWidget(win.drag_zone)
        lay.removeWidget(win.winctl)
        win.strip_lay.removeWidget(win.winctl)
        for i in reversed(range(lay.count())):  # drop the gaps and stretches added by a previous apply()
            if lay.itemAt(i).spacerItem() is not None:
                lay.takeAt(i)
        mac = UI["mode"] == "mac"
        win.addr.setMaximumWidth(680 if mac else 16777215)  # Safari's address pill is a centred field, not full width
        if mac and not horiz:  # Safari puts the window buttons at the far left
            lay.addWidget(win.winctl)
            lay.addSpacing(12)
        for iid in [i for i in self.widgets if "#" in i and i not in self.order]:
            w = self.widgets.pop(iid)
            w.hide()
            w.deleteLater()
        shown = set()
        caps, run = [], []  # macOS style: the buttons are grouped into glass capsules
        for iid in self.order:
            if (iid == "side" and horiz) or (iid == "scratch" and not horiz):
                continue  # no sidebar button without a sidebar; the scratchpad icon sits in the sidebar otherwise
            w = self.widget(iid)
            base = tb_base(iid)
            if mac and base == "addr":
                caps.append(run)
                run = []
                lay.addStretch(1)
            lay.addWidget(w, TB_STRETCH.get(base, 0))
            if mac:
                if base == "addr":
                    lay.addStretch(1)
                elif base in ("sep", "space"):
                    caps.append(run)
                    run = []
                else:
                    run.append(w)
                    if iid == "side":  # the sidebar button gets a capsule of its own
                        caps.append(run)
                        run = []
                        lay.addSpacing(10)
            w.show()
            if isinstance(w, TbSpacer):
                w.set_editing(self.editing)
            shown.add(iid)
        caps.append(run)
        win.toolbar.caps = [c for c in caps if c] if mac else []
        lay.addWidget(win.drag_zone)
        if horiz:  # horizontal tabs: the window buttons live on the tab row, like Chrome
            if mac:
                win.strip_lay.insertWidget(0, win.winctl)
            else:
                win.strip_lay.addWidget(win.winctl)
        elif not mac:
            lay.addWidget(win.winctl)
        win.winctl.show()
        win.toolbar.update()
        for iid, w in self.widgets.items():
            if iid not in shown and not (iid == "scratch" and not horiz):
                w.hide()
        if self.editing:
            self.panel.refresh()
            self.overlay.update()

    def available(self, iid):
        horiz = bool(getattr(self.win, "horiz", False))
        return not ((iid == "side" and horiz) or (iid == "scratch" and not horiz))

    def hidden_ids(self):
        return [i for i in TB_ITEMS if i not in self.order and self.available(i)]

    def visible_items(self):
        out = []
        for i, iid in enumerate(self.order):
            w = self.widgets.get(iid)
            if w is not None and w.isVisibleTo(self.win.toolbar):
                out.append((i, iid, w))
        return out

    def slot_at(self, x):
        """Where would an item dropped at toolbar x land? -> (index into self.order, x of the drop marker)."""
        vis = self.visible_items()
        if not vis:
            return 0, 6
        k = sum(1 for _i, _id, w in vis if w.geometry().center().x() < x)
        if k < len(vis):
            return vis[k][0], vis[k][2].geometry().left() - 3
        return vis[-1][0] + 1, vis[-1][2].geometry().right() + 4

    # --- changing the order ---
    def set_order(self, order):
        self.order = clean_toolbar(order)
        self.win.settings["toolbar"] = list(self.order)
        jsave("settings.json", self.win.settings)
        self.apply()

    def place(self, iid, idx):
        if not (iid in TB_ITEMS or iid == "addr" or iid in ("sep", "space") or _TB_INST.match(iid)):
            return
        order = list(self.order)
        if iid in order:
            s = order.index(iid)
            order.pop(s)
            if s < idx:
                idx -= 1
        elif iid in ("sep", "space"):  # from the tray: make a fresh copy
            iid = "%s#%d" % (iid, self.seq)
            self.seq += 1
        order.insert(max(0, min(idx, len(order))), iid)
        self.set_order(order)

    def remove(self, iid):
        if iid not in self.order:
            return
        if tb_base(iid) in TB_FIXED:
            self.win.toast("That one has to stay on the toolbar", 2500)
            return
        self.set_order([i for i in self.order if i != iid])

    def reset(self):
        self.set_order(list(TB_DEFAULT))

    # --- customise mode ---
    def drag_item(self, iid, src):
        drag = QDrag(self.overlay)
        mime = QMimeData()
        mime.setData(TB_MIME, iid.encode())
        drag.setMimeData(mime)
        pm = src.grab()
        ghost = QPixmap(pm.size())
        ghost.fill(Qt.GlobalColor.transparent)
        gp = QPainter(ghost)
        gp.setOpacity(0.85)
        gp.drawPixmap(0, 0, pm)
        gp.end()
        drag.setPixmap(ghost)
        drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
        self.overlay.src = iid if iid in self.order else None
        self.overlay.update()
        self.pending = None
        drag.exec(Qt.DropAction.MoveAction)
        self.overlay.src = self.overlay.marker = None
        self.overlay.update()
        fn, self.pending = self.pending, None
        if fn:  # do the change after the drag loop has ended, so widgets it replaces aren't deleted mid-drag
            QTimer.singleShot(0, fn)

    def _sync(self):
        self.overlay.setGeometry(0, 0, max(0, self.win.drag_zone.x()), self.win.toolbar.height())
        self.overlay.raise_()
        self.overlay.update()

    def start(self):
        if self.editing:
            return
        if self.win.isFullScreen() or not self.win.toolbar.isVisible():
            self.win.toast("Leave full screen to customize the toolbar", 3000)
            return
        self.editing = True
        self.win.addr.clearFocus()
        for w in self.widgets.values():
            if isinstance(w, TbSpacer):
                w.set_editing(True)
        self.panel.refresh()
        self.panel.show()
        self.overlay.show()
        self._sync()
        self._tick.start()

    def stop(self):
        if not self.editing:
            return
        self.editing = False
        self._tick.stop()
        self.overlay.hide()
        self.panel.hide()
        for w in self.widgets.values():
            if isinstance(w, TbSpacer):
                w.set_editing(False)


def default_winbtns():
    return "mac" if sys.platform == "darwin" else "windows"


def effective_winbtns(st):
    """The macOS and Windows interface styles bring their own window buttons; Default uses the saved choice."""
    if UI["mode"] in ("mac", "windows"):
        return UI["mode"]
    return st.get("winbtns", default_winbtns())


class DragFilter(QObject):
    """The window has no title bar, so pressing on a bare patch of chrome (not on a button or field) drags it.
    Double-clicking the same spot maximises / restores, like a real title bar."""
    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick) \
                and ev.button() == Qt.MouseButton.LeftButton and obj.childAt(ev.position().toPoint()) is None:
            if t == QEvent.Type.MouseButtonPress:
                h = self.win.windowHandle()
                if h and not self.win.isFullScreen():
                    h.startSystemMove()
            else:
                self.win.toggle_maximize()
            return True
        return False


class EdgeGrip(QWidget):
    """Thin invisible strip along a window edge or corner; dragging it resizes the frameless window."""
    def __init__(self, parent, edges, cursor):
        super().__init__(parent)
        self.edges = edges
        self.setCursor(cursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            h = self.window().windowHandle()
            if h:
                h.startSystemResize(self.edges)


class WinButton(FadeButton):
    """One of the minimise / zoom / close buttons. Paints itself in either the Windows or the macOS look."""
    MAC = {"close": (255, 95, 87), "min": (254, 188, 46), "zoom": (40, 200, 64)}
    RIPPLE = False

    def __init__(self, role, ctl):
        super().__init__(12)
        self.role, self.ctl = role, ctl
        self.setObjectName("winbtn")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # never steal focus from the page or address bar
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.ctl.mode == "mac":
            self._paint_mac(p)
        else:
            self._paint_win(p)
        p.end()

    def _paint_win(self, p):
        r = QRectF(self.rect())
        c, h, down = r.center(), self._h, self.isDown()
        p.setPen(Qt.PenStyle.NoPen)
        if self.role == "close":
            if h > 0.01 or down:
                p.setBrush(QColor(196, 43, 28) if down else QColor(232, 17, 35, int(255 * h)))
                p.drawRoundedRect(r, rr(12), rr(12))
        elif h > 0.01 or down:
            p.setBrush(QColor(255, 255, 255, 40 if down else int(24 * h)))
            p.drawRoundedRect(r, rr(12), rr(12))
        col = _mix(QColor(themed("#b5c6d4")), QColor("#ffffff"), h)
        p.setPen(QPen(col, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        x, y, s = c.x(), c.y(), 5.0
        if self.role == "min":
            p.drawLine(QPointF(x - s, y), QPointF(x + s, y))
        elif self.role == "close":
            k = s * 0.95
            p.drawLine(QPointF(x - k, y - k), QPointF(x + k, y + k))
            p.drawLine(QPointF(x - k, y + k), QPointF(x + k, y - k))
        elif self.ctl.full or self.ctl.maxed:  # restore: two overlapping squares
            p.drawRoundedRect(QRectF(x - s, y - s + 2.4, 2 * s - 2.4, 2 * s - 2.4), 1.5, 1.5)
            p.drawPolyline(QPolygonF([QPointF(x - s + 2.4, y - s + 2.4), QPointF(x - s + 2.4, y - s),
                                      QPointF(x + s, y - s), QPointF(x + s, y + s - 2.4),
                                      QPointF(x + s - 2.4, y + s - 2.4)]))
        else:  # maximise: one square
            p.drawRoundedRect(QRectF(x - s, y - s, 2 * s, 2 * s), 1.8, 1.8)

    def _paint_mac(self, p):
        c = QRectF(self.rect()).center()
        x, y, d = c.x(), c.y(), 13.0
        lit = self.window().isActiveWindow() or self.ctl.underMouse()
        base = QColor(*self.MAC[self.role]) if lit else QColor(96, 104, 112)
        if self.isDown():
            base = base.darker(135)
        p.setPen(QPen(QColor(0, 0, 0, 55), 0.8))
        p.setBrush(base)
        p.drawEllipse(QRectF(x - d / 2, y - d / 2, d, d))
        if not self.ctl.underMouse():
            return
        ink = QColor(0, 0, 0, 150)
        p.setPen(QPen(ink, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.role == "close":
            p.drawLine(QPointF(x - 2.6, y - 2.6), QPointF(x + 2.6, y + 2.6))
            p.drawLine(QPointF(x - 2.6, y + 2.6), QPointF(x + 2.6, y - 2.6))
        elif self.role == "min":
            p.drawLine(QPointF(x - 3.0, y), QPointF(x + 3.0, y))
        else:  # green: two triangles, pointing out to enter full screen and in to leave it
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            P = QPointF
            if self.ctl.full:
                p.drawPolygon(QPolygonF([P(x + 0.6, y - 0.6), P(x - 2.8, y - 0.6), P(x + 0.6, y - 4.0)]))
                p.drawPolygon(QPolygonF([P(x - 0.6, y + 0.6), P(x + 2.8, y + 0.6), P(x - 0.6, y + 4.0)]))
            else:
                p.drawPolygon(QPolygonF([P(x + 3.4, y - 3.4), P(x - 0.6, y - 3.4), P(x + 3.4, y + 0.6)]))
                p.drawPolygon(QPolygonF([P(x - 3.4, y + 3.4), P(x + 0.6, y + 3.4), P(x - 3.4, y - 0.6)]))


class WindowControls(QWidget):
    """Minimise / zoom / close, in the Windows (right-aligned caption buttons) or macOS (traffic lights) style."""
    def __init__(self, win, mode):
        super().__init__()
        self.win, self.mode = win, "windows"
        self.full = self.maxed = False
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.b_min, self.b_zoom, self.b_close = (WinButton(r, self) for r in ("min", "zoom", "close"))
        self.b_min.clicked.connect(lambda: win.showMinimized())
        self.b_zoom.clicked.connect(lambda: win.toggle_fullscreen() if self.mode == "mac" else win.toggle_maximize())
        self.b_close.clicked.connect(lambda: win.close())
        self.set_mode(mode)

    def buttons(self):
        return (self.b_min, self.b_zoom, self.b_close)

    def set_mode(self, mode):
        self.mode = "mac" if mode == "mac" else "windows"
        mac = self.mode == "mac"
        for b in self.buttons():
            self.lay.removeWidget(b)
        if mac and UI["mode"] == "mac":
            order = (self.b_close, self.b_min, self.b_zoom)  # on the left, like Safari
        elif mac:
            order = (self.b_zoom, self.b_min, self.b_close)
        else:
            order = (self.b_min, self.b_zoom, self.b_close)
        for b in order:
            self.lay.addWidget(b)
            b.setFixedSize(20 if mac else ToolIcon.SIZE, ToolIcon.SIZE)
        self.lay.setSpacing(0 if mac else 2)
        self.sync()

    def sync(self):
        self.full, self.maxed = self.win.isFullScreen(), self.win.isMaximized()
        self.b_min.setToolTip("Minimise")
        self.b_close.setToolTip("Close")
        if self.mode == "mac":
            self.b_zoom.setToolTip("Exit full screen  (F11)" if self.full else "Enter full screen  (F11)")
        else:
            self.b_zoom.setToolTip("Restore" if (self.full or self.maxed) else "Maximise")
        for b in self.buttons():
            b.update()

    def enterEvent(self, e):  # macOS: the glyphs appear on all three lights while the pointer is over the group
        super().enterEvent(e)
        self.sync()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self.sync()


class ScratchButton(DropTarget, FadeButton):
    """The Scratchpad's icon: sits above the media player (or in the toolbar when tabs are on top).
    Click = slide the drawer open/shut; it also accepts drops and shows how many things are kept."""
    def __init__(self):
        super().__init__(12)
        self.setObjectName("scratchbtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Scratchpad  (Ctrl+Shift+S)")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(36)
        self.setAcceptDrops(True)
        self.count, self.icon_only, self.on, self.drop_on, self.horiz = 0, False, False, False, False
        self._bump = 0.0
        self._bump_anim = QVariantAnimation(self)
        self._bump_anim.setDuration(650)
        self._bump_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._bump_anim.setStartValue(1.0)
        self._bump_anim.setEndValue(0.0)
        self._bump_anim.valueChanged.connect(self._set_bump)

    def _set_bump(self, v):
        self._bump = float(v)
        self.update()

    def bump(self):
        self._bump_anim.stop()
        self._bump_anim.start()

    def set_count(self, n):
        self.count = n
        self.update()

    def set_on(self, on):
        self.on = on
        self.update()

    def set_mode(self, compact, horiz):
        self.icon_only = compact or horiz
        self.horiz = horiz
        if horiz:
            self.setFixedSize(ToolIcon.SIZE, ToolIcon.SIZE)
        else:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.setFixedHeight(36)
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        lit = self.on or self.drop_on
        if lit:
            p.setPen(QPen(accent_color(200), 1.3, Qt.PenStyle.DashLine) if self.drop_on else Qt.PenStyle.NoPen)
            p.setBrush(accent_color(30))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), rr(12), rr(12))
        h = self._h
        col = accent_color() if lit else QColor(int(181 + 74 * h), int(198 + 57 * h), int(212 + 43 * h))
        gs = ToolIcon.GLYPH if self.horiz else 20
        ic = QRectF(0, 0, gs, gs)
        if self.icon_only:
            ic.moveCenter(r.center())
        else:
            ic.moveTo(11, (r.height() - 20) / 2)
        s = 1.0 + 0.28 * self._bump
        p.save()
        p.translate(ic.center())
        p.scale(s, s)
        p.translate(-ic.center())
        draw_glyph(p, "scratch", ic, col, 1.7)
        p.restore()
        if self.icon_only:
            if self.count:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(accent_color())
                p.drawEllipse(QPointF(ic.right() + 1, ic.top() + 1), 3.5, 3.5)
        else:
            f = QFont(self.font())
            f.setPixelSize(12)
            f.setWeight(QFont.Weight.Medium)
            p.setFont(f)
            badge_w = 0
            if self.count:
                label = str(self.count) if self.count < 100 else "99+"
                badge_w = max(20, QFontMetrics(f).horizontalAdvance(label) + 12)
                bx = r.width() - 10 - badge_w
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(accent_color(46))
                p.drawRoundedRect(QRectF(bx, (r.height() - 19) / 2, badge_w, 19), rr(9.5), rr(9.5))
                p.setPen(accent_color())
                p.drawText(QRectF(bx, 0, badge_w, r.height()), Qt.AlignmentFlag.AlignCenter, label)
                badge_w += 16
            p.setPen(col)
            tw = max(10, int(r.width() - 42 - badge_w))
            p.drawText(QRectF(42, 0, tw, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       QFontMetrics(f).elidedText("Scratchpad", Qt.TextElideMode.ElideRight, tw))
        p.end()


class ScratchEmpty(QWidget):
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        cy = r.center().y()
        draw_glyph(p, "scratch", QRectF(r.center().x() - 26, cy - 78, 52, 52), QColor(255, 255, 255, 50), 1.1)
        f = QFont(self.font())
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor("#b5c6d4"))
        p.drawText(QRectF(0, cy - 14, r.width(), 22), Qt.AlignmentFlag.AlignCenter, "Nothing here yet")
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor("#7d93a5"))
        opt = QTextOption(Qt.AlignmentFlag.AlignHCenter)
        opt.setWrapMode(QTextOption.WrapMode.WordWrap)
        p.drawText(QRectF(28, cy + 14, r.width() - 56, 70),
                   "Drop images, files, text or links onto the sidebar to keep a copy here.", opt)
        p.end()


class DropScroll(DropTarget, QScrollArea):
    pass


class ScratchCard(QFrame):
    """One kept item: thumbnail / icon, a title and detail line, and copy / save / remove buttons.
    Click = open (links, images) or copy (text, files); drag it out to hand the item to another app."""
    def __init__(self, owner, it):
        super().__init__()
        self.o, self.it = owner, it
        self._press = None
        self.setObjectName("scard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        k = it["kind"]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 6, 8)
        lay.setSpacing(10)
        pm = owner.thumb(it) if k == "image" else None
        if pm is not None:
            lead = QLabel()
            lead.setFixedSize(44, 44)
            lead.setPixmap(pm)
        else:
            lead = GlyphTile(k, 44)
        title, sub = self.describe()
        tw = QWidget()
        tl = QVBoxLayout(tw)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        t, s = ElidedLabel(title), ElidedLabel(sub)
        t.setObjectName("mediatitle")
        s.setObjectName("mediasub")
        tl.addStretch(1)
        tl.addWidget(t)
        tl.addWidget(s)
        tl.addStretch(1)
        lay.addWidget(lead)
        lay.addWidget(tw, 1)
        for kind, tip, fn in (("copy", "Copy", owner.copy), ("save", "Save a copy…", owner.save),
                              ("trash", "Remove from Scratchpad", owner.remove)):
            b = GlyphButton(kind, tip, warn=kind == "trash")
            b.clicked.connect(lambda _=False, f=fn, i=it: f(i))
            lay.addWidget(b)
        if k == "text":
            self.setToolTip(it["text"][:500] + ("…" if len(it["text"]) > 500 else "") + "\n\nClick to copy")
        elif k == "link":
            self.setToolTip(it["text"] + "\n\nClick to open in a new tab")
        elif k == "image":
            self.setToolTip(title + "\n\nClick to open in a new tab")
        else:
            self.setToolTip(title + "\n\nClick to copy")

    def describe(self):
        it, k, when = self.it, self.it["kind"], ago(self.it.get("ts", 0))
        if k == "text":
            first = next((ln.strip() for ln in it["text"].splitlines() if ln.strip()), "")
            return first[:120] or "(blank)", "Text · %d chars · %s" % (len(it["text"]), when)
        if k == "link":
            u = it["text"]
            shown = re.sub(r"^https?://(?:www\.)?", "", u).rstrip("/")
            return shown, "Link · %s · %s" % (QUrl(u).host().replace("www.", "") or "web", when)
        name = Path(it["file"]).name
        size = human_size(it.get("size", 0))
        if k == "image":
            path, dim = self.o.store.path(it), ""
            if path:
                sz = QImageReader(str(path)).size()
                if sz.isValid():
                    dim = "%d×%d · " % (sz.width(), sz.height())
            return name, "Image · %s%s · %s" % (dim, size, when)
        return name, "%s · %s · %s" % (Path(name).suffix.lstrip(".").upper()[:5] or "File", size, when)

    def mousePressEvent(self, e):
        self.o.pin()
        self._press = e.position().toPoint() if e.button() == Qt.MouseButton.LeftButton else None
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (self._press is not None and (e.buttons() & Qt.MouseButton.LeftButton)
                and (e.position().toPoint() - self._press).manhattanLength() > QApplication.startDragDistance() * 2):
            self._press = None
            self.o.drag_out(self.it, self)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if (self._press is not None and e.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(e.position().toPoint())):
            self._press = None
            self.o.primary(self.it)
            return
        self._press = None
        super().mouseReleaseEvent(e)


class ScratchDrawer(DropTarget, QFrame):
    """The Scratchpad panel. It slides open from the sidebar's right edge over the page, and accepts drops itself."""
    def __init__(self, browser, parent):
        super().__init__(parent)
        self.b = browser
        self.store = ScratchStore()
        self.setObjectName("scratch")
        self.setAcceptDrops(True)
        self.net = QNetworkAccessManager(self)
        self.thumbs = {}
        self.dirty = True
        self.want = False
        self.drop_on = False
        self.reveal = 0.0
        self.full = SCRATCH_W
        self.auto = False  # opened by a drag (not by the user), so it tucks itself away afterwards

        self.leave_timer = QTimer(self)  # a drag "leaving" a widget is usually just it moving onto the next one
        self.leave_timer.setSingleShot(True)
        self.leave_timer.timeout.connect(self.auto_close)
        QApplication.instance().installEventFilter(self)

        self.body = QWidget(self)
        self.body.setObjectName("sbody")
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("Scratchpad")
        title.setObjectName("scratchtitle")
        self.count_lbl = QLabel()
        self.count_lbl.setObjectName("mediasub")
        self.clear_btn = QToolButton()
        self.clear_btn.setObjectName("scratchclear")
        self.clear_btn.setText("Clear all")
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear_all)
        close = FadeButton(8)
        close.setObjectName("close")
        close.setText("✕")
        close.setToolTip("Close")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(lambda: self.set_open(False))
        head.addWidget(GlyphTile("scratch", 22, tile=False))
        head.addWidget(title)
        head.addWidget(self.count_lbl)
        head.addStretch(1)
        head.addWidget(self.clear_btn)
        head.addWidget(close)
        lay.addLayout(head)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type or paste a note or link…")
        self.input.setAcceptDrops(False)  # so drops on it land in the Scratchpad instead of the text box
        self.input.returnPressed.connect(self.add_note)
        self.input.textEdited.connect(lambda _t: self.pin())
        lay.addWidget(self.input)

        self.scroll = DropScroll()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setAcceptDrops(True)
        self.scroll.viewport().setAcceptDrops(True)
        inner = QWidget()
        self.vl = QVBoxLayout(inner)
        self.vl.setContentsMargins(0, 0, 4, 0)
        self.vl.setSpacing(6)
        self.scroll.setWidget(inner)
        self.empty = ScratchEmpty()
        lay.addWidget(self.scroll, 1)
        lay.addWidget(self.empty, 1)

        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_reveal)
        self._anim.finished.connect(self._anim_done)
        esc = QShortcut(QKeySequence("Esc"), self)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(lambda: self.set_open(False))
        self.update_count()
        self.hide()

    # ----- open / close -----
    def toggle(self):
        self.pin()
        self.set_open(not self.want)

    def set_open(self, on):
        self.want = on
        if on:
            if self.dirty:
                self.refresh()
            self.place()
            self.show()
            self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self.reveal)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.setDuration(280 if on else 200)
        self._anim.start()
        self.b.scratch_btn.set_on(on)

    def _set_reveal(self, v):
        self.reveal = float(v)
        self.place()

    def _anim_done(self):
        if not self.want:
            self.hide()

    def place(self):
        b, root = self.b, self.parentWidget()
        if root is None or getattr(b, "stack", None) is None:
            return
        if b.horiz:
            tl = b.stack.mapTo(root, QPoint(0, 0))
            x, y, h = tl.x(), tl.y(), b.stack.height()
        elif b.side.isVisible():
            r = b.side.geometry()
            x, y, h = r.right() + 10, r.top(), r.height()
        else:
            x, y, h = 8, 8, root.height() - 16
        h = max(0, h)
        self.full = max(220, min(SCRATCH_W, root.width() - x - 12))
        self.setGeometry(x, y, int(self.full * self.reveal), h)  # the panel is revealed left to right...
        self.body.setGeometry(0, 0, self.full, h)               # ...while its contents stay put
        if self.isVisible():
            self.raise_()

    # ----- pop up while dragging -----
    def popup_enabled(self):
        return bool(self.b.settings.get("scratch_popup", True))

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t not in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.DragLeave, QEvent.Type.Drop):
            return False
        if not isinstance(obj, QWidget) or obj.window() is not self.b.window():
            return False
        if t in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if self.popup_enabled() and self.accepts(ev.mimeData()):
                self.leave_timer.stop()
                if not self.want:
                    self.auto = True
                    self.set_open(True)
        elif t == QEvent.Type.DragLeave:
            if self.auto:
                self.leave_timer.start(400)
        else:  # Drop: leave it open long enough to see the new card land, or tuck away fast if it went elsewhere
            if self.auto:
                self.leave_timer.start(300)
        return False  # never swallow the event; the widgets under the pointer still handle it

    def auto_close(self):
        if self.auto and self.want:
            self.set_open(False)
        self.auto = False

    def pin(self):
        """The person is using the drawer, so stop it from tucking itself away."""
        self.auto = False
        self.leave_timer.stop()

    # ----- drops -----
    def accepts(self, md):
        if md.hasFormat(TAB_MIME) or md.hasFormat(SCRATCH_MIME):
            return False
        return md.hasUrls() or md.hasImage() or md.hasText() or md.hasHtml()

    def drop_hint(self, on):
        for w in (self.b.side, self.b.scratch_btn, self):
            if w.drop_on != on:
                w.drop_on = on
                w.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.drop_on:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(accent_color(190), 1.5, Qt.PenStyle.DashLine))
            p.setBrush(accent_color(16))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2.5, 2.5, -2.5, -2.5), rr(14), rr(14))
            p.end()

    @staticmethod
    def _html_img(markup):
        m = re.search(r"<img\b[^>]*?\bsrc\s*=\s*([\"'])(.*?)\1", markup, re.I | re.S)
        return html.unescape(m.group(2)).strip() if m else None

    def ingest(self, md):
        added, errs = [0], []

        def run(fn, *a):
            try:
                if fn(*a):
                    added[0] += 1
            except ValueError as ex:
                errs.append(str(ex))

        urls = md.urls()
        local = [u.toLocalFile() for u in urls if u.isLocalFile()]
        remote = [u.toString() for u in urls if u.scheme().lower() in ("http", "https")]
        img = md.imageData() if md.hasImage() else None
        if isinstance(img, QPixmap):
            img = img.toImage()
        img_src = self._html_img(md.html()) if md.hasHtml() else None
        if local:
            for f in local[:20]:
                run(self.store.add_file, f)
        elif isinstance(img, QImage) and not img.isNull():
            run(self.store.add_qimage, img)
        elif img_src:
            added[0] += self._grab_image(img_src, errs)
        elif remote and IMG_URL_RE.search(remote[0]):
            added[0] += self._grab_image(remote[0], errs)
        elif remote:
            for u in remote[:20]:
                run(self.store.add_text, u)
        elif md.hasText():
            run(self.store.add_text, md.text())
        elif md.hasHtml():
            run(self.store.add_text, html.unescape(re.sub(r"<[^>]+>", "", md.html())))
        self._finish(added[0], errs)

    def _grab_image(self, src, errs):
        """Returns how many items were added right away (a download adds its item when it finishes)."""
        if src.startswith("data:"):
            try:
                self.store.add_image_bytes(data_url_bytes(src))
                return 1
            except ValueError as ex:
                errs.append(str(ex))
                return 0
        u = QUrl(src)
        cur = self.b.cur()
        if cur is not None:
            u = cur.url().resolved(u)
        url = u.toString()
        if u.scheme().lower() not in ("http", "https"):
            errs.append("That image can't be saved from here")
            return 0
        if self.b.settings.get("vpn") or not safe_art_url(url):
            # Qt's own network stack skips the browser proxy and mustn't touch LAN hosts, so keep just the link
            self.store.add_text(url)
            errs.append("Kept the image's link (it isn't downloaded while the VPN is on)"
                        if self.b.settings.get("vpn") else "Kept the image's link")
            return 1
        req = QNetworkRequest(u)
        req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        rep = self.net.get(req)
        rep.downloadProgress.connect(lambda got, _t, r=rep: r.abort() if got > SCRATCH_MAX_IMG else None)
        rep.finished.connect(lambda r=rep, s=url: self._fetched(r, s))
        self.b.toast("Fetching image…", 3000)
        return 0

    def _fetched(self, rep, url):
        data = bytes(rep.readAll()) if rep.error() == QNetworkReply.NetworkError.NoError else b""
        rep.deleteLater()
        try:
            self.store.add_image_bytes(data, unquote(Path(urlparse(url).path).name))
            self._finish(1, [])
        except ValueError:
            self.store.add_text(url)
            self._finish(1, ["Couldn't download that image, kept its link instead"])

    def _finish(self, n, errs):
        if n:
            self.changed()
            self.b.scratch_btn.bump()
            msg = "Added to Scratchpad" if n == 1 else "Added %d items to Scratchpad" % n
            self.b.toast(msg + (" · " + errs[0] if errs else ""), 3200)
            if self.auto:
                self.leave_timer.start(1600)
        else:
            self.b.toast(errs[0] if errs else "Nothing to add", 4000)

    def add_note(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self._finish(1 if self.store.add_text(text) else 0, [])

    # ----- list -----
    def changed(self):
        self.dirty = True
        self.update_count()
        if self.want:
            self.refresh()

    def update_count(self):
        n = len(self.store.items)
        self.count_lbl.setText("%d item%s" % (n, "" if n == 1 else "s") if n else "")
        self.clear_btn.setVisible(n > 0)
        self.scroll.setVisible(n > 0)
        self.empty.setVisible(n == 0)
        self.b.scratch_btn.set_count(n)

    def refresh(self):
        self.dirty = False
        while self.vl.count():
            li = self.vl.takeAt(0)
            w = li.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        alive = {it["id"] for it in self.store.items}
        for k in [k for k in self.thumbs if k not in alive]:
            del self.thumbs[k]
        for it in self.store.items:
            self.vl.addWidget(ScratchCard(self, it))
        self.vl.addStretch(1)

    def thumb(self, it):
        if it["id"] not in self.thumbs:
            path = self.store.path(it)
            self.thumbs[it["id"]] = thumb_pixmap(path) if path else None
        return self.thumbs[it["id"]]

    # ----- item actions -----
    def primary(self, it):
        k = it["kind"]
        if k == "link":
            self.b.new_tab(it["text"])
        elif k == "image" and self.store.path(it):
            self.b.new_tab(QUrl.fromLocalFile(str(self.store.path(it))))
        else:
            self.copy(it)

    def _mime(self, it, with_marker=False):
        md = QMimeData()
        if with_marker:
            md.setData(SCRATCH_MIME, b"1")
        k, path = it["kind"], self.store.path(it)
        if k in ("text", "link"):
            md.setText(it["text"])
            if k == "link":
                md.setUrls([QUrl(it["text"])])
        elif path is not None:
            md.setUrls([QUrl.fromLocalFile(str(path))])
            if k == "image":
                img = QImage(str(path))
                if not img.isNull():
                    md.setImageData(img)
        return md

    def copy(self, it):
        if it["kind"] in ("image", "file") and self.store.path(it) is None:
            self.b.toast("That file is no longer in the Scratchpad", 3000)
            return
        QApplication.clipboard().setMimeData(self._mime(it))
        self.b.toast("Copied to clipboard", 2200)

    def save(self, it):
        k, path = it["kind"], self.store.path(it)
        if k == "text":
            first = next((ln.strip() for ln in it["text"].splitlines() if ln.strip()), "")
            name = (safe_name(first[:40], "note")) + ".txt"
        elif k == "link":
            name = safe_name(QUrl(it["text"]).host() or "link", "link") + ".url"
        elif path is not None:
            name = path.name
        else:
            self.b.toast("That file is no longer in the Scratchpad", 3000)
            return
        dest, _ = QFileDialog.getSaveFileName(self.b, "Save from Scratchpad", str(Path.home() / "Downloads" / name))
        if not dest:
            return
        try:
            if k == "text":
                Path(dest).write_text(it["text"], encoding="utf-8")
            elif k == "link":
                Path(dest).write_text("[InternetShortcut]\r\nURL=%s\r\n" % it["text"], encoding="utf-8")
            else:
                shutil.copy2(str(path), dest)
            self.b.toast("Saved → " + dest)
        except OSError:
            self.b.toast("Couldn't save the file", 3000)

    def remove(self, it):
        self.store.remove(it["id"])
        self.thumbs.pop(it["id"], None)
        self.changed()

    def clear_all(self):
        n = len(self.store.items)
        if n and QMessageBox.question(self.b, "Clear Scratchpad", "Remove all %d items from the Scratchpad?" % n,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.store.clear()
            self.thumbs.clear()
            self.changed()

    def drag_out(self, it, card):
        d = QDrag(card)
        d.setMimeData(self._mime(it, with_marker=True))
        pm = card.grab()
        if pm.width() > 260:
            pm = pm.scaledToWidth(260, Qt.TransformationMode.SmoothTransformation)
        d.setPixmap(pm)
        d.setHotSpot(QPoint(24, pm.height() // 2))
        d.exec(Qt.DropAction.CopyAction)


# ----- welcome tour -----
# A first-run walkthrough in the spirit of Zen's: a glass card floats over the dimmed browser and takes you through
# importing, accent colour, layout and every headline feature, with each choice applied live behind it.
TOUR_TEXT, TOUR_MUTED = "#eaf3f9", "#8ea3b4"
TOUR_SWATCHES = [("Fjord", None), ("Coral", "#ff7a6b"), ("Amber", "#ffb454"), ("Lemon", "#f4d35e"), ("Lime", "#9be564"),
                 ("Mint", "#4fe3a8"), ("Teal", "#34d1d1"), ("Sky", "#5aa9ff"), ("Indigo", "#7c83ff"),
                 ("Violet", "#b07cff"), ("Orchid", "#e27bf0"), ("Rose", "#ff6fa5")]
TOUR_GLYPHS = {"split", "group", "pin", "clock", "play", "note", "block", "import", "palette", "layout", "check"}


def kb(keys):
    """A shortcut as it should read on this computer (Ctrl+T becomes \u2318T on a Mac)."""
    if sys.platform != "darwin":
        return keys
    return keys.replace("Ctrl+", "\u2318").replace("Shift+", "\u21e7").replace("Alt+", "\u2325")


def key_chips(keys):
    return [kb(keys)] if sys.platform == "darwin" else keys.split("+")


def tour_glyph(p, kind, rect, color, width=1.6):
    """draw_glyph plus a few extra icons the tour needs (same 24x24 grid, same stroke handling)."""
    if kind not in TOUR_GLYPHS:
        draw_glyph(p, kind, rect, color, width)
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.x(), rect.y())
    p.scale(rect.width() / 24.0, rect.height() / 24.0)
    pen = QPen(QColor(color))
    pen.setWidthF(width * 24.0 / max(1.0, rect.width()))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    P = QPointF
    if kind == "split":
        p.drawRoundedRect(QRectF(3.5, 4.5, 17, 15), 3, 3)
        p.drawLine(P(12, 4.5), P(12, 19.5))
    elif kind == "group":
        p.drawRoundedRect(QRectF(3.5, 8, 17, 11.5), 3, 3)
        p.drawLine(P(7, 4.8), P(13, 4.8))
    elif kind == "pin":
        for x in (4.0, 13.5):
            for y in (4.0, 13.5):
                p.drawRoundedRect(QRectF(x, y, 6.5, 6.5), 2, 2)
    elif kind == "clock":
        p.drawEllipse(P(12, 12), 8.5, 8.5)
        p.drawPolyline(QPolygonF([P(12, 7), P(12, 12), P(15.6, 14.2)]))
    elif kind == "play":
        p.drawPolygon(QPolygonF([P(9, 6.5), P(18, 12), P(9, 17.5)]))
    elif kind == "note":
        p.drawRoundedRect(QRectF(4.5, 4.5, 15, 15), 3, 3)
        p.drawLine(P(8, 10), P(16, 10))
        p.drawLine(P(8, 14), P(13, 14))
    elif kind == "block":
        p.drawEllipse(P(12, 12), 8.5, 8.5)
        p.drawLine(P(6, 6), P(18, 18))
    elif kind == "import":
        p.drawLine(P(12, 4), P(12, 14.5))
        p.drawPolyline(QPolygonF([P(8, 10.8), P(12, 14.8), P(16, 10.8)]))
        p.drawPolyline(QPolygonF([P(5, 15), P(5, 19), P(19, 19), P(19, 15)]))
    elif kind == "palette":
        p.drawEllipse(P(12, 12), 8.5, 8.5)
        for x, y in ((8.5, 10), (12, 7.8), (15.5, 10)):
            p.drawEllipse(P(x, y), 0.9, 0.9)
    elif kind == "layout":
        p.drawRoundedRect(QRectF(3.5, 4.5, 17, 15), 3, 3)
        p.drawLine(P(9.5, 4.5), P(9.5, 19.5))
    elif kind == "check":
        p.drawPolyline(QPolygonF([P(5, 12.5), P(10, 17.5), P(19, 7)]))
    p.restore()


class Smooth:
    """Mixin: ease a float attribute towards a target (self.smooth("_h", 1.0)) and repaint on every step."""
    def smooth(self, name, to, ms=200, curve=QEasingCurve.Type.OutCubic):
        anims = self.__dict__.setdefault("_smooth_anims", {})
        old = anims.get(name)
        if old is not None:
            old.stop()
        a = QVariantAnimation(self)
        a.setDuration(max(1, ms))
        a.setStartValue(float(getattr(self, name)))
        a.setEndValue(float(to))
        a.setEasingCurve(curve)
        a.valueChanged.connect(lambda v, n=name: (setattr(self, n, float(v)), self.update()))
        anims[name] = a
        a.start()


class RiseEffect(QGraphicsEffect):
    """Fades a widget in while it drifts up into place (sign=1), or out while it drifts away (sign=-1)."""
    def __init__(self, parent, sign=1):
        super().__init__(parent)
        self._sign = sign
        self._o, self._dy = 0.0, 0.0
        self.set_progress(0.0)

    def set_progress(self, v):
        v = max(0.0, min(1.0, float(v)))
        self._o = v
        self._dy = self._sign * 14.0 * (1.0 - v)
        self.update()

    def boundingRectFor(self, rect):
        return rect.adjusted(0, -18, 0, 18)

    def draw(self, painter):
        try:
            pm, off = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates)
        except Exception:
            self.drawSource(painter)
            return
        if pm.isNull():
            return
        painter.setOpacity(painter.opacity() * self._o)
        painter.drawPixmap(off + QPoint(0, int(round(self._dy))), pm)


class TourCard(QWidget):
    """The floating glass card (with a soft shadow in its margin)."""
    M = 18

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(self.M, self.M, -self.M, -self.M)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(16):
            p.setBrush(QColor(0, 0, 0, int(9 * (1 - i / 16.0))))
            p.drawRoundedRect(r.adjusted(-i, -i + 9, i, i + 9), 20 + i, 20 + i)
        body = QColor(themed("#0d1620"))
        body.setAlpha(252)
        p.setBrush(body)
        p.drawRoundedRect(r, 20, 20)
        clip = QPainterPath()
        clip.addRoundedRect(r, 20, 20)
        g = QLinearGradient(r.topLeft(), QPointF(r.left(), r.top() + r.height() * 0.4))
        g.setColorAt(0.0, accent_color(14))
        g.setColorAt(1.0, accent_color(0))
        p.fillPath(clip, QBrush(g))
        p.setBrush(Qt.BrushStyle.NoBrush)  # one hairline instead of the heavy glass rim
        p.setPen(QPen(QColor(255, 255, 255, 22), 1.0))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 20, 20)
        p.end()


_TOUR_LOGO = {}


def tour_logo():
    """The Fjord logo from fjord.ico, at its largest stored size (cached; null pixmap if the file is missing)."""
    if "pm" not in _TOUR_LOGO:
        pm = QPixmap()
        try:
            ic = QIcon(resource_path("fjord.ico"))
            sizes = ic.availableSizes()
            big = max(sizes, key=lambda s: s.width()) if sizes else QSize(256, 256)
            pm = ic.pixmap(big)
        except Exception:
            pass
        _TOUR_LOGO["pm"] = pm
    return _TOUR_LOGO["pm"]


class TourHero(Smooth, QWidget):
    """The top of the card: the Fjord logo on a soft glow (welcome page), or a slim glyph ring on the other pages."""
    def __init__(self):
        super().__init__()
        self.kind, self.t, self.t0, self._in = "wordmark", 0.0, 0.0, 0.0
        self.col = QColor(ACCENT["main"])
        self._cfrom = self._cto = QColor(self.col)
        self._canim = QVariantAnimation(self)
        self._canim.setDuration(480)
        self._canim.setStartValue(0.0)
        self._canim.setEndValue(1.0)
        self._canim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._canim.valueChanged.connect(self._mixcol)

    def _mixcol(self, v):
        self.col = _mix(self._cfrom, self._cto, float(v))
        self.update()

    def set_color(self, c):
        """Glide to a new accent colour instead of jumping."""
        self._cfrom, self._cto = QColor(self.col), QColor(c)
        self._canim.stop()
        self._canim.start()

    def set_kind(self, kind):
        self.kind = kind
        self.t0 = self.t

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        cx, cy = w / 2.0, h / 2.0 + 6
        col, t, k = QColor(self.col), self.t, max(0.0, min(1.0, self._in))
        ease = k * k * (3 - 2 * k)
        bt = max(0.0, t - self.t0)
        glow = QRadialGradient(QPointF(cx, cy), 120)
        g0, g1 = QColor(col), QColor(col)
        g0.setAlpha(int(46 * k))
        g1.setAlpha(0)
        glow.setColorAt(0.0, g0)
        glow.setColorAt(1.0, g1)
        p.fillRect(self.rect(), QBrush(glow))
        ph = (t / 3.4) % 1.0  # a single slow pulse
        c = QColor(col)
        c.setAlpha(int(60 * (1 - ph) * k))
        p.setPen(QPen(c, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        rad = 34 + ph * 52
        p.drawEllipse(QPointF(cx, cy), rad, rad)
        logo = tour_logo()
        if self.kind == "wordmark" and not logo.isNull():
            S = 68.0 * (0.9 + 0.1 * ease)
            box = QRectF(cx - S / 2, cy - S / 2, S, S)
            p.setOpacity(k)
            p.setPen(Qt.PenStyle.NoPen)
            sh = QColor(col)
            sh.setAlpha(int(40 * k))
            p.setBrush(sh)
            p.drawRoundedRect(box.adjusted(-5, -5, 5, 5), S * 0.26, S * 0.26)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            clip = QPainterPath()
            clip.addRoundedRect(box, S * 0.22, S * 0.22)
            p.save()
            p.setClipPath(clip)
            p.drawPixmap(box, logo, QRectF(logo.rect()))
            p.restore()
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 36), 1.0))
            p.drawRoundedRect(box, S * 0.22, S * 0.22)
            p.setOpacity(1.0)
        elif self.kind == "wordmark":  # logo file missing: fall back to the lettering
            f = QFont(self.font())
            f.setPixelSize(40)
            f.setWeight(QFont.Weight.Light)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0 + 10.0 * (1 - ease))
            p.setFont(f)
            tc = QColor(TOUR_TEXT)
            tc.setAlpha(int(255 * k))
            p.setPen(tc)
            p.drawText(QRectF(0, cy - 32 + 8 * (1 - ease), w, 64), Qt.AlignmentFlag.AlignCenter, "fjord")
        else:
            R = 32.0 * (0.8 + 0.2 * ease)
            edge, fill = QColor(col), QColor(col)
            edge.setAlpha(int(110 * k))
            fill.setAlpha(int(24 * k))
            p.setPen(QPen(edge, 1.0))
            p.setBrush(fill)
            p.drawEllipse(QPointF(cx, cy), R, R)
            ink = _mix(col, QColor("#ffffff"), 0.35)
            if self.kind == "check":
                prog = max(0.0, min(1.0, (bt - 0.15) / 0.55))
                a, b, c3 = QPointF(cx - 11, cy + 1), QPointF(cx - 3, cy + 9), QPointF(cx + 12, cy - 8)
                l1, l2 = math.hypot(b.x() - a.x(), b.y() - a.y()), math.hypot(c3.x() - b.x(), c3.y() - b.y())
                run = prog * (l1 + l2)
                pts = [a]
                if run <= l1:
                    f1 = run / l1
                    pts.append(QPointF(a.x() + (b.x() - a.x()) * f1, a.y() + (b.y() - a.y()) * f1))
                else:
                    f2 = (run - l1) / l2
                    pts += [b, QPointF(b.x() + (c3.x() - b.x()) * f2, b.y() + (c3.y() - b.y()) * f2)]
                pen = QPen(ink, 2.6)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                if prog > 0:
                    p.drawPolyline(QPolygonF(pts))
                if bt < 1.4:  # one burst of sparks when the tour completes
                    q = min(1.0, bt / 1.2)
                    out = 1 - (1 - q) ** 3
                    for i in range(16):
                        ang = i * math.tau / 16 + 0.2
                        d = 36 + 50 * out * (0.7 + 0.3 * ((i * 7) % 5) / 4.0)
                        sc = QColor(col)
                        sc.setAlpha(int(230 * (1 - q)))
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(sc)
                        rr_ = 2.2 * (1 - q) + 0.5
                        p.drawEllipse(QPointF(cx + math.cos(ang) * d, cy + math.sin(ang) * d * 0.8), rr_, rr_)
            else:
                p.setOpacity(k)
                tour_glyph(p, self.kind, QRectF(cx - 15, cy - 15, 30, 30), ink, 1.6)
                p.setOpacity(1.0)
        p.end()


class TourGlyph(QWidget):
    """An icon on a soft accent tile, the lead of each feature row."""
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.setFixedSize(42, 42)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent_color(34))
        p.drawRoundedRect(QRectF(self.rect()), 13, 13)
        tour_glyph(p, self.kind, QRectF(10, 10, 22, 22), accent_color(), 1.7)
        p.end()


class TourRowCard(QWidget):
    """A softly rounded strip that holds one line of controls."""
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 14))
        p.drawRoundedRect(QRectF(self.rect()), 14, 14)
        p.end()


class TourSwatch(Smooth, QWidget):
    """A round colour swatch; the ring around the chosen one springs open."""
    picked = pyqtSignal()

    def __init__(self, name, hexcol, kind="color"):
        super().__init__()
        self.name, self.hexcol, self.kind = name, hexcol, kind
        self._h = self._sel = 0.0
        self.setFixedSize(42, 42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(name)

    def set_color(self, hexcol):
        self.hexcol = hexcol
        self.update()

    def enterEvent(self, e):
        self.smooth("_h", 1.0, 160)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.smooth("_h", 0.0, 220)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.picked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(21, 21)
        sel = max(0.0, min(1.0, self._sel))
        rainbow = self.kind == "custom" and not self.hexcol
        base = QColor(DEFAULT_ACCENT["main"] if self.kind == "default" else (self.hexcol or "#888888"))
        if sel > 0.01:
            ring = QColor(base)
            ring.setAlpha(int(255 * sel))
            R = 19.2 * (0.8 + 0.2 * self._sel)
            p.setPen(QPen(ring, 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, R, R)
        r = 15.0 + 1.6 * self._h - 2.2 * sel
        p.setPen(Qt.PenStyle.NoPen)
        if rainbow:
            cg = QConicalGradient(c, 90)
            for i, hx in enumerate(("#ff6b6b", "#ffd166", "#8ee59a", "#5aa9ff", "#b07cff", "#ff6b6b")):
                cg.setColorAt(i / 5.0, QColor(hx))
            p.setBrush(QBrush(cg))
        else:
            p.setBrush(base.lighter(100 + int(10 * self._h)))
        p.drawEllipse(c, r, r)
        if rainbow:
            pen = QPen(QColor(255, 255, 255, 235), 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(21, 15), QPointF(21, 27))
            p.drawLine(QPointF(15, 21), QPointF(27, 21))
        if sel > 0.25:
            p.setOpacity(min(1.0, (sel - 0.25) / 0.5))
            tour_glyph(p, "check", QRectF(13, 13, 16, 16), QColor(8, 18, 26), 2.4)
        p.end()


class TourChoice(Smooth, QWidget):
    """A selectable card: a little picture on top, a title and a one-line caption underneath."""
    picked = pyqtSignal()

    def __init__(self, title, sub, preview, size=(180, 100), tint=None):
        super().__init__()
        self.title, self.sub, self.preview, self.tint = title, sub, preview, tint
        self._h = self._sel = 0.0
        self.setFixedSize(*size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_selected(self, on, animate=True):
        if animate:
            self.smooth("_sel", 1.0 if on else 0.0, 280, QEasingCurve.Type.OutCubic)
        else:
            self._sel = 1.0 if on else 0.0
            self.update()

    def enterEvent(self, e):
        self.smooth("_h", 1.0, 160)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.smooth("_h", 0.0, 220)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.picked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        accent = QColor(self.tint or ACCENT["main"])
        sel = max(0.0, min(1.0, self._sel))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, int(13 + 15 * self._h)))
        p.drawRoundedRect(r, 16, 16)
        if sel > 0.01:
            tint = QColor(accent)
            tint.setAlpha(int(36 * sel))
            p.setBrush(tint)
            p.drawRoundedRect(r, 16, 16)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        p.drawRoundedRect(r, 16, 16)
        if sel > 0.01:
            edge = QColor(accent)
            edge.setAlpha(int(235 * sel))
            p.setPen(QPen(edge, 1.6))
            p.drawRoundedRect(r, 16, 16)
        pr = QRectF(r.x() + 14, r.y() + 12, r.width() - 28, r.height() - 60)
        self.preview(p, pr, accent, sel)
        f = QFont(self.font())
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(TOUR_TEXT))
        p.drawText(QRectF(r.x() + 14, pr.bottom() + 8, r.width() - 28, 18), Qt.AlignmentFlag.AlignVCenter, self.title)
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor(TOUR_MUTED))
        sub = QFontMetrics(f).elidedText(self.sub, Qt.TextElideMode.ElideRight, int(r.width() - 28))
        p.drawText(QRectF(r.x() + 14, pr.bottom() + 26, r.width() - 28, 16), Qt.AlignmentFlag.AlignVCenter, sub)
        p.end()


def _mock_window(p, r, accent, tabs, style="default"):
    """A tiny sketch of the browser window, for the layout and style cards."""
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    rad = {"default": 7.0, "mac": 11.0, "windows": 3.0}[style]
    p.setBrush(QColor(255, 255, 255, 20))
    p.drawRoundedRect(r, rad, rad)
    inner = r.adjusted(4, 4, -4, -4)
    soft, acc = QColor(255, 255, 255, 64), QColor(accent)
    acc.setAlpha(200)
    sub = max(2.0, rad * 0.55)
    if tabs == "vertical":
        side = QRectF(inner.x(), inner.y(), inner.width() * 0.3, inner.height())
        p.setBrush(QColor(255, 255, 255, 26))
        p.drawRoundedRect(side, sub, sub)
        top = side.y() + 5 + (7 if style == "mac" else 0)
        for i in range(3):
            p.setBrush(acc if i == 0 else soft)
            p.drawRoundedRect(QRectF(side.x() + 4, top + i * 8, side.width() - 8, 5), 2.5, 2.5)
        page = QRectF(side.right() + 4, inner.y(), inner.right() - side.right() - 4, inner.height())
    else:
        strip = QRectF(inner.x(), inner.y(), inner.width(), 10)
        w = (strip.width() - 8) / 3.0
        for i in range(3):
            p.setBrush(acc if i == 0 else soft)
            p.drawRoundedRect(QRectF(strip.x() + 2 + i * w, strip.y() + 1.5, w - 4, 7), 3, 3)
        page = QRectF(inner.x(), strip.bottom() + 3, inner.width(), inner.bottom() - strip.bottom() - 3)
    p.setBrush(QColor(255, 255, 255, 13))
    p.drawRoundedRect(page, sub, sub)
    if tabs == "vertical":  # a toolbar line on the page, shaped like the style's controls
        bar = QRectF(page.x() + 4, page.y() + 4, page.width() - 8, 6)
        p.setBrush(QColor(255, 255, 255, 38))
        p.drawRoundedRect(bar, 3 if style != "windows" else 1, 3 if style != "windows" else 1)
    if style == "mac":
        for i, hx in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            p.setBrush(QColor(hx))
            p.drawEllipse(QPointF(inner.x() + 5 + i * 5.5, inner.y() + 4), 1.7, 1.7)
    elif style == "windows":
        pen = QPen(QColor(255, 255, 255, 120), 1.0)
        p.setPen(pen)
        x0, y0 = inner.right() - 22, inner.y() + 3
        p.drawLine(QPointF(x0, y0 + 2), QPointF(x0 + 4, y0 + 2))
        p.drawRect(QRectF(x0 + 8, y0, 4, 4))
        p.drawLine(QPointF(x0 + 16, y0), QPointF(x0 + 20, y0 + 4))
        p.drawLine(QPointF(x0 + 16, y0 + 4), QPointF(x0 + 20, y0))
    p.restore()


def _preview_layout(tabs):
    return lambda p, r, accent, sel: _mock_window(p, QRectF(r.center().x() - 38, r.y(), 76, r.height()), accent, tabs)


def _preview_style(style):
    return lambda p, r, accent, sel: _mock_window(p, QRectF(r.center().x() - 38, r.y(), 76, r.height()), accent, "vertical", style)


def _preview_glyph(kind, color=None):
    def draw(p, r, accent, sel):
        s = min(r.width(), r.height(), 46.0)
        tour_glyph(p, kind, QRectF(r.center().x() - s / 2, r.center().y() - s / 2, s, s), QColor(color) if color else accent, 1.7)
    return draw


class TourSegment(Smooth, QWidget):
    """A pill-shaped selector whose highlight slides between options."""
    changed = pyqtSignal(int)

    def __init__(self, labels, index=0):
        super().__init__()
        self.labels, self.index, self._pos = list(labels), index, float(index)
        f = self._font()
        fm = QFontMetrics(f)
        self.widths = [fm.horizontalAdvance(t) + 28 for t in self.labels]
        self.setFixedSize(sum(self.widths) + 6, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _font(self):
        f = QFont(self.font())
        f.setPixelSize(12)
        return f

    def set_index(self, i, animate=True):
        if i == self.index:
            return
        self.index = i
        self.smooth("_pos", float(i), 320 if animate else 1, QEasingCurve.Type.InOutCubic)
        self.changed.emit(i)

    def mouseReleaseEvent(self, e):
        if not self.isEnabled() or e.button() != Qt.MouseButton.LeftButton:
            return
        x, acc = e.position().x() - 3, 0.0
        for i, w in enumerate(self.widths):
            acc += w
            if x < acc:
                self.set_index(i)
                return
        self.set_index(len(self.widths) - 1)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 16))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        cum = [3.0]
        for w in self.widths:
            cum.append(cum[-1] + w)
        n = len(self.widths)
        i = max(0, min(n - 1, int(math.floor(self._pos))))
        tt = max(0.0, min(1.0, self._pos - i)) if i < n - 1 else 0.0
        x = cum[i] + ((cum[i + 1] - cum[i]) * tt if i < n - 1 else 0.0)
        wd = self.widths[i] + ((self.widths[i + 1] - self.widths[i]) * tt if i < n - 1 else 0.0)
        hl = QRectF(x, 3, wd, r.height() - 6)
        fill, edge = accent_color(70), accent_color(210)
        p.setBrush(fill)
        p.setPen(QPen(edge, 1.0))
        p.drawRoundedRect(hl, hl.height() / 2, hl.height() / 2)
        p.setFont(self._font())
        for j, text in enumerate(self.labels):
            near = max(0.0, 1.0 - abs(self._pos - j))
            p.setPen(_mix(QColor(TOUR_MUTED), QColor("#ffffff"), near) if self.isEnabled() else QColor(90, 105, 118))
            p.drawText(QRectF(cum[j], 0, self.widths[j], r.height()), Qt.AlignmentFlag.AlignCenter, text)
        p.end()


class TourToggle(Smooth, QWidget):
    """An on/off switch with a springy knob."""
    toggled = pyqtSignal(bool)

    def __init__(self, on=False):
        super().__init__()
        self.on, self._v = bool(on), 1.0 if on else 0.0
        self.setFixedSize(48, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.on = not self.on
            self.smooth("_v", 1.0 if self.on else 0.0, 300, QEasingCurve.Type.OutBack)
            self.toggled.emit(self.on)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        v = max(0.0, min(1.0, self._v))
        r = QRectF(self.rect()).adjusted(1, 3, -1, -3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_mix(QColor(75, 92, 108), QColor(ACCENT["main"]), v))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        x = r.x() + 3 + (r.width() - r.height()) * self._v
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(x + (r.height() - 6) / 2.0 + 0.0, r.center().y()), (r.height() - 6) / 2.0, (r.height() - 6) / 2.0)
        p.end()


class TourDots(Smooth, QWidget):
    """Progress dots; the current one stretches into a pill and glides along."""
    def __init__(self, n):
        super().__init__()
        self.n, self._pos = n, 0.0
        self.setFixedSize(n * 5 + 12 + (n - 1) * 5 + 2, 12)

    def set_index(self, i):
        self.smooth("_pos", float(i), 420, QEasingCurve.Type.InOutCubic)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        x = 1.0
        for j in range(self.n):
            near = max(0.0, 1.0 - abs(self._pos - j))
            w = 5.0 + 12.0 * near
            c = QColor(ACCENT["main"])
            c.setAlpha(int(55 + 200 * near))
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, 4, w, 4), 2, 2)
            x += w + 5
        p.end()


class TourButton(Smooth, QAbstractButton):
    """The tour's own buttons: a filled accent pill (primary) or quiet text."""
    def __init__(self, text, primary=False):
        super().__init__()
        self.primary, self._h = primary, 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self.set_label(text)

    def _font(self):
        f = QFont(self.font())
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.DemiBold if self.primary else QFont.Weight.Medium)
        return f

    def set_label(self, text):
        self.setText(text)
        self.setFixedWidth(QFontMetrics(self._font()).horizontalAdvance(text) + (40 if self.primary else 26))
        self.update()

    def enterEvent(self, e):
        self.smooth("_h", 1.0, 150)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.smooth("_h", 0.0, 220)
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isEnabled()
        p.setPen(Qt.PenStyle.NoPen)
        if self.primary:
            col = _mix(QColor(ACCENT["main"]), QColor("#ffffff"), 0.2 * self._h)
            if self.isDown():
                col = col.darker(110)
            if not on:
                col.setAlpha(80)
            p.setBrush(col)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            txt = QColor(7, 18, 26) if on else QColor(7, 18, 26, 140)
        else:
            p.setBrush(QColor(255, 255, 255, int(20 * self._h)))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            txt = _mix(QColor(TOUR_MUTED), QColor("#ffffff"), self._h) if on else QColor(80, 95, 108)
        p.setFont(self._font())
        p.setPen(txt)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class WelcomeTour(Smooth, QWidget):
    """The first-run walkthrough. Lives over the whole window; every choice it offers is applied live."""
    closed = pyqtSignal()
    CARD_W, CARD_H = 676, 628

    def __init__(self, browser, parent):
        super().__init__(parent)
        self.b = browser
        self.setObjectName("tour")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scrim, self._slide = 0.0, 0.0
        self.i, self.t = -1, 0.0
        self._nav = self._importing = self._closing = self._imp_done = False
        self.page_w = {}
        self.pages = [("welcome", "wordmark", self._pg_welcome, "Get started"),
                      ("import", "import", self._pg_import, "Continue"),
                      ("accent", "palette", self._pg_accent, "Continue"),
                      ("layout", "layout", self._pg_layout, "Continue"),
                      ("tabs", "sidebar", self._pg_tabs, "Continue"),
                      ("speed", "speed", self._pg_speed, "Continue"),
                      ("privacy", "shield", self._pg_privacy, "Continue"),
                      ("tools", "scratch", self._pg_tools, "Continue"),
                      ("done", "check", self._pg_done, "Start browsing")]
        self.card = TourCard(self)
        self.card.setFixedSize(self.CARD_W, self.CARD_H)
        m = TourCard.M
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(m + 36, m + 6, m + 36, m + 18)
        cl.setSpacing(0)
        self.hero = TourHero()
        self.hero.setFixedHeight(112)
        cl.addWidget(self.hero)
        self.body = QStackedWidget()
        cl.addWidget(self.body, 1)
        foot = QGridLayout()
        foot.setContentsMargins(0, 12, 0, 0)
        self.btn_skip = TourButton("Skip tour")
        self.dots = TourDots(len(self.pages))
        self.btn_back = TourButton("Back")
        self.btn_next = TourButton("Get started", True)
        right = QHBoxLayout()
        right.setSpacing(6)
        right.addWidget(self.btn_back)
        right.addWidget(self.btn_next)
        foot.addWidget(self.btn_skip, 0, 0, Qt.AlignmentFlag.AlignLeft)
        foot.addWidget(self.dots, 0, 1, Qt.AlignmentFlag.AlignCenter)
        foot.addLayout(right, 0, 2, Qt.AlignmentFlag.AlignRight)
        foot.setColumnStretch(0, 1)
        foot.setColumnStretch(2, 1)
        cl.addLayout(foot)
        self.btn_back.hide()
        self.btn_skip.clicked.connect(self.close_tour)
        self.btn_back.clicked.connect(self.back)
        self.btn_next.clicked.connect(self.next)
        parent.installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.hide()

    # ----- frame, painting, input -----
    def eventFilter(self, o, e):
        if o is self.parentWidget() and e.type() == QEvent.Type.Resize:
            self.place()
        return False

    def place(self):
        p = self.parentWidget()
        if p is None:
            return
        self.setGeometry(p.rect())
        self.place_card()
        self.raise_()

    def place_card(self):
        self.card.move((self.width() - self.card.width()) // 2,
                       max(0, (self.height() - self.card.height()) // 2) + int(self._slide))

    def paintEvent(self, e):
        p = QPainter(self)
        s = max(0.0, min(1.0, self._scrim))
        p.fillRect(self.rect(), QColor(5, 9, 14, int(216 * s)))
        g = QRadialGradient(QPointF(self.width() / 2.0, self.height() / 2.0), max(self.width(), self.height()) * 0.55)
        g.setColorAt(0.0, accent_color(int(38 * s)))
        g.setColorAt(1.0, accent_color(0))
        p.fillRect(self.rect(), QBrush(g))
        p.end()

    def mousePressEvent(self, e):
        # the tour covers the window buttons, so hand clicks on them through (you can always close or minimise the window)
        try:
            wc, root = self.b.winctl, self.parentWidget()
            pt = e.position().toPoint()
            local = wc.mapFrom(root, pt)
            if wc.isVisible() and wc.rect().contains(local):
                btn = wc.childAt(local)
                if btn is not None and hasattr(btn, "click"):
                    btn.click()
        except Exception:
            pass
        e.accept()

    def wheelEvent(self, e):
        e.accept()

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.next()
        elif k == Qt.Key.Key_Left:
            self.back()
        elif k == Qt.Key.Key_Escape and not self._importing:
            self.close_tour()
        else:
            e.accept()

    def _tick(self):
        self.t += 0.033
        self.hero.t = self.t
        self.hero.update()

    def _accent_changed(self):
        self.hero.set_color(QColor(ACCENT["main"]))
        for w in self.findChildren(QWidget):
            w.update()

    # ----- opening, closing, moving between pages -----
    def start(self):
        self.place()
        self.show()
        self.raise_()
        self.setFocus()
        self.timer.start(33)
        self.smooth("_scrim", 1.0, 650)
        fx = QGraphicsOpacityEffect(self.card)
        fx.setOpacity(0.0)
        self.card.setGraphicsEffect(fx)
        a = QVariantAnimation(self)
        a.setDuration(750)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)

        def step(v):
            fx.setOpacity(min(1.0, float(v) * 1.5))
            self._slide = 34.0 * (1.0 - float(v))
            self.place_card()
        a.valueChanged.connect(step)
        a.finished.connect(lambda: self.card.setGraphicsEffect(None))
        self._intro = a
        a.start()
        QTimer.singleShot(280, lambda: self.go(0))

    def close_tour(self):
        if self._closing or self._importing:
            return
        self._closing = True
        self.b._import_hook = None
        fx = QGraphicsOpacityEffect(self.card)
        self.card.setGraphicsEffect(fx)
        a = QVariantAnimation(self)
        a.setDuration(480)
        a.setStartValue(1.0)
        a.setEndValue(0.0)
        a.setEasingCurve(QEasingCurve.Type.InCubic)

        def step(v):
            fx.setOpacity(float(v))
            self._slide = 22.0 * (1.0 - float(v))
            self.place_card()

        def done():
            self.timer.stop()
            self.hide()
            self.closed.emit()
            self.deleteLater()
        a.valueChanged.connect(step)
        a.finished.connect(done)
        self._outro = a
        self.smooth("_scrim", 0.0, 520)
        a.start()

    def _page(self, i):
        if i not in self.page_w:
            w = self.pages[i][2]()
            self.body.addWidget(w)
            self.page_w[i] = w
        return self.page_w[i]

    def go(self, i):
        if self._nav or self._closing or i == self.i or not 0 <= i < len(self.pages):
            return
        self._nav = True
        old = self.body.currentWidget() if self.i >= 0 else None

        def show_new():
            self.i = i
            page = self._page(i)
            self.body.setCurrentWidget(page)
            if old is not None:
                old.setGraphicsEffect(None)
            _name, hero_kind, _b, cta = self.pages[i]
            self.hero.set_kind(hero_kind)
            self.hero.smooth("_in", 1.0, 800)
            self.dots.set_index(i)
            self.btn_next.set_label(cta)
            self.btn_back.setVisible(i > 0)
            self._stagger(page)
            QTimer.singleShot(460, lambda: setattr(self, "_nav", False))
        if old is None:
            show_new()
            return
        fx = RiseEffect(old, -1)
        fx.set_progress(1.0)
        old.setGraphicsEffect(fx)
        self.hero.smooth("_in", 0.0, 160)
        a = QVariantAnimation(self)
        a.setDuration(170)
        a.setStartValue(1.0)
        a.setEndValue(0.0)
        a.setEasingCurve(QEasingCurve.Type.InCubic)
        a.valueChanged.connect(lambda v: fx.set_progress(float(v)))
        a.finished.connect(show_new)
        self._out = a
        a.start()

    def _stagger(self, page):
        """Bring a page's pieces in one after another, each rising and fading into place."""
        page.layout().activate()
        for n, w in enumerate(page._stag):
            fx = RiseEffect(w, 1)
            w.setGraphicsEffect(fx)
            QTimer.singleShot(70 * n, lambda w=w, fx=fx: self._reveal(w, fx))
            QTimer.singleShot(70 * n + 1600, lambda w=w, fx=fx: self._unfx(w, fx))  # failsafe

    def _reveal(self, w, fx):
        try:
            a = QVariantAnimation(w)
            a.setDuration(560)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.valueChanged.connect(lambda v: fx.set_progress(float(v)))
            a.finished.connect(lambda: self._unfx(w, fx))
            a.start()
        except RuntimeError:
            pass

    @staticmethod
    def _unfx(w, fx):
        try:
            if w.graphicsEffect() is fx:
                w.setGraphicsEffect(None)
        except RuntimeError:
            pass

    def next(self):
        if self._nav or self._importing or self._closing:
            return
        if self.pages[self.i][0] == "import" and self._start_imports():
            return
        if self.i >= len(self.pages) - 1:
            self.close_tour()
            return
        self.go(self.i + 1)

    def back(self):
        if self._nav or self._importing or self._closing:
            return
        self.go(self.i - 1)

    # ----- building blocks for the pages -----
    def _make_page(self, title, sub):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(0)
        page._stag = []
        t = QLabel(title)
        t.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        t.setStyleSheet("color:%s;font-size:23px;font-weight:300;background:transparent" % TOUR_TEXT)
        s = QLabel(sub)
        s.setWordWrap(True)
        s.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        s.setStyleSheet("color:%s;font-size:13px;background:transparent" % TOUR_MUTED)
        lay.addWidget(t)
        lay.addSpacing(5)
        lay.addWidget(s)
        lay.addSpacing(16)
        page._stag += [t, s]
        return page, lay

    @staticmethod
    def _add(page, w, gap=0):
        page.layout().addWidget(w)
        page._stag.append(w)
        if gap:
            page.layout().addSpacing(gap)

    @staticmethod
    def _label(text, css, wrap=True, center=False):
        lb = QLabel(text)
        lb.setWordWrap(wrap)
        lb.setStyleSheet(css + ";background:transparent")
        if center:
            lb.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        return lb

    def _cap(self, text):
        lb = self._label(text, "color:%s;font-size:10px;font-weight:600" % TOUR_MUTED, False)
        f = lb.font()
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        lb.setFont(f)
        return lb

    def _chips(self, keys):
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        for c in key_chips(keys):
            h.addWidget(self._label(c, "background:rgba(255,255,255,0.09);border-radius:6px;padding:2px 7px;"
                                       "color:#cfe0ec;font-size:11px", False))
        return wrap

    def _row(self, glyph, title, desc, keys=None, right=None):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)
        h.addWidget(TourGlyph(glyph), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self._label(title, "color:%s;font-size:14px;font-weight:600" % TOUR_TEXT, False))
        if keys:
            head.addWidget(self._chips(keys))
        head.addStretch(1)
        col.addLayout(head)
        col.addWidget(self._label(desc, "color:%s;font-size:12px" % TOUR_MUTED))
        h.addLayout(col, 1)
        if right is not None:
            h.addWidget(right, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    @staticmethod
    def _hbox(widgets, spacing=10, center=True):
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(spacing)
        if center:
            h.addStretch(1)
        for w in widgets:
            h.addWidget(w)
        if center:
            h.addStretch(1)
        return wrap

    # ----- pages -----
    def _pg_welcome(self):
        page, lay = self._make_page("Welcome to Fjord", "The web, calm and clear.\nLet's make it yours. It only takes a minute.")
        lay.addSpacing(8)
        steps = [("Import", "import"), ("Colour", "palette"), ("Layout", "layout"), ("Speed", "speed"), ("Privacy", "shield")]
        pills = []
        for name, _g in steps:
            pills.append(self._label(name, "background:transparent;border:1px solid rgba(255,255,255,0.12);border-radius:12px;padding:4px 13px;"
                                           "color:#9fb3c3;font-size:12px", False))
        self._add(page, self._hbox(pills, 8), 14)
        self._add(page, self._label("Use \u2190 \u2192 or Enter to move around. You can replay this any time from the \u22ef menu.",
                                    "color:#5f7487;font-size:11px", True, True))
        lay.addStretch(1)
        return page

    def _import_row(self, src):
        row = TourRowCard()
        row.setFixedHeight(56)
        h = QHBoxLayout(row)
        h.setContentsMargins(16, 0, 12, 0)
        h.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(0)
        name = self._label(src["name"], "color:%s;font-size:13px;font-weight:600" % TOUR_TEXT, False)
        name.setMinimumWidth(40)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addStretch(1)
        col.addWidget(name)
        col.addWidget(self._label("Bookmarks and history" if src.get("history") else "Bookmarks only",
                                  "color:%s;font-size:11px" % TOUR_MUTED, False))
        col.addStretch(1)
        h.addLayout(col, 1)
        labels = ["Skip", "Bookmarks", "Bookmarks + history"] if src.get("history") else ["Skip", "Import bookmarks"]
        seg = TourSegment(labels, 0)
        seg.changed.connect(lambda _i: setattr(self, "_imp_done", False))
        h.addWidget(seg)
        self.imp_rows.append((src, seg))
        return row

    def _pg_import(self):
        page, lay = self._make_page("Bring your stuff along",
                                    "Choose what to import from the browsers on this computer. Passwords and cookies stay put.")
        self.imp_rows = []
        sources = detect_import_sources()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none}")
        scroll.viewport().setAutoFillBackground(False)
        scroll.setFixedHeight(216)
        inner = QWidget()
        inner.setAutoFillBackground(False)
        self.imp_lay = QVBoxLayout(inner)
        self.imp_lay.setContentsMargins(0, 0, 4, 0)
        self.imp_lay.setSpacing(8)
        for src in sources:
            self.imp_lay.addWidget(self._import_row(src))
        if not sources:
            self.imp_lay.addWidget(self._label("No other browsers found on this computer.\nYou can still import a bookmarks file "
                                               "exported from any browser.", "color:%s;font-size:12px" % TOUR_MUTED, True, True))
        self.imp_lay.addStretch(1)
        scroll.setWidget(inner)
        self._add(page, scroll, 10)
        file_btn = TourButton("Import a bookmarks file\u2026")
        file_btn.clicked.connect(self._pick_import_file)
        self._add(page, self._hbox([file_btn]), 4)
        self.imp_status = self._label("", "color:%s;font-size:12px" % TOUR_MUTED, True, True)
        self._add(page, self.imp_status)
        lay.addStretch(1)
        return page

    def _pick_import_file(self):
        path, _f = QFileDialog.getOpenFileName(self, "Import bookmarks", str(Path.home()),
                                               "Bookmarks files (*.html *.htm);;All files (*)")
        if not path:
            return
        src = {"kind": "html", "path": path, "name": Path(path).name, "history": False}
        row = self._import_row(src)
        self.imp_lay.insertWidget(max(0, self.imp_lay.count() - 1), row)
        self.imp_rows[-1][1].set_index(1)

    def _set_status(self, text):
        self.imp_status.setText(text)

    def _start_imports(self):
        if self._imp_done:
            return False
        self._jobs = [(s, seg.index == 2) for s, seg in self.imp_rows if seg.index > 0]
        if not self._jobs:
            return False
        self._importing = True
        self._imp_total, self._imp_ok, self._imp_err = len(self._jobs), 0, ""
        for w in (self.btn_next, self.btn_back, self.btn_skip):
            w.setEnabled(False)
        for _s, seg in self.imp_rows:
            seg.setEnabled(False)
        self._next_job()
        return True

    def _next_job(self):
        if not self._jobs:
            self._imports_finished()
            return
        if self.b._import_busy:
            QTimer.singleShot(300, self._next_job)
            return
        src, hist = self._jobs.pop(0)
        self._set_status("Importing from %s\u2026  (%d of %d)" % (src["name"], self._imp_total - len(self._jobs), self._imp_total))
        self.b._import_busy = True
        self.b._import_hook = self._job_done
        self.b.importer.run(src, hist)

    def _job_done(self, ok, msg):
        if ok:
            self._imp_ok += 1
        else:
            self._imp_err = msg
        self._next_job()

    def _imports_finished(self):
        self.b._import_hook = None
        self._importing = False
        for w in (self.btn_next, self.btn_back, self.btn_skip):
            w.setEnabled(True)
        for _s, seg in self.imp_rows:
            seg.setEnabled(True)
        if self._imp_ok:
            self._imp_done = True
            self._set_status("Done. Imported from %d browser%s." % (self._imp_ok, "" if self._imp_ok == 1 else "s"))
            QTimer.singleShot(1000, self.next)
        else:
            self._set_status(self._imp_err or "Nothing could be imported.")

    def _pg_accent(self):
        page, lay = self._make_page("Choose your accent",
                                    "A splash of colour for tabs, buttons and highlights. Pick one and watch Fjord change.")
        cur = self.b.settings.get("accent")
        cur_main = str(cur.get("main", "")).lower() if isinstance(cur, dict) else ""
        self.swatches = []
        sw_defs = [TourSwatch(n, hx, "default" if hx is None else "color") for n, hx in TOUR_SWATCHES]
        sw_defs.append(TourSwatch("Custom colour\u2026", None, "custom"))
        for sw in sw_defs:
            if sw.kind == "default":
                on = not cur_main
            elif sw.kind == "color":
                pair = derive_accent(sw.hexcol)
                on = bool(pair) and pair[0].lower() == cur_main
            else:
                on = False
            sw._sel = 1.0 if on else 0.0
            sw.picked.connect(lambda s=sw: self._pick_swatch(s))
            self.swatches.append(sw)
        if cur_main and not any(s._sel > 0.5 for s in self.swatches):  # a colour picked earlier with the custom swatch
            self.swatches[-1].hexcol, self.swatches[-1]._sel = cur_main, 1.0
        self.accent_name = self._label("", "color:%s;font-size:13px;font-weight:600" % TOUR_TEXT, False, True)
        chosen = next((s for s in self.swatches if s._sel > 0.5), None)
        self.accent_name.setText(chosen.name if chosen else "Custom")
        self._add(page, self._hbox(self.swatches[:7], 10), 10)
        self._add(page, self._hbox(self.swatches[7:], 10), 18)
        self._add(page, self.accent_name, 6)
        self._add(page, self._label("Fjord also tints its bars and sidebar to match, so every colour feels a little different. "
                                    "Change it later in Settings.", "color:#5f7487;font-size:11px", True, True))
        lay.addStretch(1)
        return page

    def _pick_swatch(self, sw):
        name = sw.name
        if sw.kind == "custom":
            c = QColorDialog.getColor(QColor(sw.hexcol or ACCENT["main"]), self, "Pick an accent colour")
            if not c.isValid():
                return
            pair = derive_accent(c.name(), 0.12)
            if pair is None:
                self.b.toast("That one is a little grey. Try something more colourful.", 3500)
                return
            sw.set_color(c.name())
            name = "Custom"
        elif sw.kind == "default":
            pair = None
        else:
            pair = derive_accent(sw.hexcol)
        for s in self.swatches:
            s.smooth("_sel", 1.0 if s is sw else 0.0, 380, QEasingCurve.Type.OutBack)
        self.accent_name.setText(name)
        self.b.set_accent(pair)
        self._accent_changed()

    def _choice_group(self, items, current, on_pick):
        """items: list of (key, TourChoice). Keeps exactly one selected and calls on_pick(key) on a change."""
        group = []

        def pick(key):
            if key == self._cur_of(group):
                return
            for k, ch in group:
                ch.set_selected(k == key)
            on_pick(key)
        for key, ch in items:
            ch.set_selected(key == current, False)
            ch.picked.connect(lambda k=key: pick(k))
            group.append((key, ch))
        return group

    @staticmethod
    def _cur_of(group):
        for k, ch in group:
            if ch._sel > 0.5:
                return k
        return None

    def _pg_layout(self):
        page, lay = self._make_page("Make it feel right",
                                    "Where your tabs live and how the window looks. Both are live, and both live in Settings too.")
        cur_layout = "horizontal" if self.b.settings.get("layout") == "horizontal" else "vertical"
        lay_items = [("vertical", TourChoice("Vertical tabs", "A sidebar on the left", _preview_layout("vertical"), (264, 112))),
                     ("horizontal", TourChoice("Horizontal tabs", "A strip across the top", _preview_layout("horizontal"), (264, 112)))]
        self._choice_group(lay_items, cur_layout, self._set_layout)
        self._add(page, self._cap("TAB LAYOUT"), 6)
        self._add(page, self._hbox([c for _k, c in lay_items], 16), 14)
        style_items = [(m, TourChoice(lbl, sub, _preview_style(m), (176, 112)))
                       for m, lbl, sub in (("default", "Default", "Fjord as it is"), ("mac", "macOS", "Liquid glass, soft motion"),
                                           ("windows", "Windows", "Flat, Windows 11 look"))]
        self._choice_group(style_items, UI["mode"], self._set_style)
        self._add(page, self._cap("INTERFACE STYLE"), 6)
        self._add(page, self._hbox([c for _k, c in style_items], 12))
        lay.addStretch(1)
        return page

    def _set_layout(self, v):
        self.b.settings["layout"] = v
        jsave("settings.json", self.b.settings)
        self.b.apply_layout()

    def _set_style(self, v):
        self.b.set_ui_style(v, refresh_settings=False)
        self._accent_changed()

    def _pg_tabs(self):
        page, lay = self._make_page("Tabs, your way", "Fjord keeps your tabs out of the way and your favourites close.")
        rows = [("sidebar", "Sidebar tabs", "Your tabs live on the side. Hide it any time, or drag its edge to resize it.", "Ctrl+B"),
                ("pin", "Essentials", "Pin the sites you open every day above your tabs. Right-click a tab, then Add to Essentials.", None),
                ("group", "Tab groups", "Collect related tabs into colourful islands you can fold away. Right-click a tab, then Add to group.", None),
                ("split", "Split view", "Drop one tab onto another to browse two pages side by side.", None)]
        for g, t, d, k in rows:
            self._add(page, self._row(g, t, d, k), 16)
        lay.addStretch(1)
        return page

    def _pg_speed(self):
        page, lay = self._make_page("How fast, how frugal?",
                                    "Choose how much of your computer Fjord may use. The speedometer in the toolbar switches it any time.")
        tints = {"eco": "#4ade80", "normal": None, "turbo": "#f87171"}
        glyphs = {"eco": "speed_eco", "normal": "speed", "turbo": "speed_turbo"}
        items = [(k, TourChoice(m["label"], m["desc"], _preview_glyph(glyphs[k], tints[k]), (176, 128), tints[k]))
                 for k, m in SPEED_MODES.items()]
        self._choice_group(items, self.b.speed_mode, self.b.set_speed_mode)
        self._add(page, self._hbox([c for _k, c in items], 12), 18)
        self._add(page, self._row("clock", "Sleeping tabs",
                                  "Tabs you are not using doze off to save memory, then wake the moment you come back. "
                                  "Turbo keeps everything awake."), 12)
        self._add(page, self._label("Eco and Turbo also change process and GPU limits, which take full effect after a restart.",
                                    "color:#5f7487;font-size:11px", True, True))
        lay.addStretch(1)
        return page

    def _pg_privacy(self):
        page, lay = self._make_page("Private by default", "Quieter pages, and a few tools to keep you in control.")
        tog = TourToggle(bool(self.b.adblock.enabled))
        tog.toggled.connect(self.b.set_adblock)
        self._add(page, self._row("block", "Ads and trackers blocked",
                                  "Cleaner, faster pages. Pause blocking for a single site from the \u22ef menu if one breaks.",
                                  right=tog), 18)
        self._add(page, self._row("shield", "VPN and proxy",
                                  "The shield button in the toolbar sends your traffic through a SOCKS or HTTP proxy with one click. "
                                  "Set the proxy up in Settings."), 18)
        self._add(page, self._row("clock", "Site time budgets",
                                  "Give distracting sites a daily limit. Fjord nudges you when it is used up. "
                                  "Find it under \u22ef, Privacy and performance."), 4)
        lay.addStretch(1)
        return page

    def _pg_tools(self):
        page, lay = self._make_page("Little helpers", "Small tools that save you a trip to another app.")
        ext_note = "" if QWebEngineExtensionManager is not None else " (Needs PyQt6 6.10 or newer.)"
        rows = [("scratch", "Scratchpad", "Drop images, files, text or links on the sidebar to keep a copy for later.", "Ctrl+Shift+S"),
                ("note", "Sticky notes", "Click the little note button at the bottom right of any site to pin a note to it.", None),
                ("play", "Media player", "Whatever is playing shows up in the sidebar with its controls. Right-click it for options.", None),
                ("puzzle", "Extensions", "Add Chrome, Firefox and Safari extensions from the puzzle-piece button." + ext_note, None)]
        for g, t, d, k in rows:
            self._add(page, self._row(g, t, d, k), 16)
        lay.addStretch(1)
        return page

    def _pg_done(self):
        page, lay = self._make_page("You're all set", "A few shortcuts worth knowing. Everything else lives in the \u22ef menu.")
        shortcuts = [("New tab", "Ctrl+T"), ("Address bar", "Ctrl+L"), ("Bookmark page", "Ctrl+D"),
                     ("Find in page", "Ctrl+F"), ("Reopen closed tab", "Ctrl+Shift+T"), ("History", "Ctrl+H")]
        grid = QWidget()
        gl = QGridLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setHorizontalSpacing(10)
        gl.setVerticalSpacing(8)
        for n, (name, keys) in enumerate(shortcuts):
            cell = TourRowCard()
            cell.setFixedHeight(44)
            h = QHBoxLayout(cell)
            h.setContentsMargins(14, 0, 12, 0)
            h.addWidget(self._label(name, "color:%s;font-size:12px" % TOUR_TEXT, False), 1)
            h.addWidget(self._chips(keys))
            gl.addWidget(cell, n // 2, n % 2)
        self._add(page, grid, 14)
        self._add(page, self._label("Want to see this again? Open the \u22ef menu and choose Welcome tour.",
                                    "color:#5f7487;font-size:11px", True, True))
        lay.addStretch(1)
        return page


class Browser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fjord")
        # No native title bar: Fjord draws its own window buttons. MinMaxButtonsHint keeps taskbar minimise/restore working on Windows.
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowMinMaxButtonsHint)
        self.resize(1280, 820)
        DATA_DIR.mkdir(exist_ok=True)
        self.settings = jload("settings.json", {"engine": "Google"})
        UI["mode"] = valid_ui_mode(self.settings.get("ui_style"))
        load_ui_tuning(self.settings)
        self.tour = None            # the WelcomeTour overlay while it is open
        self._import_hook = None    # lets the tour follow an import without the usual toast and bookmarks page
        self.bookmarks = jload("bookmarks.json", [])
        self.history = jload("history.json", [])
        self.notes = jload("notes.json", {})   # host -> list of sticky notes
        if not isinstance(self.notes, dict):
            self.notes = {}
        self.essentials = jload("essentials.json", [])
        self.bgserver = BgServer()
        self.bgserver.start()
        self.bgserver.event.connect(self.on_bg_event)
        self.apply_bg_globals()
        self.topsites = jload("topsites.json", {})
        if not isinstance(self.topsites, dict):
            self.topsites = {}
        self.topsites.setdefault("pinned", [])
        self.topsites.setdefault("hidden", [])
        self.tiles = []
        self.horiz = False
        self.compact = False
        self.groups = []
        self.gprog = {}   # group id -> how far its island is slid open (0..1) while animating
        self.ganim = {}   # group id -> running QVariantAnimation
        self.chips = {}   # group id -> island header widget
        self._restored = False   # session saving stays off until the saved session has been loaded
        self._save_pending = False
        ICON_DIR.mkdir(exist_ok=True)
        self.closed = []
        self.sidebar_wanted = True
        self.sidebar_w = clamp_side_w(self.settings.get("sidebar_w", SIDE_DEFAULT_W))

        self.profile = QWebEngineProfile("fjord", self)
        self.profile.setPersistentStoragePath(str(DATA_DIR / "profile"))
        self.speed_mode = self.settings.get("speed_mode") if self.settings.get("speed_mode") in SPEED_MODES else "normal"
        self.profile.setHttpCacheMaximumSize(SPEED_MODES[self.speed_mode]["cache_mb"] * 1024 * 1024)
        self.profile.setCachePath(str(DATA_DIR / "cache"))
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        self.profile.downloadRequested.connect(self.on_download)
        hook = QWebEngineScript()  # lets the sidebar media player trigger next/previous track
        hook.setName("fjord-media-hook")
        hook.setSourceCode(MEDIA_HOOK_JS)
        hook.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        hook.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        hook.setRunsOnSubFrames(False)
        self.profile.scripts().insert(hook)
        notes_hook = QWebEngineScript()  # the sticky-note button; asks for each site's notes once it is up
        notes_hook.setName(NOTES_SCRIPT)
        notes_hook.setSourceCode(NOTES_JS.replace("__COLORS__", json.dumps(NOTE_COLORS)))
        notes_hook.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        notes_hook.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        notes_hook.setRunsOnSubFrames(False)
        self.profile.scripts().insert(notes_hook)
        s = self.profile.settings()
        for a, v in ((QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True),
                     (QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True),
                     (QWebEngineSettings.WebAttribute.PluginsEnabled, True),
                     (QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)):
            s.setAttribute(a, v)
        if hasattr(QWebEngineSettings.WebAttribute, "ForceDarkMode"):
            s.setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, True)

        self.adblock = AdBlock(self.settings)
        self.adblock.ready.connect(self.on_adblock_ready)
        self.interceptor = AdInterceptor(self.adblock)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.adblock.install_global(self.profile)
        self.extensions = ExtensionHub(self)
        self.extensions.changed.connect(self.on_ext_changed)
        self.extensions.load_all()
        self._build_ui()
        self._build_shortcuts()
        self.refresh_completer()
        self.apply_layout()
        self.update_engine_btn()
        self._restore_session(sys.argv[1:])
        self.open_essentials_bg()
        self.update_vpn_btn()
        self.updater = Updater(self)
        self.updater.found.connect(self.on_update_found)
        self.updater.uptodate.connect(self.on_update_uptodate)
        self.updater.failed.connect(self.on_update_failed)
        self.updater.installed.connect(self.on_update_installed)
        self._update_manual = False
        self._update_busy = False
        self.importer = Importer(self)
        self.importer.done.connect(self.on_import_done)
        self.importer.failed.connect(self.on_import_failed)
        self._import_busy = False
        self._import_sources = []
        self.budgets = self._budgets_load()
        self._b_last = time.monotonic()
        self._b_ticks = 0
        self._b_warned = set()
        self._b_suggest = ""
        self.budget_ov = BudgetOverlay(self, self.stack)
        self.budget_ov.more.connect(self.budget_more)
        self.budget_ov.skip.connect(self.budget_skip)
        self.budget_ov.close_tab.connect(self.budget_close)
        self._budget_timer = QTimer(self)
        self._budget_timer.timeout.connect(self.budget_tick)
        self._budget_timer.start(1000)
        if self.settings.get("auto_update", True):
            QTimer.singleShot(5000, lambda: self.check_updates(False))
        self.adblock.load_async()
        self.icons = IconFetcher()
        self.icons.ready.connect(self.on_icons_ready)
        self.icons.fetch()
        QApplication.instance().focusChanged.connect(self.on_focus_changed)
        if not self.settings.get("tour_done"):
            QTimer.singleShot(900, self.start_tour)

    @property
    def engine(self):
        return self.settings.get("engine", "Google")

    # ----- UI -----
    def _build_ui(self):
        root = Backdrop()
        root.setObjectName("root")
        outer = QHBoxLayout(root)
        outer.setContentsMargins(frame_margin(), frame_margin(), frame_margin(), frame_margin())
        outer.setSpacing(8)
        self.setCentralWidget(root)

        self.grip = None
        self.side = SideFrame(self.sidebar_leave, self.place_grip)
        self.side.setObjectName("sidebar")
        self.side.setFixedWidth(self.side_width())
        self.grip = SideGrip(root, lambda: self.side.width(), self.set_side_width, self.save_side_width,
                             lambda: self.set_side_width(SIDE_DEFAULT_W, save=True))
        self.player = MediaPlayer(self, self.side)
        self.side_lay = QVBoxLayout(self.side)
        self.side_lay.setContentsMargins(10, 10, 10, 10)
        self.side_lay.setSpacing(8)
        self.newtab = QPushButton("＋  New tab")
        self.newtab.setObjectName("newtab")
        self.newtab.setCursor(Qt.CursorShape.PointingHandCursor)
        self.newtab.clicked.connect(lambda: self.new_tab(focus_address=True))
        self.tabs = TabList(lambda r: self.close_tab(self.stack.widget(r)))
        self.tabs.setItemDelegate(TabDelegate(self.tabs))
        self.tabs.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tabs.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self.tab_menu)
        self.tabs.currentRowChanged.connect(self.on_row_changed)
        self.tabs.on_drop = self.start_split_rows
        self.ess_wrap = QWidget()
        self.ess_grid = QGridLayout(self.ess_wrap)
        self.ess_grid.setContentsMargins(0, 0, 0, 0)
        self.ess_grid.setSpacing(6)
        self.divider = QFrame()
        self.divider.setObjectName("divider")
        self.divider.setFixedHeight(1)
        self.group_wrap = QWidget()
        self.group_lay = QBoxLayout(QBoxLayout.Direction.TopToBottom, self.group_wrap)
        self.group_lay.setContentsMargins(0, 0, 0, 0)
        self.group_lay.setSpacing(4)
        self.edge = HoverZone(lambda: self.set_sidebar(True))
        self.edge.setFixedWidth(6)
        self.edge.hide()
        self._sleep_timer = QTimer(self)
        self._sleep_timer.timeout.connect(self.sleep_tabs)
        self._sleep_ticks = 0
        self._sleep_timer.start(15000)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._autohide_check)
        outer.addWidget(self.edge)
        outer.addWidget(self.side)
        outer.addWidget(self.side)

        right = QVBoxLayout()
        right.setSpacing(4)
        self.strip = QWidget()
        self.strip_lay = QHBoxLayout(self.strip)
        self.strip_lay.setContentsMargins(0, 0, 0, 0)
        self.strip_lay.setSpacing(6)
        self.strip_fill = QWidget()
        self.strip_fill.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.strip.hide()
        right.addWidget(self.strip)
        self.toolbar = GlassBar()
        tb = QHBoxLayout(self.toolbar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(6)
        self.tb_lay = tb
        self.apply_toolbar_margins()
        self.btn_side = ToolIcon("sidebar", "Toggle sidebar  (Ctrl+B)", self.toggle_sidebar)
        self.btn_back = ToolIcon("back", "Back  (Alt+←)", lambda: self.cur().back())
        self.btn_fwd = ToolIcon("forward", "Forward  (Alt+→)", lambda: self.cur().forward())
        self.btn_reload = ToolIcon("reload", "Reload  (Ctrl+R)", self.reload_or_stop)
        self.addr = AddressBar()
        self.addr.setFixedHeight(ToolIcon.SIZE)
        self.addr.setPlaceholderText("Search or enter address")
        self.addr.returnPressed.connect(self.navigate)
        self.completer = QCompleter(QStringListModel(self), self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setMaxVisibleItems(8)
        self.completer.popup().setStyleSheet(themed(popup_qss()))
        self.completer.activated[str].connect(
            lambda t: (self.addr.setText(t), QTimer.singleShot(0, self.navigate)))
        self.addr.setCompleter(self.completer)
        self.btn_engine = self._tool("Google ▾", self.show_engine_menu)
        self.btn_engine.setObjectName("engine")
        self.btn_engine.setFixedSize(ToolIcon.SIZE, ToolIcon.SIZE)
        self.btn_speed = ToolIcon("speed", "", self.show_speed_menu)
        self.update_speed_btn()
        self.btn_vpn = ToolIcon("shield", "Proxy/VPN is off - click to turn on", self.toggle_vpn)
        self.btn_ext = ToolIcon("puzzle", "Extensions", self.show_ext_menu)
        self.btn_star = ToolIcon("star", "Bookmark this page  (Ctrl+D)", self.toggle_bookmark)
        self.btn_menu = ToolIcon("dots", "Menu", self.show_menu)
        self.drag_zone = QWidget()  # a bit of bare toolbar to grab and drag the window by
        self.drag_zone.setFixedWidth(4)
        self.winctl = WindowControls(self, effective_winbtns(self.settings))
        self.winctl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.winctl.customContextMenuRequested.connect(
            lambda pos: self.winbtn_menu().exec(self.winctl.mapToGlobal(pos)))
        self.toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.toolbar.customContextMenuRequested.connect(self.toolbar_menu)
        right.addWidget(self.toolbar)  # the buttons themselves are placed by ToolbarEditor.apply() below

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.hide()
        self.prog_fx = QGraphicsOpacityEffect(self.progress)
        self.progress.setGraphicsEffect(self.prog_fx)
        right.addWidget(self.progress)
        self.stack = TabArea()
        right.addWidget(self.stack, 1)

        self.find = QLineEdit()
        self.find.setPlaceholderText("Find in page  (Enter = next, Esc = close)")
        self.find.hide()
        self.find.textChanged.connect(lambda t: self.cur().findText(t))
        self.find.returnPressed.connect(lambda: self.cur().findText(self.find.text()))
        esc = QShortcut(QKeySequence("Esc"), self.find)
        esc.setContext(Qt.ShortcutContext.WidgetShortcut)
        esc.activated.connect(self.close_find)
        right.addWidget(self.find)
        outer.addLayout(right, 1)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().hide()
        self.hoverbar = HoverBar(self)
        self._hover_text, self._hover_pm, self._toast_text = "", None, ""
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._end_toast)
        self.scratch_btn = ScratchButton()
        self.scratch = ScratchDrawer(self, root)
        DropTarget.scratch = self.scratch
        self.scratch_btn.clicked.connect(self.scratch.toggle)
        self.tb_ed = ToolbarEditor(self)
        right.insertWidget(right.indexOf(self.toolbar) + 1, self.tb_ed.panel)
        self.tb_ed.apply()
        self._build_frame(root)

    # ----- frameless window: resize edges, dragging, window buttons -----
    def _build_frame(self, root):
        C = Qt.CursorShape
        E = Qt.Edge
        spec = ((E.LeftEdge, C.SizeHorCursor), (E.RightEdge, C.SizeHorCursor),
                (E.TopEdge, C.SizeVerCursor), (E.BottomEdge, C.SizeVerCursor),
                (E.TopEdge | E.LeftEdge, C.SizeFDiagCursor), (E.BottomEdge | E.RightEdge, C.SizeFDiagCursor),
                (E.TopEdge | E.RightEdge, C.SizeBDiagCursor), (E.BottomEdge | E.LeftEdge, C.SizeBDiagCursor))
        self.grips = [(EdgeGrip(root, ed, cur), ed) for ed, cur in spec]
        self.drag_filter = DragFilter(self)
        for w in (self.toolbar, self.strip, self.side, self.drag_zone):
            w.installEventFilter(self.drag_filter)
        self.sync_frame()

    def place_grips(self):
        if not hasattr(self, "grips"):
            return
        r = self.centralWidget().rect()
        w, h, e, c = r.width(), r.height(), 6, 12
        E = Qt.Edge
        boxes = {E.LeftEdge: (0, c, e, h - 2 * c), E.RightEdge: (w - e, c, e, h - 2 * c),
                 E.TopEdge: (c, 0, w - 2 * c, e), E.BottomEdge: (c, h - e, w - 2 * c, e),
                 E.TopEdge | E.LeftEdge: (0, 0, c, c), E.BottomEdge | E.RightEdge: (w - c, h - c, c, c),
                 E.TopEdge | E.RightEdge: (w - c, 0, c, c), E.BottomEdge | E.LeftEdge: (0, h - c, c, c)}
        show = not (self.isMaximized() or self.isFullScreen())
        for g, ed in self.grips:
            g.setGeometry(*boxes[ed])
            g.setVisible(show)
            g.raise_()

    def sync_frame(self):
        """Keep margins, resize grips and window buttons in step with normal / maximised / full-screen."""
        if not hasattr(self, "winctl"):
            return
        if self.isFullScreen():
            m = 0
        elif self.isMaximized():
            m = 6 if UI["mode"] == "mac" else 0  # the floating glass needs a little air even when maximised
        else:
            m = frame_margin()
        self.centralWidget().layout().setContentsMargins(m, m, m, m)
        self.place_grips()
        self.winctl.sync()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QEvent.Type.WindowStateChange, QEvent.Type.ActivationChange):
            self.sync_frame()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def toggle_maximize(self):
        if self.isFullScreen() or self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def apply_toolbar_margins(self):
        """The macOS style gives the toolbar a little side padding."""
        if UI["mode"] == "mac":
            self.tb_lay.setContentsMargins(6, 3, 6, 3)
        else:
            self.tb_lay.setContentsMargins(0, 0, 0, 0)

    def set_winbtns(self, mode):
        mode = "mac" if mode == "mac" else "windows"
        self.settings["winbtns"] = mode
        jsave("settings.json", self.settings)
        self.winctl.set_mode(effective_winbtns(self.settings))

    def winbtn_menu(self, parent=None):
        locked = UI["mode"] != "default"  # the macOS / Windows styles pick their own buttons
        m = QMenu("Window buttons" + ("  (set by interface style)" if locked else ""), parent or self)
        cur = effective_winbtns(self.settings)
        for key, label in (("windows", "Windows style"), ("mac", "macOS style")):
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(cur == key)
            a.setEnabled(not locked)
            a.triggered.connect(lambda _c, k=key: self.set_winbtns(k))
        return m

    # ----- interface style: default / macOS / Windows -----
    def style_menu(self, parent=None):
        m = QMenu("Interface style", parent or self)
        for key, _name in UI_MODES:
            a = m.addAction(UI_LABELS[key])
            a.setCheckable(True)
            a.setChecked(UI["mode"] == key)
            a.triggered.connect(lambda _c, k=key: self.set_ui_style(k))
        return m

    def _style_snapshot(self):
        """Grab the window as it looks now, so switching style can cross-fade instead of jumping."""
        try:
            if not self.isVisible() or self.isMinimized() or self.windowOpacity() < 1.0:
                return None
            pm = self.screen().grabWindow(int(self.winId()))
        except Exception:
            return None
        if pm.isNull():
            return None
        root = self.centralWidget()
        lab = QLabel(root)
        lab.setPixmap(pm)
        lab.setScaledContents(True)
        lab.setGeometry(root.rect())
        lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lab.show()
        lab.raise_()
        return lab

    def set_ui_style(self, mode, refresh_settings=True):
        """Switch between the Default, macOS (Safari-like liquid glass) and Windows (Windows 11) interface styles."""
        mode = valid_ui_mode(mode)
        self.settings["ui_style"] = mode
        jsave("settings.json", self.settings)
        if mode == UI["mode"]:
            return
        shot = self._style_snapshot()
        UI["mode"] = mode
        self.winctl.set_mode(effective_winbtns(self.settings))
        QApplication.instance().setStyleSheet(themed(app_qss()))
        self.completer.popup().setStyleSheet(themed(popup_qss()))
        self.addr.refresh_style()
        self.apply_toolbar_margins()
        self.tb_ed.apply()
        self.sync_frame()
        for w in self.findChildren(QWidget):
            w.update()
        self.refresh_start_pages()
        if refresh_settings:
            for t in self.tab_list():
                if t.url().scheme() == "fjord" and t.url().host() == "settings":
                    t.setHtml(settings_html(self), QUrl("fjord://settings"))
        if shot is not None:
            fx = QGraphicsOpacityEffect(shot)
            shot.setGraphicsEffect(fx)
            animate(fx, b"opacity", 1.0, 0.0, 560 if mode == "mac" else 280, done=shot.deleteLater)

    def set_accent(self, pair, fade=True):
        """Set the accent colour (a (main, alt) pair), or None to go back to following the background. The whole UI crossfades."""
        if pair:
            self.settings["accent"] = {"main": pair[0], "alt": pair[1]}
        else:
            self.settings.pop("accent", None)
        jsave("settings.json", self.settings)
        shot = self._style_snapshot() if fade else None
        self.apply_bg_globals()
        self.addr.refresh_style()
        self.tb_ed.apply()
        self.sync_frame()
        for w in self.findChildren(QWidget):
            w.update()
        self.refresh_start_pages()
        for t in self.tab_list():
            if t.url().scheme() == "fjord" and t.url().host() == "settings":
                t.setHtml(settings_html(self), QUrl("fjord://settings"))
        if shot is not None:
            fx = QGraphicsOpacityEffect(shot)
            shot.setGraphicsEffect(fx)
            animate(fx, b"opacity", 1.0, 0.0, 560, done=shot.deleteLater)

    def start_tour(self):
        """The welcome tour: import, accent colour, layout and a run through the features."""
        if self.tour is not None:
            return
        self.tour = WelcomeTour(self, self.centralWidget())
        self.tour.closed.connect(self._tour_closed)
        self.tour.start()

    def _tour_closed(self):
        self.tour = None
        self.settings["tour_done"] = True
        jsave("settings.json", self.settings)
        self.focus_address()

    def apply_ui_tuning(self):
        """Re-apply the corner-radius / glass sliders everywhere, without switching interface style."""
        load_ui_tuning(self.settings)
        QApplication.instance().setStyleSheet(themed(app_qss()))
        self.completer.popup().setStyleSheet(themed(popup_qss()))
        self.addr.refresh_style()
        self.apply_toolbar_margins()
        self.tb_ed.apply()
        self.sync_frame()
        for w in self.findChildren(QWidget):
            w.update()
        self.refresh_start_pages()

    def _tool(self, text, slot):
        b = FadeButton()
        b.setText(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    def _build_shortcuts(self):
        def sc(keys, fn):
            QShortcut(QKeySequence(keys), self, activated=fn)
        sc("Ctrl+T", lambda: self.new_tab(focus_address=True))
        sc("Ctrl+Shift+T", self.reopen_tab)
        sc("Ctrl+W", lambda: self.close_tab(self.cur()))
        sc("Ctrl+L", self.focus_address)
        sc("Ctrl+R", self.reload_or_stop)
        sc("F5", lambda: self.cur().reload())
        sc("Alt+Left", lambda: self.cur().back())
        sc("Alt+Right", lambda: self.cur().forward())
        sc("Ctrl+Tab", lambda: self.cycle(1))
        sc("Ctrl+Shift+Tab", lambda: self.cycle(-1))
        sc("Ctrl+B", self.toggle_sidebar)
        sc("Ctrl+F", self.open_find)
        sc("Ctrl+D", self.toggle_bookmark)
        sc("Ctrl+H", self.open_history)
        sc("Ctrl+Shift+O", self.open_bookmarks)
        sc("Ctrl+P", self.print_pdf)
        sc("Ctrl+Shift+S", self.scratch.toggle)
        sc("Ctrl+,", lambda: self.open_settings())
        for k in ("Ctrl+=", "Ctrl++"):
            sc(k, lambda: self.zoom(0.1))
        sc("Ctrl+-", lambda: self.zoom(-0.1))
        sc("Ctrl+0", lambda: self.cur().setZoomFactor(1.0))
        sc("F11", self.toggle_fullscreen)
        for n in range(1, 10):
            sc(f"Ctrl+{n}", lambda n=n: self.tabs.setCurrentRow(
                self.stack.count() - 1 if n == 9 else min(n - 1, self.stack.count() - 1)))

    # ----- tabs -----
    def cur(self):
        return self.stack.currentWidget()

    def new_tab(self, url=None, blank=False, focus_address=False, background=False, opener=None, lazy=False):
        tab = Tab(self.profile, self)
        row = TabRow(tab, self.close_tab)
        tab.row = row
        item = QListWidgetItem()
        item.setSizeHint(self.item_size(0))
        self.stack.addWidget(tab)
        self.tabs.addItem(item)
        self.tabs.setItemWidget(item, row)
        row.set_compact(self.compact)
        self.animate_item(item, 0, self.extent(), 220)

        pg = tab.page()
        tab.titleChanged.connect(lambda t, tb=tab: (tb.row.title.setText(t or "New Tab"), tb.row.setToolTip(t)))
        tab.iconChanged.connect(lambda ic, t=tab: self.on_icon(t, ic))
        tab.urlChanged.connect(lambda _u, t=tab: self.sync_chrome(t))
        tab.loadStarted.connect(lambda t=tab: self.set_loading(t, True))
        tab.loadFinished.connect(lambda ok, t=tab: self.on_loaded(t, ok))
        tab.loadProgress.connect(lambda p, t=tab: self.on_progress(t, p))
        pg.fullScreenRequested.connect(self.on_fullscreen)
        pg.windowCloseRequested.connect(lambda t=tab: self.close_tab(t))
        pg.linkHovered.connect(self.on_hover)
        pg.proxyAuthenticationRequired.connect(self.on_proxy_auth)
        pg.recentlyAudibleChanged.connect(lambda a, t=tab: (t.row.update_audio(t), self.player.on_audible(t, a)))

        if url and lazy:
            tab.pending = QUrl(url) if isinstance(url, str) else url
            tab.row.title.setText(tab.pending.host() or tab.pending.toString())
        elif url:
            tab.load(QUrl(url) if isinstance(url, str) else url)
        elif not blank:
            tab.setHtml(self.start_page(), START_URL)
        if not background:
            self.tabs.setCurrentRow(self.stack.count() - 1)
            if focus_address:
                self.focus_address()
        if opener is not None and opener.group and self.group_by_id(opener.group) and not opener.closing:
            self.add_to_group(tab, opener.group)
        return tab

    def animate_item(self, item, start, end, ms=200, done=None):
        item.setData(ROLE_ANIM, True)  # layout_islands() keeps its hands off this row until we're done
        anim = QVariantAnimation(self)
        anim.setDuration(ms)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def step(v):
            v = int(v)
            extra = item.data(ROLE_EXTRA) or 0  # a carrier row also owns its island header
            item.setSizeHint(self.item_size(v + extra * v // max(1, self.extent())))
        anim.valueChanged.connect(step)

        def finish():
            item.setData(ROLE_ANIM, False)
            if done:
                done()
            else:
                self.layout_islands()
            anim.deleteLater()
        anim.finished.connect(finish)
        anim.start()

    def close_tab(self, tab):
        i = self.stack.indexOf(tab)
        if i < 0 or tab.closing:
            return
        u = tab.url()
        if u.scheme() in ("http", "https"):
            self.closed.append(u.toString())
            del self.closed[:-30]  # keep only the last 30 closed tabs
        alive = [k for k in range(self.stack.count()) if not self.stack.widget(k).closing]
        if len(alive) <= 1:
            tab.setHtml(self.start_page(), START_URL)
            return
        tab.closing = True
        if self.player.tab is tab:
            self.player.clear()
        if self.cur() is tab:
            others = [k for k in alive if k != i]

            def shown(k):
                return self.tab_visible(k)

            def pick(pool):
                after = [k for k in pool if k > i]
                before = [k for k in pool if k < i]
                return after[0] if after else (before[-1] if before else None)
            same = [k for k in others if tab.group and self.stack.widget(k).group == tab.group and self.tab_visible(k)]
            k = pick(same)
            if k is None:
                k = pick([x for x in others if shown(x)])
            if k is None:
                k = pick(others)
            self.tabs.setCurrentRow(k)
        if tab.group:
            self.rebuild_groups()  # hand the island header to the next tab right away
        self.animate_item(self.tabs.item(i), self.extent(), 0, 170, done=lambda: self._remove_tab(tab))

    def _remove_tab(self, tab):
        i = self.stack.indexOf(tab)
        if i < 0:
            return
        self.tabs.blockSignals(True)
        item = self.tabs.takeItem(i)
        w = self.tabs.itemWidget(item)
        if w:
            w.set_header(None)
            w.deleteLater()
        self.stack.removeWidget(tab)
        self.tabs.setCurrentRow(self.stack.currentIndex())
        self.tabs.blockSignals(False)
        tab.deleteLater()
        self.sync_chrome(self.cur())
        self.update_split_marks()
        self.rebuild_groups()

    def reopen_tab(self):
        if self.closed:
            self.new_tab(self.closed.pop())

    def close_others(self, keep):
        for i in reversed(range(self.stack.count())):
            if self.stack.widget(i) is not keep:
                self.close_tab(self.stack.widget(i))

    def tab_menu(self, pos):
        item = self.tabs.itemAt(pos)
        if not item:
            return
        tab = self.stack.widget(self.tabs.row(item))
        m = QMenu(self)
        m.addAction("Reload", tab.reload)
        m.addAction("Duplicate", lambda: self.new_tab(tab.url()))
        muted = tab.page().isAudioMuted()
        m.addAction("Unmute tab" if muted else "Mute tab", lambda: (
            tab.page().setAudioMuted(not muted), tab.row.update_audio(tab)))
        m.addSeparator()
        gm = m.addMenu("Add to group")
        for g in self.groups:
            gm.addAction("● " + g["name"], lambda _c=False, gid=g["id"]: self.add_to_group(tab, gid))
        gm.addAction("New group…", lambda _c=False: self.new_group(tab))
        if tab.group:
            m.addAction("Remove from group", lambda _c=False: self.remove_from_group(tab))
        sp = self.stack.split or []
        in_split = self.stack.in_split(tab)
        others = [w for w in self.tab_list() if w is not tab and not w.closing and not self.stack.in_split(w)]
        if sp and not in_split and len(sp) < MAX_PANES:
            m.addAction("Add to split view", lambda _c=False: self.add_to_split(tab, sp[-1]))
        if others and (not in_split or len(sp) < MAX_PANES):
            sm = m.addMenu("Add to split view" if in_split else "Split view with")
            for w in others:
                label = (w.title() or w.url().host() or "New Tab")[:40]
                if in_split:
                    sm.addAction(label, lambda _c=False, w=w: self.add_to_split(w, tab))
                else:
                    sm.addAction(label, lambda _c=False, w=w: self.start_split(tab, w, tab))
        if in_split:
            if len(sp) > 2:
                m.addAction("Remove from split view", lambda _c=False: self.remove_from_split(tab))
            m.addAction("Exit split view", lambda _c=False: self.exit_split())
        m.addSeparator()
        host = self.host_of(tab.url())
        real = bool(host) and not self.is_internal(tab.url())
        if real and host in [e["host"] for e in self.essentials]:
            m.addAction("Remove from Essentials", lambda: self.remove_essential(host))
        else:
            act = m.addAction("Add to Essentials" if real else "Add to Essentials (open a website first)",
                              lambda: self.add_essential(tab))
            act.setEnabled(real)
        m.addSeparator()
        m.addAction("Close tab", lambda: self.close_tab(tab))
        m.addAction("Close other tabs", lambda: self.close_others(tab))
        m.exec(self.tabs.viewport().mapToGlobal(pos))

    def on_row_changed(self, row):
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)
            self.tabs.scrollToItem(self.tabs.item(row))
            t = self.cur()
            self.wake_tab(t)
            stop_anim(self.progress, b"value")
            stop_anim(self.prog_fx, b"opacity")
            self.prog_fx.setOpacity(1.0)
            self.progress.setValue(t.prog)
            self.progress.setVisible(t.loading)
            self.sync_chrome(t)
            self.budget_overlay_sync()
            g = self.group_by_id(t.group) if t.group else None
            if g and g["collapsed"]:  # landing on a tab inside a shut island slides it open
                self.set_group_open(g, True)
            else:
                self.apply_group_visibility()

    # ----- memory: sleeping background tabs -----
    # Sleep timings come from the speed mode: tabs untouched for `freeze` seconds are frozen (JS stops, CPU use
    # drops); after `discard` seconds they are discarded (renderer memory is freed, reloads on return).
    def update_speed_btn(self):
        m = SPEED_MODES[self.speed_mode]
        self.btn_speed.set_kind({"eco": "speed_eco", "turbo": "speed_turbo"}.get(self.speed_mode, "speed"))
        tint = {"eco": "#4ade80", "turbo": "#f87171"}.get(self.speed_mode)  # green for Eco, red for Turbo
        self.btn_speed.set_active(self.speed_mode != "normal", tint)
        self.btn_speed.setToolTip("Speed: %s - %s (click to change)" % (m["label"], m["desc"].lower()))

    def show_speed_menu(self):
        menu = QMenu(self)
        for key, m in SPEED_MODES.items():
            icon_kind = {"eco": "speed_eco", "turbo": "speed_turbo"}.get(key, "speed")
            pm = QPixmap(36, 36)
            pm.fill(Qt.GlobalColor.transparent)
            pp = QPainter(pm)
            draw_glyph(pp, icon_kind, QRectF(4, 4, 28, 28), QColor("#b5c6d4"), 2.4)
            pp.end()
            pm.setDevicePixelRatio(2.0)
            a = menu.addAction(QIcon(pm), m["label"])
            a.setCheckable(True)
            a.setChecked(key == self.speed_mode)
            a.triggered.connect(lambda _c, k=key: self.set_speed_mode(k))
        menu.exec(self.btn_speed.mapToGlobal(self.btn_speed.rect().bottomLeft()))

    def set_speed_mode(self, key):
        if key not in SPEED_MODES or key == self.speed_mode:
            return
        old_flags = SPEED_MODES[self.speed_mode]["flags"]
        self.speed_mode = key
        m = SPEED_MODES[key]
        self.settings["speed_mode"] = key
        self.settings["sleep_tabs"] = m["sleep"] is not None  # keeps the menu's sleep toggle in step
        jsave("settings.json", self.settings)
        self.profile.setHttpCacheMaximumSize(m["cache_mb"] * 1024 * 1024)
        if m["sleep"] is None:
            for t in self.tab_list():
                self.wake_tab(t)
        else:
            self._sleep_ticks = 0
            trim_memory()
        self.update_speed_btn()
        needs_restart = sorted(old_flags) != sorted(m["flags"])
        self.toast("%s mode on%s" % (m["label"], " - restart Fjord to apply the process and GPU limits" if needs_restart else ""),
                   4000)

    def wake_tab(self, t):
        if t is None:
            return
        t.last_active = time.time()
        if t.row is not None:
            t.row.set_sleeping(False)
        if t.pending is not None:
            u, t.pending = t.pending, None
            t.load(u)
            return
        pg = t.page()
        if pg.lifecycleState() != QWebEnginePage.LifecycleState.Active:
            pg.setLifecycleState(QWebEnginePage.LifecycleState.Active)
            if self.is_internal(t.url()):  # a frozen new-tab page may leave its background video paused
                pg.runJavaScript("var v=document.getElementById('bgv');if(v&&v.paused){var p=v.play();"
                                 "if(p&&p.catch)p.catch(function(){});}")

    def sleep_tabs(self):
        self._sleep_ticks += 1
        mode = SPEED_MODES[self.speed_mode]
        if mode["trim_ticks"] and self._sleep_ticks % mode["trim_ticks"] == 0:  # every 1-2 minutes (never in turbo)
            trim_memory()
        if mode["sleep"] is None or not self.settings.get("sleep_tabs", True):
            return
        freeze_s, discard_s = mode["sleep"]
        now, cur = time.time(), self.cur()
        for t in self.tab_list():
            if t is cur or t.closing or t.loading or t.pending is not None or self.stack.in_split(t) or self.player.tab is t:
                continue
            pg = t.page()
            if pg.recentlyAudible():
                continue
            internal = self.is_internal(t.url())  # fjord:// pages can't be reloaded once discarded, but they can freeze
            idle = now - t.last_active
            state = pg.lifecycleState()
            if idle >= discard_s and not internal and state != QWebEnginePage.LifecycleState.Discarded:
                pg.setLifecycleState(QWebEnginePage.LifecycleState.Discarded)
                t.row.set_sleeping(True)
            elif idle >= freeze_s and state == QWebEnginePage.LifecycleState.Active:
                pg.setLifecycleState(QWebEnginePage.LifecycleState.Frozen)
                t.row.set_sleeping(True)

    def set_sleep_tabs(self, on):
        self.settings["sleep_tabs"] = on
        jsave("settings.json", self.settings)
        if not on:
            for t in self.tab_list():
                self.wake_tab(t)
        if on and SPEED_MODES[self.speed_mode]["sleep"] is None:
            self.speed_mode = "normal"  # sleeping tabs contradicts Turbo, so drop back to Normal
            self.settings["speed_mode"] = "normal"
            jsave("settings.json", self.settings)
            self.profile.setHttpCacheMaximumSize(SPEED_MODES["normal"]["cache_mb"] * 1024 * 1024)
            self.update_speed_btn()
        self.toast("Background tabs will " + ("sleep to save memory" if on else "stay awake"), 2500)

    def tab_visible(self, k):
        it, t = self.tabs.item(k), self.stack.widget(k)
        if it is None or t is None or it.isHidden():
            return False
        g = self.group_by_id(t.group) if t.group else None
        return not (g and g["collapsed"])

    def cycle(self, d):
        n = self.stack.count()
        r = self.tabs.currentRow()
        for _ in range(n):
            r = (r + d) % n
            if self.tab_visible(r):
                break
        self.tabs.setCurrentRow(r)

    # ----- chrome sync -----
    @staticmethod
    def is_internal(u):
        return u.scheme() in ("fjord", "about") or u.isEmpty()

    def sync_chrome(self, tab):
        if tab is None or tab is not self.cur():
            return
        u = tab.url()
        if not self.addr.hasFocus():
            self.addr.setText("" if self.is_internal(u) else u.toString())
            self.addr.setCursorPosition(0)
        h = tab.history()
        self.btn_back.setEnabled(h.canGoBack())
        self.btn_fwd.setEnabled(h.canGoForward())
        self.btn_reload.set_kind("stop" if tab.loading else "reload")
        self.btn_reload.setToolTip("Stop loading" if tab.loading else "Reload  (Ctrl+R)")
        starred = any(b["url"] == u.toString() for b in self.bookmarks)
        self.btn_star.set_kind("star_on" if starred else "star")
        self.btn_star.setToolTip("Remove bookmark  (Ctrl+D)" if starred else "Bookmark this page  (Ctrl+D)")
        self.setWindowTitle((tab.title() or "New Tab") + " — Fjord")
        self.mark_essentials(u)

    def set_loading(self, tab, loading):
        tab.loading = loading
        self.sync_chrome(tab)
        if tab is not self.cur():
            return
        if loading:
            stop_anim(self.progress, b"value")
            stop_anim(self.prog_fx, b"opacity")
            self.prog_fx.setOpacity(1.0)
            self.progress.setValue(0)
            self.progress.show()
        else:
            animate(self.progress, b"value", self.progress.value(), 100, 160, done=self._fade_progress)

    def _fade_progress(self):
        t = self.cur()
        if t is not None and not t.loading:
            animate(self.prog_fx, b"opacity", 1.0, 0.0, 400,
                    done=lambda: self.progress.hide() if not self.cur().loading else None)

    def on_loaded(self, tab, ok):
        self.set_loading(tab, False)
        u = tab.url()
        if ok and u.scheme() in ("http", "https"):
            us = u.toString()
            if not (self.history and self.history[-1]["url"] == us):
                self.history.append({"url": us, "title": tab.title(), "t": time.time()})
                del self.history[:-3000]
                jsave("history.json", self.history)
                self.refresh_completer()

    def on_progress(self, tab, p):
        tab.prog = p
        if tab is self.cur() and tab.loading:
            animate(self.progress, b"value", self.progress.value(), p, 240)

    def _refresh_hoverbar(self):
        if self._hover_text:
            self.hoverbar.set_content(self._hover_text, self._hover_pm)
        elif self._toast_text:
            self.hoverbar.set_content(self._toast_text)
        else:
            self.hoverbar.hide()

    def on_hover(self, url):
        self._hover_text, self._hover_pm = "", None
        if url and not url.startswith("fjord://"):
            self._hover_text = url
            host = self.host_of(QUrl(url))
            if host and re.fullmatch(r"[\w.\-]+", host):
                ip = ICON_DIR / (host + ".png")  # the site's logo, if we have saved it
                if ip.exists():
                    self._hover_pm = QPixmap(str(ip))
        self._refresh_hoverbar()

    def toast(self, msg, ms=5000):
        self._toast_text = msg
        self._toast_timer.start(ms)
        self._refresh_hoverbar()

    def _end_toast(self):
        self._toast_text = ""
        self._refresh_hoverbar()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "hoverbar"):
            self.hoverbar.place()
        if hasattr(self, "scratch"):
            self.scratch.place()
        if hasattr(self, "budget_ov") and self.budget_ov.isVisible():
            self.budget_ov.place()
        self.place_grips()

    # ----- actions -----
    def navigate(self):
        url = to_url(self.addr.text(), self.engine)
        if url and self.cur():
            self.cur().load(url)
            self.cur().setFocus()

    def focus_address(self):
        self.addr.setFocus()
        self.addr.selectAll()

    def reload_or_stop(self):
        t = self.cur()
        t.stop() if t.loading else t.reload()

    def zoom(self, d):
        t = self.cur()
        t.setZoomFactor(max(0.25, min(5.0, t.zoomFactor() + d)))

    def extent(self):
        return 180 if self.horiz else 40

    def item_size(self, v):
        return QSize(v, 38) if self.horiz else QSize(0, v)

    def side_width(self):
        return 64 if self.compact else self.sidebar_w

    def set_side_width(self, w, save=False):
        cap = max(SIDE_MIN_W, min(SIDE_MAX_W, self.width() // 2))
        self.sidebar_w = max(SIDE_MIN_W, min(cap, int(w)))
        if not self.compact and not self.horiz and self.sidebar_wanted:
            self.side.setFixedWidth(self.sidebar_w)
        if save:
            self.save_side_width()

    def save_side_width(self):
        self.settings["sidebar_w"] = self.sidebar_w
        jsave("settings.json", self.settings)

    def place_grip(self):
        drawer = getattr(self, "scratch", None)
        if drawer is not None:
            drawer.place()  # the Scratchpad drawer hugs the sidebar's right edge
        g = self.grip
        if g is None:
            return
        if not self.side.isVisible() or self.horiz or self.compact:
            g.hide()
            return
        r = self.side.geometry()  # grip straddles the sidebar's right edge: 4px inside, 8px across the gap
        g.setGeometry(r.right() + 1 - 4, r.top() + 14, 12, max(0, r.height() - 28))
        g.show()
        g.raise_()

    def set_visualizer(self, on):
        self.settings["visualizer"] = bool(on)
        jsave("settings.json", self.settings)
        self.player.set_viz(bool(on))

    def toggle_sidebar(self):
        if not self.horiz:
            self.set_sidebar(not self.sidebar_wanted)

    def set_sidebar(self, show):
        self.sidebar_wanted = show
        if self.horiz:
            return
        self._hide_timer.stop()
        self.side.setMinimumWidth(0)
        start = self.side.width() if self.side.isVisible() else 0
        if show:
            self.edge.hide()
            if not self.side.isVisible():
                self.side.setMaximumWidth(0)
                self.side.show()
            animate(self.side, b"maximumWidth", start, self.side_width(), 280,
                    done=lambda: self.side.setFixedWidth(self.side_width()))
        else:
            animate(self.side, b"maximumWidth", start, 0, 240, done=self._sidebar_hidden)

    def _sidebar_hidden(self):
        self.side.hide()
        self.edge.setVisible(bool(self.settings.get("autohide")) and not self.horiz)

    def sidebar_leave(self):
        if self.settings.get("autohide") and not self.horiz and self.sidebar_wanted:
            self._hide_timer.start(450)

    def _autohide_check(self):
        if not self.settings.get("autohide") or self.horiz or not self.sidebar_wanted:
            return
        if QApplication.activePopupWidget() is not None:  # a context menu is open
            self._hide_timer.start(500)
            return
        if self.grip.dragging or self.grip.underMouse():  # resizing the sidebar
            self._hide_timer.start(450)
            return
        drawer = self.scratch
        if drawer.isVisible() and drawer.geometry().contains(self.centralWidget().mapFromGlobal(QCursor.pos())):
            self._hide_timer.start(450)  # the pointer is on the Scratchpad drawer
            return
        if not self.side.rect().contains(self.side.mapFromGlobal(QCursor.pos())):
            self.set_sidebar(False)

    def apply_layout(self):
        st = self.settings
        self.horiz = st.get("layout") == "horizontal"
        self.compact = bool(st.get("compact")) and not self.horiz
        for w in (self.ess_wrap, self.divider, self.group_wrap, self.newtab, self.tabs, self.scratch_btn, self.player,
                  self.strip_fill):
            self.side_lay.removeWidget(w)
            self.strip_lay.removeWidget(w)
        self.tb_lay.removeWidget(self.scratch_btn)
        if self.horiz:
            for w, k in ((self.ess_wrap, 0), (self.group_wrap, 0), (self.tabs, 0), (self.newtab, 0), (self.strip_fill, 1)):
                self.strip_lay.addWidget(w, k)
            self.tabs.setFlow(QListView.Flow.LeftToRight)
            self.tabs.setWrapping(False)
            self.tabs.setSpacing(0)
            self.tabs.setFixedHeight(38)
            self.tabs.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            self.newtab.setText("＋")
            self.newtab.setFixedSize(32, 30)
            self.newtab.setProperty("compact", True)  # flat Chrome-style + button
            self.newtab.style().unpolish(self.newtab)
            self.newtab.style().polish(self.newtab)
            self.strip.show()
            self.side.hide()
            self.edge.hide()
        else:
            for w, k in ((self.ess_wrap, 0), (self.divider, 0), (self.newtab, 0), (self.group_wrap, 0), (self.tabs, 1)):
                self.side_lay.addWidget(w, k)
            self.side_lay.addWidget(self.scratch_btn, 0)
            self.side_lay.addWidget(self.player, 0)  # media player pinned to the very bottom
            self.tabs.setFlow(QListView.Flow.TopToBottom)
            self.tabs.setSpacing(0)
            self.tabs.setMinimumHeight(0)
            self.tabs.setMaximumHeight(16777215)
            self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.newtab.setMinimumSize(0, 0)
            self.newtab.setMaximumSize(16777215, 16777215)
            self.newtab.setText("＋" if self.compact else "＋  New tab")
            self.newtab.setProperty("compact", self.compact)
            self.newtab.style().unpolish(self.newtab)
            self.newtab.style().polish(self.newtab)
            self.strip.hide()
            m = 8 if self.compact else 10
            self.side_lay.setContentsMargins(m, 10, m, 10)
            tw = self.side_width()
            if self.sidebar_wanted:
                if self.side.isVisible():
                    self.side.setMinimumWidth(0)
                    animate(self.side, b"maximumWidth", self.side.width(), tw, 260,
                            done=lambda: self.side.setFixedWidth(self.side_width()))
                else:
                    self.side.setFixedWidth(tw)
                    self.side.show()
            else:
                self.side.setFixedWidth(tw)
                self.side.hide()
                self.edge.setVisible(bool(st.get("autohide")))
        self.tabs.setProperty("horiz", self.horiz)
        self.tabs.style().unpolish(self.tabs)
        self.tabs.style().polish(self.tabs)
        self.tabs.updateGeometry()
        self.player.set_compact(self.compact)
        self.scratch_btn.set_mode(self.compact, self.horiz)
        self.scratch_btn.show()
        self.tb_ed.apply()  # sidebar button / scratchpad icon come and go with the layout
        self.place_grip()
        for i in range(self.tabs.count()):
            item = self.tabs.item(i)
            w = self.tabs.itemWidget(item)
            if w:
                w.set_compact(self.compact)
            item.setSizeHint(self.item_size(self.extent()))
        self.rebuild_essentials()
        self.rebuild_groups()

    def apply_setting(self, k, v):
        st = self.settings
        if k == "adblock":
            self.set_adblock(v == "1")
        elif k == "vpn":
            self.toggle_vpn()
        elif k == "preset" and v in ("tor", "local"):
            st.update(proxy_type="socks5", proxy_host="127.0.0.1", proxy_port="9050" if v == "tor" else "1080",
                      proxy_user="", proxy_pw="")
        elif k in ("compact", "autohide", "ess_startup", "visualizer"):
            st[k] = v == "1"
        elif k == "layout" and v in ("vertical", "horizontal"):
            st[k] = v
        elif k == "winbtns" and v in ("windows", "mac"):
            st[k] = v
        elif k == "ui_style" and v in dict(UI_MODES):
            self.set_ui_style(v, refresh_settings=False)  # the settings page is redrawn below
        elif k in TUNE and v.lstrip("-").isdecimal():
            _n, lo, hi, _d = TUNE[k]
            st[k] = max(lo, min(hi, int(v)))
            self.apply_ui_tuning()
        elif k == "ui_reset":
            for key in TUNE:
                st.pop(key, None)
            self.apply_ui_tuning()
        elif k == "engine" and v in ENGINES:
            self.set_engine(v)
        elif k == "font" and v in installed_fonts():
            self.set_font(v)
        elif k == "greet_mode" and v in ("default", "custom", "quote"):
            self.update_greeting(mode=v)
        elif k == "greet_text":
            text = v.strip()[:GREETING_MAX]
            self.update_greeting(text=text, mode="custom" if text else "default")
        elif k == "greet_pick":
            self.update_greeting(mode="quote", pick=int(v) if v.isdecimal() and int(v) < len(QUOTES) else None)
        elif k == "tour":
            QTimer.singleShot(0, self.start_tour)
        elif k == "accent_reset":
            self.set_accent(None)
        jsave("settings.json", st)
        if k in ("compact", "layout"):
            self.apply_layout()
        elif k == "winbtns":
            self.winctl.set_mode(effective_winbtns(st))
        elif k == "visualizer":
            self.player.set_viz(bool(st["visualizer"]))
        elif k == "autohide":
            if st["autohide"]:
                self.edge.hide()
            else:
                self.set_sidebar(True)
        QTimer.singleShot(0, lambda: self.open_settings(keep_scroll=True))

    # ----- tab groups -----
    def tab_list(self):
        return [self.stack.widget(i) for i in range(self.stack.count())]

    def group_by_id(self, gid):
        return next((g for g in self.groups if g["id"] == gid), None)

    def group_color(self, tab):
        g = self.group_by_id(tab.group) if tab.group else None
        return g["color"] if g else None

    def island_hdr(self):
        """Space an island header takes at the start of its first tab's row."""
        if self.horiz:
            return 96
        return 26 if self.compact else 30

    def rebuild_groups(self):
        for t in self.tab_list():
            if t.row is not None:
                t.row.set_header(None)
        for c in self.chips.values():
            try:
                c.deleteLater()
            except RuntimeError:
                pass
        self.chips = {}
        while self.group_lay.count():
            w = self.group_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.group_lay.setDirection(QBoxLayout.Direction.LeftToRight if self.horiz
                                    else QBoxLayout.Direction.TopToBottom)
        tabs = self.tab_list()
        live = {t.group for t in tabs if t.group and not t.closing}
        for g in self.groups:
            if g["id"] in live:
                g["seen"] = True
        # a group that had tabs and lost them all disappears; a freshly made empty group stays
        self.groups = [g for g in self.groups if g["id"] in live or not g.get("seen")]
        for gid in [k for k in self.ganim if k not in live]:
            old = self.ganim.pop(gid)
            old.stop()
            old.deleteLater()
        for gid in [k for k in self.gprog if k not in live]:
            self.gprog.pop(gid)

        def first_pos(g):
            return next((k for k, t in enumerate(tabs) if t.group == g["id"] and not t.closing), 10 ** 6)
        for g in sorted(self.groups, key=first_pos):
            n = sum(1 for t in tabs if t.group == g["id"] and not t.closing)
            chip = GroupChip(g["id"], self.drop_on_group)
            chip.configure(g["name"], g["color"], n, self.compact, self.horiz)
            chip.clicked.connect(lambda g=g: self.toggle_group(g))
            chip.customContextMenuRequested.connect(lambda _p, g=g: self.group_menu(g))
            self.chips[g["id"]] = chip
            if n == 0:  # empty island: its header lives up in the group bar until a tab joins
                if self.horiz:
                    chip.setFixedWidth(self.island_hdr())
                else:
                    chip.setFixedHeight(self.island_hdr())
                self.group_lay.addWidget(chip)
        nb = NewGroupButton(10)
        nb.setObjectName("newgroup")
        nb.setText("▤" if self.compact else "＋ Group")
        nb.setToolTip("New empty tab group (then drag tabs onto its header, or right-click a tab > Add to group)")
        nb.setCursor(Qt.CursorShape.PointingHandCursor)
        nb.clicked.connect(lambda _c=False: self.new_group())
        self.group_lay.addWidget(nb)
        self.refresh_group_rows()
        self.queue_save()

    def refresh_group_rows(self):
        for t in self.tab_list():
            if t.row is not None:
                t.row.set_group(self.group_color(t))
        self.apply_group_visibility()

    def apply_group_visibility(self):
        self.layout_islands()

    def layout_islands(self):
        """Size every row for the current island state and place each island's header.
        Called every animation frame while an island slides open or shut."""
        tabs = self.tab_list()
        ext, hdr, horiz = self.extent(), self.island_hdr(), self.horiz
        first, members = {}, {}
        for i, t in enumerate(tabs):
            if t.closing or not t.group or self.group_by_id(t.group) is None:
                continue
            first.setdefault(t.group, i)
            members.setdefault(t.group, []).append(i)
        for i, t in enumerate(tabs):
            item = self.tabs.item(i)
            if item is None or t.closing:
                continue
            g = self.group_by_id(t.group) if t.group in first else None
            if g is None:
                extra, hide, size, grouped = 0, False, self.item_size(ext), 0
            else:
                p = self.gprog.get(g["id"], 0.0 if g["collapsed"] else 1.0)
                extra = hdr if first[g["id"]] == i else 0
                h = int(round(ext * p))
                hide, size, grouped = (extra == 0 and h <= 0), self.item_size(h + extra), 1
            if item.data(ROLE_EXTRA) != extra:
                item.setData(ROLE_EXTRA, extra)
            if item.data(ROLE_GROUPED) != grouped:
                item.setData(ROLE_GROUPED, grouped)
            if item.isHidden() != hide:
                item.setHidden(hide)
            if not item.data(ROLE_ANIM) and item.sizeHint() != size:
                item.setSizeHint(size)
            row = t.row
            if row is not None:
                row.set_grouped(bool(grouped))
                want = self.chips.get(g["id"]) if (g is not None and extra) else None
                if row.hdr_chip is not want or (want is not None and (row.hdr_extra != hdr or row.hdr_horiz != horiz)):
                    row.set_header(want, hdr, horiz)
        cur = self.cur()
        islands = []
        for g in self.groups:
            gid = g["id"]
            if gid not in members:
                continue
            chip = self.chips.get(gid)
            if chip is not None:
                chip.set_open(self.gprog.get(gid, 0.0 if g["collapsed"] else 1.0))
                chip.set_active(cur is not None and cur.group == gid)
            islands.append((g["color"], members[gid]))
        self.tabs.islands = islands
        self.tabs.viewport().update()

    def set_group_open(self, g, open_, animate=True):
        """Slide an island's tabs out (open) or back in (shut)."""
        gid = g["id"]
        was = self.gprog.get(gid, 0.0 if g["collapsed"] else 1.0)
        target = 1.0 if open_ else 0.0
        g["collapsed"] = not open_
        old = self.ganim.pop(gid, None)
        if old is not None:
            old.stop()
            old.deleteLater()
        if not animate or abs(target - was) < 0.001:
            self.gprog.pop(gid, None)
            self.layout_islands()
            return
        anim = QVariantAnimation(self)
        anim.setDuration(max(90, int(240 * abs(target - was))))
        anim.setStartValue(was)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.ganim[gid] = anim
        self.gprog[gid] = was

        def step(v):
            self.gprog[gid] = float(v)
            self.layout_islands()

        def finish():
            if self.ganim.get(gid) is anim:
                self.ganim.pop(gid, None)
                self.gprog.pop(gid, None)
                self.layout_islands()
                if open_ and self.tabs.currentRow() >= 0:
                    self.tabs.scrollToItem(self.tabs.item(self.tabs.currentRow()))
            anim.deleteLater()
        anim.valueChanged.connect(step)
        anim.finished.connect(finish)
        anim.start()

    def toggle_group(self, g):
        self.set_group_open(g, g["collapsed"])

    def new_group(self, tab=None):
        name, ok = QInputDialog.getText(self, "New group", "Group name:", text="Group %d" % (len(self.groups) + 1))
        if not ok or not name.strip():
            return
        g = {"id": "g%d" % int(time.time() * 1000), "name": name.strip()[:24],
             "color": GROUP_COLORS[len(self.groups) % len(GROUP_COLORS)][1], "collapsed": False}
        self.groups.append(g)
        if tab is not None and not tab.closing:
            self.add_to_group(tab, g["id"])
        else:
            self.rebuild_groups()

    def add_to_group(self, tab, gid):
        if tab.closing or self.group_by_id(gid) is None:
            return
        if tab.group == gid:
            return
        tab.group = gid
        i = self.stack.indexOf(tab)
        members = [k for k, t in enumerate(self.tab_list()) if t.group == gid and t is not tab and not t.closing]
        if members:  # keep a group's tabs next to each other
            last = max(members)
            self.move_tab(tab, last if i < last else last + 1)
        self.rebuild_groups()
        g = self.group_by_id(gid)
        if g and g["collapsed"]:  # a tab joining a shut island slides it open
            self.set_group_open(g, True)

    def remove_from_group(self, tab):
        gid = tab.group
        tab.group = None
        i = self.stack.indexOf(tab)
        members = [k for k, t in enumerate(self.tab_list()) if t.group == gid and not t.closing]
        if members and 0 <= i < max(members):  # step out of the group's block so it stays contiguous
            self.move_tab(tab, max(members))
        self.rebuild_groups()

    def drop_on_group(self, src_row, gid):
        tab = self.stack.widget(src_row)
        if tab is not None:
            self.add_to_group(tab, gid)

    def rename_group(self, g):
        name, ok = QInputDialog.getText(self, "Rename group", "Group name:", text=g["name"])
        if ok and name.strip():
            g["name"] = name.strip()[:24]
            self.rebuild_groups()

    def set_group_color(self, g, color):
        g["color"] = color
        self.rebuild_groups()

    def delete_group(self, g, close_tabs):
        members = [t for t in self.tab_list() if t.group == g["id"]]
        self.groups.remove(g)
        for t in members:
            t.group = None
            if close_tabs:
                self.close_tab(t)
        self.rebuild_groups()

    def group_menu(self, g):
        m = QMenu(self)
        m.addAction("Expand" if g["collapsed"] else "Collapse", lambda: self.toggle_group(g))
        m.addAction("Rename…", lambda: self.rename_group(g))
        cm = m.addMenu("Color")
        for name, col in GROUP_COLORS:
            a = cm.addAction(name)
            a.triggered.connect(lambda _c=False, col=col: self.set_group_color(g, col))
        m.addSeparator()
        m.addAction("Ungroup", lambda: self.delete_group(g, False))
        m.addAction("Close group", lambda: self.delete_group(g, True))
        m.exec(QCursor.pos())

    def move_tab(self, tab, to):
        """Move a tab (list row + view) to index `to`, keeping the current tab selected."""
        i = self.stack.indexOf(tab)
        if i < 0 or i == to:
            return
        cur = self.cur()
        if tab.row is not None:
            tab.row.set_header(None)  # keep the island header alive when the old row widget is dropped
        self.tabs.blockSignals(True)
        self.tabs.takeItem(i)  # also drops the old row widget
        self.stack.reorder(i, to)
        item = QListWidgetItem()
        item.setSizeHint(self.item_size(self.extent()))
        self.tabs.insertItem(to, item)
        row = TabRow(tab, self.close_tab)
        tab.row = row
        self.tabs.setItemWidget(item, row)
        row.title.setText(tab.title() or "New Tab")
        row.setToolTip(tab.title())
        if not tab.icon().isNull():
            row.set_icon(tab.icon())
        row.set_compact(self.compact)
        row.update_audio(tab)
        row.set_sleeping(tab.page().lifecycleState() != QWebEnginePage.LifecycleState.Active)
        self.tabs.setCurrentRow(self.stack.indexOf(cur))
        self.tabs.blockSignals(False)
        self.update_split_marks()

    # ----- split view -----
    def start_split_rows(self, src, dst):
        """A tab was dropped on another tab. Dragging into an existing split adds a pane to it."""
        a, b = self.stack.widget(src), self.stack.widget(dst)
        if a is None or b is None or a is b:
            return
        in_a, in_b = self.stack.in_split(a), self.stack.in_split(b)
        if in_b:
            self.add_to_split(a, b)  # dragged tab joins the split, right next to the tab it was dropped on
        elif in_a:
            self.add_to_split(b, a)
        else:
            self.start_split(b, a)  # new split: target on the left, dragged tab on the right

    def start_split(self, left, right, focus=None):
        if left is right or left.closing or right.closing:
            return
        self.stack.set_split([left, right])
        self.tabs.setCurrentRow(self.stack.indexOf(focus or right))
        self.stack.relayout()
        self.update_split_marks()
        self.sync_chrome(self.cur())
        self.toast("Split view: drag a third tab onto one of these to add it. Drag dividers to resize.", 4500)

    def add_to_split(self, new, anchor, focus=None):
        """Put `new` into the current split, directly right of `anchor` (which must already be in it)."""
        sp = self.stack.split
        if not sp:
            return self.start_split(anchor, new, focus or new)
        if new is anchor or new.closing or not self.stack.in_split(anchor):
            return
        sp, wts = list(sp), list(self.stack.weights)
        if self.stack.in_split(new):  # already a pane: just move it next to the anchor
            k = next(j for j, t in enumerate(sp) if t is new)
            sp.pop(k)
            wt = wts.pop(k)
        else:
            if len(sp) >= MAX_PANES:
                self.toast("Split view holds up to %d tabs" % MAX_PANES)
                return
            wt = None
        a = next(j for j, t in enumerate(sp) if t is anchor)
        sp.insert(a + 1, new)
        if wt is None:  # the new pane gets an equal share, the others shrink proportionally
            share = 1.0 / len(sp)
            wts = [x * (1 - share) for x in wts]
            wts.insert(a + 1, share)
        else:
            wts.insert(a + 1, wt)
        self.stack.set_split(sp, wts)
        self.tabs.setCurrentRow(self.stack.indexOf(focus or new))
        self.stack.relayout()
        self.update_split_marks()
        self.sync_chrome(self.cur())
        self.toast("Split view: %d tabs" % len(sp), 2500)

    def remove_from_split(self, tab):
        sp = self.stack.split
        if not sp or not self.stack.in_split(tab):
            return
        k = next(j for j, t in enumerate(sp) if t is tab)
        rest = [t for t in sp if t is not tab]
        wts = [x for j, x in enumerate(self.stack.weights) if j != k]
        tot = sum(wts) or 1.0
        self.stack.set_split(rest, [x / tot for x in wts])
        self.update_split_marks()
        self.sync_chrome(self.cur())

    def exit_split(self):
        self.stack.set_split([])
        self.update_split_marks()
        self.sync_chrome(self.cur())

    def update_split_marks(self):
        for t in self.tab_list():
            if t.row is not None:
                t.row.set_split(self.stack.in_split(t))

    def on_focus_changed(self, _old, new):
        """Clicking inside a split pane makes that pane the active tab."""
        if not self.stack.split:
            return
        w = new
        while w is not None:
            if self.stack.in_split(w):
                if w is not self.cur():
                    self.tabs.setCurrentRow(self.stack.indexOf(w))
                return
            w = w.parentWidget()

    def on_icons_ready(self):
        self.update_engine_btn()
        t = self.cur()
        if t is not None and t.url().scheme() == "fjord" and t.url().host() == "settings":
            self.open_settings(keep_scroll=True)

    # ----- ad blocking & VPN -----
    def refresh_adblock_pages(self):
        self.adblock.install_global(self.profile)
        for i in range(self.stack.count()):
            self.adblock.install(self.stack.widget(i).page())

    def on_adblock_ready(self):
        self.refresh_adblock_pages()
        QTimer.singleShot(1500, trim_memory)  # the filter lists were just parsed: give the freed memory back
        t = self.cur()
        if t is not None and t.url().scheme() == "fjord" and t.url().host() == "settings":
            self.open_settings(keep_scroll=True)

    def _reload_if_site(self):
        t = self.cur()
        if t is not None and not self.is_internal(t.url()):
            t.reload()

    def set_adblock(self, on):
        self.adblock.enabled = on
        self.settings["adblock"] = on
        jsave("settings.json", self.settings)
        self.refresh_adblock_pages()
        self.toast("Ad blocking " + ("on" if on else "off"), 2500)
        self._reload_if_site()

    def pause_site(self, host, pause):
        if not host:
            return
        (self.adblock.allow.add if pause else self.adblock.allow.discard)(host)
        self.settings["adblock_allow"] = sorted(self.adblock.allow)
        jsave("settings.json", self.settings)
        self.refresh_adblock_pages()
        self._reload_if_site()

    def update_vpn_btn(self):
        on = bool(self.settings.get("vpn"))
        self.btn_vpn.set_kind("shield_on" if on else "shield")
        self.btn_vpn.set_active(on)
        self.btn_vpn.setToolTip("Proxy/VPN is ON - click to turn off" if on else "Proxy/VPN is off - click to turn on")

    def on_proxy_auth(self, _url, auth, _host):
        if self.settings.get("proxy_user"):
            auth.setUser(self.settings["proxy_user"])
            auth.setPassword(self.settings.get("proxy_pw", ""))

    def toggle_vpn(self):
        st = self.settings
        if not (st.get("proxy_host") and st.get("proxy_port")):
            self.toast("Set up a proxy first")
            self.open_settings()
            return
        on = not st.get("vpn", False)
        ask = QMessageBox.question(
            self, "Fjord", ("Turn the VPN/proxy ON" if on else "Turn the VPN/proxy OFF")
            + "?\n\nAir will restart and restore your tabs.")
        if ask != QMessageBox.StandardButton.Yes:
            return
        st["vpn"] = on
        jsave("settings.json", st)
        self.restart()

    def restart(self):
        code = "import time,subprocess,sys;time.sleep(1.5);subprocess.Popen(sys.argv[1:])"
        self.close()  # saves the session
        QProcess.startDetached(sys.executable, ["-c", code, sys.executable] + sys.argv)

    def open_settings(self, keep_scroll=False):
        t = self.cur()
        page = settings_html(self)
        if keep_scroll and t.url().scheme() == "fjord" and t.url().host() == "settings":
            def go(y):
                def restore(_ok):
                    t.loadFinished.disconnect(restore)
                    t.page().runJavaScript("window.scrollTo(0,%d)" % int(y or 0))
                t.loadFinished.connect(restore)
                t.setHtml(page, QUrl("fjord://settings"))
            t.page().runJavaScript("window.scrollY", go)
        else:
            self.show_page("settings", page)

    def open_essentials_bg(self):
        if not self.settings.get("ess_startup", True):
            return
        have = set()
        for i in range(self.stack.count()):
            w = self.stack.widget(i)
            have.add(self.host_of(w.url() if not w.url().isEmpty() else w.page().requestedUrl()))
        for e in self.essentials:
            if e["host"] not in have:
                self.new_tab(e["url"], background=True)

    def open_find(self):
        if not self.find.isVisible():
            self.find.setMaximumHeight(0)
            self.find.show()
            animate(self.find, b"maximumHeight", 0, 38, 200,
                    done=lambda: self.find.setMaximumHeight(16777215))
        self.find.setFocus()
        self.find.selectAll()

    def close_find(self):
        animate(self.find, b"maximumHeight", self.find.height(), 0, 160,
                done=lambda: (self.find.hide(), self.find.setMaximumHeight(16777215)))
        self.cur().findText("")
        self.cur().setFocus()

    def on_fullscreen(self, req):
        req.accept()
        on = req.toggleOn()
        self.side.setVisible(self.sidebar_wanted and not on and not self.horiz)
        self.scratch.setVisible(bool(self.scratch.want and not on))  # page fullscreen: tuck the drawer away
        self.strip.setVisible(self.horiz and not on)
        self.edge.setVisible(not on and bool(self.settings.get("autohide")) and not self.sidebar_wanted and not self.horiz)
        if on:
            self.tb_ed.stop()
        self.toolbar.setVisible(not on)
        m = 0 if on else frame_margin()
        self.centralWidget().layout().setContentsMargins(m, m, m, m)
        self.showFullScreen() if on else self.showNormal()

    def on_download(self, d):
        folder = Path.home() / "Downloads"
        folder.mkdir(exist_ok=True)
        d.setDownloadDirectory(str(folder))
        d.accept()
        name = d.downloadFileName()
        self.toast(f"Downloading {name}…")
        d.isFinishedChanged.connect(lambda: self.toast(f"Downloaded {name}"))
        if name.lower().endswith((".xpi", ".crx")):  # an extension: offer to add it once it has arrived
            asked = []

            def offer():
                if d.isFinished() and not asked and d.state() == d.DownloadState.DownloadCompleted:
                    asked.append(1)
                    self.offer_extension(str(Path(d.downloadDirectory()) / d.downloadFileName()), name)
            d.isFinishedChanged.connect(offer)

    def print_pdf(self):
        t = self.cur()
        folder = Path.home() / "Downloads"
        folder.mkdir(exist_ok=True)
        path = folder / (re.sub(r"[^\w\-]+", "_", t.title() or "page")[:50] + ".pdf")
        t.page().printToPdf(str(path))
        self.toast(f"Saved PDF → {path}")

    # ----- bookmarks / history -----
    def refresh_completer(self):
        urls = [b["url"] for b in self.bookmarks]
        seen = set(urls)
        for h in reversed(self.history[-600:]):
            if h["url"] not in seen:
                seen.add(h["url"])
                urls.append(h["url"])
        self.completer.model().setStringList(urls)

    def toggle_bookmark(self):
        t = self.cur()
        u = t.url()
        if self.is_internal(u):
            return
        us = u.toString()
        if any(b["url"] == us for b in self.bookmarks):
            self.bookmarks = [b for b in self.bookmarks if b["url"] != us]
            self.toast("Bookmark removed", 2500)
        else:
            self.bookmarks.append({"url": us, "title": t.title() or us})
            self.toast("Bookmarked ★", 2500)
        jsave("bookmarks.json", self.bookmarks)
        self.refresh_completer()
        self.sync_chrome(t)

    def show_page(self, name, page_html):
        t = self.cur()
        if not self.is_internal(t.url()):
            t = self.new_tab(blank=True)
        t.setHtml(page_html, QUrl(f"fjord://{name}"))

    def open_history(self):
        e = html.escape
        rows = "".join(
            f'<div class=r><a href="{e(h["url"], True)}"><b>{e(h["title"] or h["url"])}</b>'
            f'<small>{e(h["url"])}</small></a></div>' for h in reversed(self.history[-300:]))
        self.show_page("history", list_page("History", rows, '<a href="fjord://clear-history">Clear all</a>'))

    def open_bookmarks(self):
        e = html.escape
        groups = {}
        for b in self.bookmarks:  # grouped by folder, in the order each folder first appears; unfiled ones come first
            groups.setdefault(b.get("folder") or "", []).append(b)
        rows = ""
        for folder in sorted(groups, key=lambda f: f != ""):
            if folder or len(groups) > 1:
                rows += '<div class=fh>%s</div>' % e(folder or "Bookmarks")
            for b in groups[folder]:
                rows += (f'<div class=r><a href="{e(b["url"], True)}"><b>{e(b["title"])}</b><small>{e(b["url"])}</small></a>'
                         f'<a class=x href="fjord://remove-bookmark?u={quote(b["url"], safe="")}">\u2715</a></div>')
        if len(self.bookmarks) > 8:
            rows = ('<input id=q placeholder="Search bookmarks\u2026" autocomplete=off style="width:100%;box-sizing:border-box;padding:11px 16px;'
                    'border-radius:99px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.06);color:inherit;font:inherit;'
                    'outline:none;margin:4px 0 8px">' + rows
                    + "<script>var q=document.getElementById('q');q.addEventListener('input',function(){var v=q.value.toLowerCase();"
                      "document.querySelectorAll('.r').forEach(function(r){r.style.display=r.textContent.toLowerCase().indexOf(v)<0?'none':'';});"
                      "document.querySelectorAll('.fh').forEach(function(h){var n=h.nextElementSibling,any=false;"
                      "while(n&&!n.classList.contains('fh')){if(n.style.display!=='none')any=true;n=n.nextElementSibling;}"
                      "h.style.display=any?'':'none';});});</script>")
        self.show_page("bookmarks", list_page("Bookmarks", rows, '<a href="fjord://import-open">Import</a>'))

    # ----- importing from other browsers -----
    def open_import(self):
        self._import_sources = detect_import_sources()
        self.show_page("import", import_html(self._import_sources))

    def run_import(self, src, with_history):
        if self._import_busy:
            return
        self._import_busy = True
        self.toast("Importing from %s\u2026" % src["name"], 60000)
        self.importer.run(src, with_history)

    def import_from_file(self):
        path, _f = QFileDialog.getOpenFileName(self, "Import bookmarks", str(Path.home()),
                                               "Bookmarks files (*.html *.htm);;All files (*)")
        if path:
            self.run_import({"kind": "html", "path": path, "name": Path(path).name}, False)

    def on_import_failed(self, msg):
        self._import_busy = False
        if self._import_hook:
            self._import_hook(False, msg)
            return
        self.toast(msg, 9000)

    def on_import_done(self, res):
        self._import_busy = False
        have = {b["url"] for b in self.bookmarks}
        added = 0
        for b in res["bookmarks"]:
            if b["url"] not in have:
                have.add(b["url"])
                self.bookmarks.append(b)
                added += 1
        seen = {h["url"] for h in self.history}
        new_hist = [h for h in res["history"] if h["url"] not in seen]
        if new_hist:
            self.history = sorted(self.history + new_hist, key=lambda h: h.get("t", 0))[-3000:]
            jsave("history.json", self.history)
        if added:
            jsave("bookmarks.json", self.bookmarks)
        self.refresh_completer()
        parts = []
        if added:
            parts.append("%d bookmark%s" % (added, "" if added == 1 else "s"))
        if new_hist:
            parts.append("%d history entries" % len(new_hist))
        msg = ("Imported %s from %s" % (" and ".join(parts), res["name"])) if parts else "Nothing new to import from %s" % res["name"]
        if res["notes"]:
            msg += ". " + " ".join(res["notes"])
        if self._import_hook:  # the welcome tour is running this import and reports the result itself
            self._import_hook(True, msg)
            return
        self.toast(msg, 9000)
        if added:
            self.open_bookmarks()

    def internal_action(self, url):
        if url.host() == "search":
            u = to_url(parse_qs(url.query()).get("q", [""])[0], self.engine)
            if u:
                self.cur().load(u)
        elif url.host() == "set":
            q = parse_qs(url.query(QUrl.ComponentFormattingOption.FullyEncoded))
            self.apply_setting(q.get("k", [""])[0], q.get("v", [""])[0])
        elif url.host() == "vpn":
            q = parse_qs(url.query())
            g = lambda k: q.get(k, [""])[0].strip()
            host, port = g("host"), g("port")
            if (re.fullmatch(r"[\w.\-:]+", host or "!") and port.isdigit() and 0 < int(port) < 65536
                    and g("type") in ("socks5", "http", "https")):
                st = self.settings
                st.update(proxy_type=g("type"), proxy_host=host, proxy_port=port, proxy_user=g("user"))
                if g("pw") or not g("user"):
                    st["proxy_pw"] = g("pw")
                jsave("settings.json", st)
                self.toast("Proxy saved" + (" - turn the VPN off and on to apply it" if st.get("vpn") else ""), 4000)
            else:
                self.toast("Enter a valid host and port")
            self.open_settings(keep_scroll=True)
        elif url.host() == "adblock-update":
            self.adblock.load_async(force=True)
            self.toast("Updating filter lists...", 4000)
            self.open_settings(keep_scroll=True)
        elif url.host() == "allow-remove":
            self.pause_site(parse_qs(url.query()).get("h", [""])[0], False)
            self.open_settings(keep_scroll=True)
        elif url.host() == "ess-add":
            try:
                self.add_essential(self.stack.widget(int(parse_qs(url.query()).get("i", ["-1"])[0])))
            except (ValueError, AttributeError):
                pass
            self.open_settings(keep_scroll=True)
        elif url.host() == "ess-remove":
            self.remove_essential(parse_qs(url.query()).get("h", [""])[0])
            self.open_settings(keep_scroll=True)
        elif url.host() == "bg":
            self.handle_bg(parse_qs(url.query()))
        elif url.host() == "top-add":
            self.add_top_site()
        elif url.host() == "top-remove":
            self.remove_top_site(parse_qs(url.query()).get("h", [""])[0])
        elif url.host().startswith("ext-"):
            self.ext_action(url)
        elif url.host() in ("budget-add", "budget-set", "budget-remove"):
            self.budget_action(url)
        elif url.host() == "import-open":
            self.open_import()
        elif url.host() == "import-run":
            q = parse_qs(url.query())
            try:
                self.run_import(self._import_sources[int(q.get("i", ["-1"])[0])], q.get("h", ["0"])[0] == "1")
            except (ValueError, IndexError):
                pass
        elif url.host() == "import-file":
            self.import_from_file()
        elif url.host() == "clear-history":
            self.history = []
            jsave("history.json", [])
            self.refresh_completer()
            self.open_history()
        else:
            u = parse_qs(url.query()).get("u", [""])[0]
            self.bookmarks = [b for b in self.bookmarks if b["url"] != u]
            jsave("bookmarks.json", self.bookmarks)
            self.refresh_completer()
            self.open_bookmarks()

    # ----- extensions -----
    def show_ext_menu(self):
        hub = self.extensions
        m = QMenu(self)
        t = self.cur()
        page = t.url().toString() if t is not None else ""
        try:
            on_store = resolve_ext_url(page)[0] in ("crx", "amo")  # a Chrome Web Store or Firefox Add-ons page
        except ExtError:
            on_store = False
        if on_store:
            m.addAction("Add this page's extension to Fjord", lambda: hub.add_url(page))
            m.addSeparator()
        if not hub.available:
            m.addAction("Extensions need PyQt6-WebEngine 6.10 or newer", self.open_extensions)
            m.addSeparator()
        for r in hub.records:
            name, on = Path(r["dir"]).name, r.get("enabled", True)
            ico = QIcon(str(Path(r["dir"]) / r["icon"])) if r.get("icon") else QIcon()
            sub = m.addMenu(ico, r["name"])
            sub.addAction("Open", lambda _c=False, n=name: hub.popup(n)).setEnabled(on)
            if r.get("options"):
                sub.addAction("Settings", lambda _c=False, n=name: hub.options(n)).setEnabled(on)
            sub.addAction("Turn off" if on else "Turn on", lambda _c=False, n=name: hub.toggle(n))
            sub.addAction("Remove", lambda _c=False, n=name: self.confirm_remove_ext(n))
        if hub.records:
            m.addSeparator()
        m.addAction("Add or manage extensions…", self.open_extensions)
        m.exec(self.btn_ext.mapToGlobal(self.btn_ext.rect().bottomLeft()))

    def open_extensions(self, keep_scroll=False):
        t = self.cur()
        page = extensions_html(self)
        if keep_scroll and t is not None and t.url().scheme() == "fjord" and t.url().host() == "extensions":
            def go(y):
                def restore(_ok):
                    t.loadFinished.disconnect(restore)
                    t.page().runJavaScript("window.scrollTo(0,%d)" % int(y or 0))
                t.loadFinished.connect(restore)
                t.setHtml(page, QUrl("fjord://extensions"))
            t.page().runJavaScript("window.scrollY", go)
        else:
            self.show_page("extensions", page)

    def on_ext_changed(self):
        t = self.cur() if hasattr(self, "stack") else None
        if t is not None and t.url().scheme() == "fjord" and t.url().host() == "extensions":
            QTimer.singleShot(0, lambda: self.open_extensions(keep_scroll=True))

    def ext_action(self, url):
        q = parse_qs(url.query(QUrl.ComponentFormattingOption.FullyEncoded))
        g = lambda k: q.get(k, [""])[0]
        h, hub = url.host(), self.extensions
        if h == "ext-open":
            self.open_extensions()
        elif h == "ext-add":
            self.pick_extension(g("m"))
        elif h == "ext-url":
            hub.add_url(g("u"))
        elif h == "ext-toggle":
            hub.toggle(g("d"))
        elif h == "ext-remove":
            self.confirm_remove_ext(g("d"))
        elif h == "ext-popup":
            hub.popup(g("d"))
        elif h == "ext-options":
            hub.options(g("d"))

    def pick_extension(self, mode):
        mac = sys.platform == "darwin"
        if mode == "file":
            path, _ = QFileDialog.getOpenFileName(self, "Add extension", str(Path.home() / "Downloads"),
                                                  "Extensions (*.crx *.xpi *.zip *.ipa);;All files (*)")
        else:
            opts = QFileDialog.Option.ShowDirsOnly
            if mac:  # the native Mac dialog won't pick an .app, and that is where Safari extensions live
                opts |= QFileDialog.Option.DontUseNativeDialog
            path = QFileDialog.getExistingDirectory(self, "Pick an extension folder or Safari app",
                                                    "/Applications" if mac else str(Path.home()), opts)
        if path:
            self.extensions.add_path(path)

    def confirm_remove_ext(self, name):
        rec = self.extensions.record(name)
        if rec and QMessageBox.question(self, "Fjord", 'Remove "%s"? Its saved data goes with it.' % rec["name"]) \
                == QMessageBox.StandardButton.Yes:
            self.extensions.remove(name)

    def offer_extension(self, path, name):
        if QMessageBox.question(self, "Fjord", 'Add "%s" as an extension?' % name) == QMessageBox.StandardButton.Yes:
            self.extensions.add_path(path)

    # ----- menu -----
    def set_engine(self, name):
        self.settings["engine"] = name
        jsave("settings.json", self.settings)
        self.update_engine_btn()
        for i in range(self.stack.count()):
            t = self.stack.widget(i)
            if t.url().scheme() == "fjord" and t.url().host() == "start":
                t.setHtml(self.start_page(), START_URL)
        self.rerun_search()
        self.toast(f"Search engine: {name}", 2500)

    def rerun_search(self):
        t = self.cur()
        u = t.url()
        q = parse_qs(u.query()).get("q", [""])[0]
        on_engine = any(u.host() == QUrl(b).host() for b in ENGINES.values())
        if q and on_engine and u.host() != QUrl(ENGINES[self.engine]).host():
            t.load(to_url(q, self.engine))

    def set_font(self, name):
        self.settings["font"] = name
        jsave("settings.json", self.settings)
        apply_font(QApplication.instance(), name)
        for i in range(self.stack.count()):
            t = self.stack.widget(i)
            if t.url().scheme() == "fjord" and t.url().host() == "start":
                t.setHtml(self.start_page(), START_URL)

    def update_engine_btn(self):
        self.btn_engine.setIcon(engine_icon(self.engine))
        self.btn_engine.setIconSize(QSize(ToolIcon.GLYPH + 2, ToolIcon.GLYPH + 2))
        self.btn_engine.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.btn_engine.setText("")
        self.btn_engine.setToolTip(f"Search engine: {self.engine}")
        self.addr.setPlaceholderText(f"Search {self.engine} or enter address")

    def show_engine_menu(self):
        m = QMenu(self)
        for name in ENGINES:
            a = m.addAction(engine_icon(name), name)
            a.setCheckable(True)
            a.setChecked(name == self.engine)
            a.triggered.connect(lambda _c, n=name: self.set_engine(n))
        m.exec(self.btn_engine.mapToGlobal(self.btn_engine.rect().bottomLeft()))

    # ----- frequently visited sites (new tab page) -----
    @staticmethod
    def site_label(host):
        parts = host.split(".")
        core = parts[-2] if len(parts) >= 2 else host
        if len(parts) >= 3 and core in ("co", "com", "org", "net", "gov", "ac", "edu"):
            core = parts[-3]
        return core[:1].upper() + core[1:]

    def top_sites(self, limit=8):
        """Pinned sites first, then the most visited ones from history (search engines and hidden sites skipped)."""
        ts = self.topsites
        hidden = set(ts.get("hidden", []))
        engines = {self.host_of(QUrl(u)) for u in ENGINES.values()}
        out, seen = [], set()
        for pin in ts.get("pinned", []):
            h = self.host_of(QUrl(pin["url"]))
            if h and h not in seen:
                seen.add(h)
                out.append({"url": pin["url"], "title": pin.get("title") or self.site_label(h), "host": h})
        counts, scheme = {}, {}
        for hit in self.history[-3000:]:
            u = QUrl(hit["url"])
            if u.scheme() not in ("http", "https"):
                continue
            h = self.host_of(u)
            if h:
                counts[h] = counts.get(h, 0) + 1
                scheme[h] = u.scheme()
        for h, _n in sorted(counts.items(), key=lambda kv: -kv[1]):
            if len(out) >= limit:
                break
            if h in seen or h in hidden or h in engines:
                continue
            seen.add(h)
            out.append({"url": "%s://%s/" % (scheme[h], h), "title": self.site_label(h), "host": h})
        if not out:  # brand-new profile: fall back to bookmarks
            out = [{"url": b["url"], "title": b["title"] or b["url"], "host": ""} for b in self.bookmarks[:limit]]
        return out

    # ----- new tab greeting (custom text or daily quote) -----
    def greeting_conf(self):
        g = self.settings.get("greeting")
        return g if isinstance(g, dict) else {}

    @staticmethod
    def daily_quote_index():
        return datetime.date.today().toordinal() % len(QUOTES)

    def quote_index(self):
        pick = self.greeting_conf().get("pick")
        if isinstance(pick, int) and not isinstance(pick, bool) and 0 <= pick < len(QUOTES):
            return pick
        return self.daily_quote_index()

    def greeting_for_start(self):
        """(text, author) shown above the search bar, or None for the default line."""
        g = self.greeting_conf()
        if g.get("mode") == "custom":
            text = str(g.get("text", "")).strip()[:GREETING_MAX]
            if text:
                return text, ""
        elif g.get("mode") == "quote":
            return QUOTES[self.quote_index()]
        return None

    def update_greeting(self, **changes):
        g = dict(self.greeting_conf())
        g.update(changes)
        self.settings["greeting"] = g
        jsave("settings.json", self.settings)
        self.refresh_start_pages()

    def start_page(self):
        return start_html(self.engine, self.bookmarks, self.top_sites(), self.bg_for_start(), self.greeting_for_start())

    # ----- custom background (colour / gradient / image / video) -----
    def apply_bg_globals(self):
        conf = self.settings.get("bg") if isinstance(self.settings.get("bg"), dict) else {}
        css, light = None, False
        kind = conf.get("type")
        if kind == "color" and re.fullmatch(r"#[0-9a-fA-F]{6}", str(conf.get("color", ""))):
            css = conf["color"]
            r, g, b = (int(css[i:i + 2], 16) for i in (1, 3, 5))
            light = (0.299 * r + 0.587 * g + 0.114 * b) > 150
        elif kind == "gradient" and isinstance(conf.get("grad"), int) and 0 <= conf["grad"] < len(BG_GRADIENTS):
            css = BG_GRADIENTS[conf["grad"]][1]
        CUR_BG["css"], CUR_BG["light"] = css, light
        src = None  # where the accent colour comes from
        if kind == "color" and css:
            src = css
        elif kind == "gradient" and css:
            src = re.findall(r"#[0-9a-fA-F]{6}", css)[-1]
        elif (kind in ("image", "video") and conf.get("accent") and conf.get("accent_file") == conf.get("file")
              and conf.get("accent_v") == ACCENT_ALGO):
            src = conf["accent"]
        pair = derive_accent(src, 0.3 if kind == "color" else 0.2) if src else None
        mine = self.settings.get("accent")  # an accent picked in the welcome tour wins over the background's
        if isinstance(mine, dict) and all(re.fullmatch(r"#[0-9a-fA-F]{6}", str(mine.get(k, ""))) for k in ("main", "alt")):
            pair = (mine["main"].lower(), mine["alt"].lower())
        ACCENT["main"], ACCENT["alt"] = pair if pair else (DEFAULT_ACCENT["main"], DEFAULT_ACCENT["alt"])
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(themed(app_qss()))
            if hasattr(self, "completer"):
                self.completer.popup().setStyleSheet(themed(popup_qss()))

    def bg_for_start(self):
        conf = self.settings.get("bg") if isinstance(self.settings.get("bg"), dict) else {}
        if conf.get("type") in ("image", "video") and conf.get("file") and self.bgserver.port:
            f = BG_DIR / os.path.basename(str(conf["file"]))
            if f.is_file():
                return {"kind": conf["type"], "url": self.bgserver.url(f.name), "dim": int(conf.get("dim", 30)),
                        "file": f.name, "done": conf.get("accent_file") == f.name and conf.get("accent_v") == ACCENT_ALGO,
                        "evt": self.bgserver.event_url()}
        return None

    def convert_video(self, path):
        ff = shutil.which("ffmpeg")
        if not ff:
            self.toast("Fjord can only play WebM videos. Install ffmpeg (Windows: run  winget install ffmpeg  then restart Fjord) "
                       "and pick this video again to convert it automatically, or choose a .webm file.", 14000)
            return
        BG_DIR.mkdir(parents=True, exist_ok=True)
        name = "bg-%d.webm" % int(time.time())
        out = BG_DIR / (name + ".part")
        args = ["-y", "-i", path, "-an", "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "36", "-deadline", "realtime",
                "-cpu-used", "8", "-row-mt", "1", "-vf", "scale=w='min(1280,iw)':h=-2,fps=30", "-f", "webm", str(out)]
        proc = QProcess(self)
        self._ffmpeg = proc
        proc.finished.connect(lambda code, _st: self._video_converted(code, out, name, Path(path).name))
        proc.start(ff, args)
        self.toast("Converting the video to WebM... this can take a minute", 900000)

    def _video_converted(self, code, out, name, label):
        if code != 0 or not out.exists() or out.stat().st_size == 0:
            try:
                out.unlink()
            except OSError:
                pass
            self.toast("Could not convert that video. Try a different file or a .webm.", 8000)
            return
        final = BG_DIR / name
        try:
            out.replace(final)
        except OSError:
            self.toast("Could not save the converted video", 6000)
            return
        for f in BG_DIR.iterdir():
            if f.name != name:
                try:
                    f.unlink()
                except OSError:
                    pass
        conf = dict(self.settings.get("bg") or {})
        conf.pop("accent", None)
        conf.pop("accent_file", None)
        conf.update(type="video", file=name, label=label)
        self.settings["bg"] = conf
        jsave("settings.json", self.settings)
        self.apply_bg_globals()
        self.refresh_start_pages()
        self.toast("Video background ready", 3000)
        t = self.cur()
        if t is not None and t.url().scheme() == "fjord" and t.url().host() == "settings":
            self.open_settings(keep_scroll=True)

    def on_bg_event(self, q):
        if isinstance(q, dict) and q.get("kind", [""])[0] in ("accent", "videoerror"):
            self.handle_bg(q)

    def pick_bg_file(self, kind):
        filt = {"image": "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.avif)",
                "video": "Videos (*.webm *.mp4 *.m4v *.ogv *.mov)"}[kind]
        path, _ = QFileDialog.getOpenFileName(self, "Choose a background " + kind, str(Path.home()), filt)
        if not path:
            return None
        if kind == "video" and Path(path).suffix.lower() != ".webm":
            self.convert_video(path)  # Fjord's browser engine can't decode H.264, so make a WebM copy
            return None
        BG_DIR.mkdir(parents=True, exist_ok=True)
        name = "bg-%d%s" % (int(time.time()), Path(path).suffix.lower())
        try:
            shutil.copyfile(path, BG_DIR / name)
        except OSError:
            self.toast("Could not copy that file")
            return None
        for f in BG_DIR.iterdir():  # keep only the active background
            if f.name != name:
                try:
                    f.unlink()
                except OSError:
                    pass
        return name, Path(path).name

    def handle_bg(self, q):
        def g(k):
            return q.get(k, [""])[0]
        kind = g("kind")
        conf = dict(self.settings.get("bg") or {})
        if kind == "default":
            conf = {"type": "default", "dim": conf.get("dim", 30)}
        elif kind == "color" and re.fullmatch(r"#[0-9a-fA-F]{6}", g("c")):
            conf.update(type="color", color=g("c").lower())
        elif kind == "gradient" and g("i").isdigit() and int(g("i")) < len(BG_GRADIENTS):
            conf.update(type="gradient", grad=int(g("i")))
        elif kind == "dim" and g("v").isdigit():
            conf["dim"] = min(80, int(g("v")))
        elif kind == "accent":
            c, f = g("c"), g("f")
            if re.fullmatch(r"#[0-9a-fA-F]{6}", c) and f == conf.get("file") and conf.get("type") in ("image", "video"):
                conf.update(accent=c.lower(), accent_file=f, accent_v=ACCENT_ALGO)
                self.settings["bg"] = conf
                jsave("settings.json", self.settings)
                self.apply_bg_globals()
                self.refresh_start_pages()
            return
        elif kind == "videoerror":
            self.toast("This video can't be played in Fjord (unsupported codec). Pick it again: Fjord converts it to WebM if "
                       "ffmpeg is installed, or choose a .webm file.", 10000)
            return
        elif kind in ("image", "video"):
            picked = self.pick_bg_file(kind)
            if picked:
                conf.pop("accent", None)
                conf.pop("accent_file", None)
                conf.update(type=kind, file=picked[0], label=picked[1])
        self.settings["bg"] = conf
        jsave("settings.json", self.settings)
        self.apply_bg_globals()
        self.refresh_start_pages()
        self.open_settings(keep_scroll=True)

    def refresh_start_pages(self):
        for i in range(self.stack.count()):
            t = self.stack.widget(i)
            if t.url().scheme() == "fjord" and t.url().host() == "start":
                t.setHtml(self.start_page(), START_URL)

    def add_top_site(self):
        text, ok = QInputDialog.getText(self, "Add site", "Website address:")
        text = text.strip()
        if ok and text:
            u = QUrl(text if "://" in text else "https://" + text)
            h = self.host_of(u)
            if u.scheme() in ("http", "https") and "." in h:
                ts = self.topsites
                ts["pinned"] = [p for p in ts["pinned"] if self.host_of(QUrl(p["url"])) != h]
                ts["pinned"].append({"url": "%s://%s/" % (u.scheme(), u.host()), "title": self.site_label(h)})
                ts["hidden"] = [x for x in ts["hidden"] if x != h]
                jsave("topsites.json", ts)
            else:
                self.toast("Enter a valid website address")
        self.refresh_start_pages()

    def remove_top_site(self, host):
        ts = self.topsites
        if any(self.host_of(QUrl(p["url"])) == host for p in ts["pinned"]):
            ts["pinned"] = [p for p in ts["pinned"] if self.host_of(QUrl(p["url"])) != host]
        elif host and host not in ts["hidden"]:
            ts["hidden"].append(host)
        jsave("topsites.json", ts)
        self.refresh_start_pages()

    # ----- essentials -----
    @staticmethod
    def host_of(u):
        h = u.host() if isinstance(u, QUrl) else QUrl(u).host()
        return h[4:] if h.startswith("www.") else h

    # ----- sticky notes -----
    def send_notes(self, page):
        url = page.url()
        if url.scheme() not in ("http", "https"):
            return
        key = self.host_of(url)
        data = json.dumps(self.notes.get(key, []))
        page.runJavaScript("window.__fjNotesLoad && window.__fjNotesLoad(%s);" % data)

    def save_notes(self, host, payload):
        key = self.host_of(QUrl("//" + host)) if host else ""
        if not key:
            return
        try:
            raw = json.loads(payload)
        except ValueError:
            return
        if not isinstance(raw, list):
            return
        clean = []
        for n in raw[:NOTES_MAX]:
            if not isinstance(n, dict):
                continue
            num = lambda k, d, lo, hi: min(max(int(n.get(k, d)), lo), hi) if isinstance(n.get(k, d), (int, float)) else d
            color = n.get("color") if n.get("color") in NOTE_COLORS + OLD_NOTE_COLORS else NOTE_COLORS[0]
            clean.append({"id": str(n.get("id", ""))[:20], "text": str(n.get("text", ""))[:10000], "color": color,
                          "x": num("x", 0, 0, 100000), "y": num("y", 0, 0, 1000000),
                          "w": num("w", 250, 210, 1200), "h": num("h", 210, 150, 1200)})
        if clean:
            self.notes[key] = clean
        else:
            self.notes.pop(key, None)
        jsave("notes.json", self.notes)

    def save_icon(self, host, icon):
        p = ICON_DIR / f"{host}.png"
        if icon.isNull() or p.exists():
            return False
        return icon.pixmap(32, 32).save(str(p))

    def on_icon(self, tab, icon):
        tab.row.set_icon(icon)
        host = self.host_of(tab.url())
        saved = bool(host) and tab.url().scheme() in ("http", "https") and self.save_icon(host, icon)
        if saved and any(e["host"] == host for e in self.essentials):
            QTimer.singleShot(0, self.rebuild_essentials)

    def rebuild_essentials(self):
        while self.ess_grid.count():
            w = self.ess_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.tiles = []
        for n, e in enumerate(self.essentials):
            b = FadeButton(12)
            b.setObjectName("essential")
            b.setCheckable(True)
            b.setFixedSize(*((34, 30) if self.horiz else ((44, 44) if self.compact else (46, 40))))
            b.setToolTip(e["title"])
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            icon_path = ICON_DIR / f"{e['host']}.png"
            if icon_path.exists():
                b.setIcon(QIcon(str(icon_path)))
                b.setIconSize(QSize(22, 22) if self.compact else QSize(20, 20))
            else:
                b.setText((e["title"] or e["host"])[:1].upper())
            b.clicked.connect(lambda _c, e=e: self.open_essential(e))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _p, e=e: self.essential_menu(e))
            pos = (0, n) if self.horiz else ((n, 0) if self.compact else (n // 4, n % 4))
            self.ess_grid.addWidget(b, *pos)
            self.tiles.append((b, e))
        self.ess_grid.setAlignment(Qt.AlignmentFlag.AlignHCenter if self.compact else Qt.AlignmentFlag.AlignLeft)
        self.ess_wrap.setVisible(bool(self.essentials))
        self.divider.setVisible(bool(self.essentials) and not self.horiz)
        self.mark_essentials()

    def mark_essentials(self, u=None):
        t = self.cur() if hasattr(self, "stack") else None
        host = self.host_of(u if u is not None else (t.url() if t else QUrl()))
        for b, e in self.tiles:
            b.setChecked(e["host"] == host)

    def open_essential(self, e):
        for i in range(self.stack.count()):
            if self.host_of(self.stack.widget(i).url()) == e["host"]:
                self.tabs.setCurrentRow(i)
                break
        else:
            self.new_tab(e["url"])
        self.mark_essentials()

    def essential_menu(self, e):
        m = QMenu(self)
        m.addAction("Open in new tab", lambda: self.new_tab(e["url"]))
        m.addAction("Remove from Essentials", lambda: self.remove_essential(e["host"]))
        m.exec(QCursor.pos())

    def add_essential(self, tab):
        u = tab.url()
        host = self.host_of(u)
        if self.is_internal(u) or not host:
            self.toast("Open a website first, then add it to Essentials")
            return
        if any(e["host"] == host for e in self.essentials):
            self.toast("Already in Essentials", 2500)
            return
        self.save_icon(host, tab.icon())
        self.essentials.append({"url": f"{u.scheme()}://{u.host()}/", "title": tab.title() or host, "host": host})
        jsave("essentials.json", self.essentials)
        self.rebuild_essentials()
        self.toast("Added to Essentials", 2500)

    def remove_essential(self, host):
        self.essentials = [e for e in self.essentials if e["host"] != host]
        jsave("essentials.json", self.essentials)
        self.rebuild_essentials()

    def toolbar_menu(self, pos):
        m = QMenu(self)
        m.addAction("Customize toolbar…", self.tb_ed.start)
        m.addMenu(self.style_menu(m))
        m.exec(self.toolbar.mapToGlobal(pos))

    # ----- updates -----
    def check_updates(self, manual=True):
        if self._update_busy:
            return
        self._update_busy = True
        self._update_manual = manual
        if manual:
            self.toast("Checking for updates…", 3000)
        self.updater.check()

    def set_auto_update(self, on):
        self.settings["auto_update"] = bool(on)
        jsave("settings.json", self.settings)

    def set_scratch_popup(self, on):
        self.settings["scratch_popup"] = bool(on)
        jsave("settings.json", self.settings)
        self.toast("Scratchpad %s when you drag something" % ("pops up" if on else "stays put"), 2500)

    def on_update_uptodate(self):
        self._update_busy = False
        if self._update_manual:
            self.toast("Fjord is up to date (v%s)" % APP_VERSION, 4000)

    def on_update_failed(self, msg):
        self._update_busy = False
        if self._update_manual:
            self.toast(msg, 8000)

    def on_update_found(self, info):
        box = QMessageBox(self)
        box.setWindowTitle("Fjord update")
        box.setText("Fjord %s is available (you have %s).\nUpdate and restart now?" % (info["version"], APP_VERSION))
        if info.get("notes"):  # release notes shown as plain text (the built-in "Show Details" button gets clipped by our styling)
            n = info["notes"]
            box.setInformativeText(n if len(n) <= 400 else n[:400].rstrip() + "…")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        if box.exec() == QMessageBox.StandardButton.Yes:
            self.toast("Downloading Fjord %s…" % info["version"], 60000)
            self.updater.install(info)
        else:
            self._update_busy = False

    def on_update_installed(self, version):
        self._update_busy = False
        self.toast("Updated to Fjord %s, restarting…" % version, 4000)
        QTimer.singleShot(900, self.restart_app)

    def restart_app(self):
        frozen = getattr(sys, "frozen", False)
        p = QProcess()
        env = QProcessEnvironment.systemEnvironment()
        env.insert("FJORD_RESTARTED", "1")  # tells the new copy to wait a moment for this one to release the profile
        p.setProcessEnvironment(env)
        p.setProgram(sys.executable)
        p.setArguments([] if frozen else [str(_app_path())])
        p.startDetached()
        self.close()
        QApplication.quit()

    # ----- site time budgets -----
    def _budgets_load(self):
        d = jload("budgets.json", {})
        if not isinstance(d, dict):
            d = {}
        lim = {}
        for k, v in (d.get("limits") or {}).items() if isinstance(d.get("limits"), dict) else []:
            try:
                if norm_site(k) == k and 1 <= int(v) <= 1440:
                    lim[k] = int(v)
            except (TypeError, ValueError):
                pass
        out = {"limits": lim, "day": str(d.get("day") or ""), "used": {}, "extra": {}, "off": []}
        for key in ("used", "extra"):
            if isinstance(d.get(key), dict):
                out[key] = {k: float(v) for k, v in d[key].items() if isinstance(v, (int, float))}
        if isinstance(d.get("off"), list):
            out["off"] = [k for k in d["off"] if isinstance(k, str)]
        return out

    def budget_save(self):
        jsave("budgets.json", self.budgets)

    def budget_key(self, host):
        """Which budgeted site (if any) this host belongs to, e.g. m.youtube.com -> youtube.com."""
        host = (host or "").lower()
        host = host[4:] if host.startswith("www.") else host
        best = None
        for k in self.budgets["limits"]:
            if (host == k or host.endswith("." + k)) and (best is None or len(k) > len(best)):
                best = k
        return best

    def budget_over_key(self, host):
        k = self.budget_key(host)
        bd = self.budgets
        if k and k not in bd["off"] and bd["used"].get(k, 0) >= bd["limits"][k] * 60 + bd["extra"].get(k, 0):
            return k
        return None

    def budget_tick(self):
        try:
            now = time.monotonic()
            dt = min(now - self._b_last, 3.0)  # a long gap means the computer slept; don't count it
            self._b_last = now
            bd = self.budgets
            today = datetime.date.today().isoformat()
            if bd["day"] != today:
                bd.update(day=today, used={}, extra={}, off=[])
                self._b_warned = set()
                self.budget_save()
            if bd["limits"]:
                cur = self.cur()
                active = self.isActiveWindow() and not self.isMinimized() and os_idle_seconds() < 180
                keys = set()
                for w in self.tab_list():
                    if w.closing or self.is_internal(w.url()):
                        continue
                    audible = w.page().recentlyAudible()
                    if audible or (w is cur and active):
                        k = self.budget_key(w.url().host())
                        if k:
                            keys.add(k)
                            if audible and self.budget_over_key(w.url().host()):
                                w.page().runJavaScript(BUDGET_PAUSE_JS)  # over the limit: stop media playing in the background
                for k in keys:
                    bd["used"][k] = bd["used"].get(k, 0) + dt
                    total = bd["limits"][k] * 60 + bd["extra"].get(k, 0)
                    if total - bd["used"][k] <= 300 and bd["limits"][k] >= 10 and (k, total) not in self._b_warned and bd["used"][k] < total:
                        self._b_warned.add((k, total))
                        self.toast("5 minutes left on %s today" % k, 6000)
                self._b_ticks += 1
                if keys and self._b_ticks % 15 == 0:
                    self.budget_save()
            self.budget_overlay_sync()
        except Exception:
            pass  # never let the timer take the browser down

    def budget_overlay_sync(self):
        if not hasattr(self, "budget_ov"):
            return
        t = self.cur()
        k = None
        if t is not None and self.budgets["limits"] and not self.is_internal(t.url()):
            k = self.budget_over_key(t.url().host())
        if k:
            newly = self.budget_ov.show_for(k, int(self.budgets["used"].get(k, 0) / 60), self.budgets["limits"][k])
            if newly:
                t.page().runJavaScript(BUDGET_PAUSE_JS)
        elif self.budget_ov.isVisible():
            self.budget_ov.hide()

    def budget_more(self):
        k = self.budget_ov.key
        if k:
            self.budgets["extra"][k] = self.budgets["extra"].get(k, 0) + 300
            self.budget_save()
            self.budget_overlay_sync()
            self.toast("5 more minutes on %s" % k, 3000)

    def budget_skip(self):
        k = self.budget_ov.key
        if k and k not in self.budgets["off"]:
            self.budgets["off"].append(k)
            self.budget_save()
            self.budget_overlay_sync()
            self.toast("No limit on %s for the rest of today" % k, 4000)

    def budget_close(self):
        t = self.cur()
        if t is not None:
            self.close_tab(t)
        self.budget_overlay_sync()

    def open_budgets(self):
        t = self.cur()
        self._b_suggest = norm_site(t.url().host()) if t is not None and not self.is_internal(t.url()) else ""
        self.show_page("budgets", budgets_html(self))

    def budget_action(self, url):
        h = url.host()
        q = parse_qs(url.query())
        g = lambda k: q.get(k, [""])[0]
        bd = self.budgets
        if h == "budget-add":
            site = norm_site(g("site"))
            try:
                m = int(float(g("min")))
            except ValueError:
                m = 0
            if not site:
                self.toast("Enter a site like youtube.com", 4000)
            elif not 1 <= m <= 1440:
                self.toast("Minutes per day must be between 1 and 1440", 4000)
            else:
                bd["limits"][site] = m
                self.toast("%s: %d min a day" % (site, m), 3000)
        elif h == "budget-set":
            site = norm_site(g("h"))
            try:
                d = int(g("d"))
            except ValueError:
                d = 0
            if site in bd["limits"]:
                bd["limits"][site] = max(1, min(1440, bd["limits"][site] + d))
        elif h == "budget-remove":
            site = norm_site(g("h"))
            bd["limits"].pop(site, None)
            bd["used"].pop(site, None)
            bd["extra"].pop(site, None)
            if site in bd["off"]:
                bd["off"].remove(site)
        self.budget_save()
        self.budget_overlay_sync()
        self.show_page("budgets", budgets_html(self))

    def show_menu(self):
        m = QMenu(self)
        m.addAction("New tab\tCtrl+T", lambda: self.new_tab(focus_address=True))
        m.addAction("Reopen closed tab\tCtrl+Shift+T", self.reopen_tab)
        m.addAction("Add page to Essentials", lambda: self.add_essential(self.cur()))
        m.addSeparator()
        m.addAction("Bookmarks\tCtrl+Shift+O", self.open_bookmarks)
        m.addAction("History\tCtrl+H", self.open_history)
        m.addAction("Extensions", self.open_extensions)
        m.addAction("Import from another browser…", self.open_import)
        m.addAction("Open downloads folder", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(Path.home() / "Downloads"))))
        m.addSeparator()
        m.addAction("Find in page\tCtrl+F", self.open_find)
        m.addAction("Save page as PDF\tCtrl+P", self.print_pdf)
        m.addAction("View page source", lambda: self.new_tab("view-source:" + self.cur().url().toString())
                    if not self.is_internal(self.cur().url()) else None)
        if self.stack.split:
            m.addAction("Exit split view", lambda _c=False: self.exit_split())
        m.addSeparator()

        # ----- View & appearance -----
        view = m.addMenu("View && appearance")
        view.addAction("Zoom in", lambda: self.zoom(0.1))
        view.addAction("Zoom out", lambda: self.zoom(-0.1))
        view.addAction("Reset zoom", lambda: self.cur().setZoomFactor(1.0))
        view.addAction("Fullscreen\tF11", self.toggle_fullscreen)
        view.addSeparator()
        sub = view.addMenu("Search engine")
        for name in ENGINES:
            a = sub.addAction(engine_icon(name), name)
            a.setCheckable(True)
            a.setChecked(name == self.engine)
            a.triggered.connect(lambda _c, n=name: self.set_engine(n))
        fsub = view.addMenu("Font")
        for fam in installed_fonts():
            fa = fsub.addAction(fam)
            fa.setCheckable(True)
            fa.setChecked(fam == CUR_FONT["family"])
            fa.triggered.connect(lambda _c, n=fam: self.set_font(n))
        sp = view.addAction("Pop up Scratchpad when dragging")
        sp.setCheckable(True)
        sp.setChecked(bool(self.settings.get("scratch_popup", True)))
        sp.triggered.connect(lambda _c: self.set_scratch_popup(not self.settings.get("scratch_popup", True)))
        view.addMenu(self.style_menu(view))
        view.addMenu(self.winbtn_menu(view))
        view.addAction("Customize toolbar…", self.tb_ed.start)

        # ----- Privacy & performance -----
        priv = m.addMenu("Privacy && performance")
        ab = priv.addAction("Block ads && trackers")
        ab.setCheckable(True)
        ab.setChecked(self.adblock.enabled)
        ab.triggered.connect(lambda _c: self.set_adblock(not self.adblock.enabled))
        site = self.host_of(self.cur().url())
        if site and not self.is_internal(self.cur().url()):
            paused = site in self.adblock.allow
            priv.addAction(("Resume" if paused else "Pause") + " ad blocking on " + site,
                           lambda: self.pause_site(site, not paused))
        sl = priv.addAction("Sleep background tabs (saves memory)")
        sl.setCheckable(True)
        sl.setChecked(bool(self.settings.get("sleep_tabs", True)))
        sl.triggered.connect(lambda _c: self.set_sleep_tabs(not self.settings.get("sleep_tabs", True)))
        priv.addAction("Site time budgets…", self.open_budgets)
        priv.addAction("VPN / Proxy…", lambda: self.open_settings())

        # ----- Updates -----
        upd = m.addMenu("Updates")
        upd.addAction("Check for updates… (v%s)" % APP_VERSION, lambda: self.check_updates(True))
        au = upd.addAction("Check for updates on launch")
        au.setCheckable(True)
        au.setChecked(bool(self.settings.get("auto_update", True)))
        au.triggered.connect(lambda _c: self.set_auto_update(not self.settings.get("auto_update", True)))

        m.addSeparator()
        m.addAction("Welcome tour…", self.start_tour)
        m.addAction("Settings\tCtrl+,", lambda: self.open_settings())
        m.addAction("Quit", self.close)
        m.exec(self.btn_menu.mapToGlobal(self.btn_menu.rect().bottomLeft()))

    # ----- session -----
    def _restore_session(self, cli):
        data = jload("session.json", [])
        if isinstance(data, dict):  # saved groups come back even when launched with URLs
            self.groups = [g for g in data.get("groups", []) if isinstance(g, dict) and "id" in g]
        if cli:
            entries = [{"url": q.toString()} for q in (to_url(u, self.engine) for u in cli if u) if q]
        elif isinstance(data, dict):
            entries = data.get("tabs", [])
        else:
            entries = [{"url": u} for u in data]
        if not entries:
            self.new_tab(focus_address=True)
            self._restored = True
            self.rebuild_groups()
            return
        for n, en in enumerate(entries):
            t = self.new_tab(QUrl(en["url"]), background=True, lazy=(n > 0 and not cli))
            if en.get("group") and self.group_by_id(en["group"]):
                t.group = en["group"]
        self.tabs.setCurrentRow(0)
        self._restored = True
        self.rebuild_groups()

    def save_groups(self):
        """Write the open tabs and every tab group (name, colour, open/shut state, which tabs) to session.json."""
        def url_of(w):
            return w.pending if w.pending is not None else w.url()
        tabs = [w for w in self.tab_list() if not w.closing and not self.is_internal(url_of(w))]
        jsave("session.json", {"tabs": [{"url": url_of(w).toString(), "group": w.group} for w in tabs],
                               "groups": [dict(g) for g in self.groups]})

    def queue_save(self):
        """Save shortly after a group change, so a crash or kill doesn't lose the groups."""
        if not self._restored or self._save_pending:
            return
        self._save_pending = True

        def go():
            self._save_pending = False
            self.save_groups()
        QTimer.singleShot(400, go)

    def closeEvent(self, e):
        self.budget_save()
        if self._restored:
            self.save_groups()
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()
        super().closeEvent(e)


def resource_path(name):
    """Find a bundled file both when run as a script and when packaged by PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def main():
    if sys.platform == "win32":
        import ctypes  # gives Fjord its own taskbar identity so the icon shows instead of python.exe's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("fjord.browser.1")
    if os.environ.pop("FJORD_RESTARTED", None):
        time.sleep(2.5)  # we were relaunched by the updater: give the old process time to exit
    cleanup_old_update()
    app = QApplication(sys.argv)
    app.setApplicationName("Fjord")
    app.setWindowIcon(QIcon(resource_path("fjord.ico")))
    DATA_DIR.mkdir(exist_ok=True)
    load_custom_fonts()
    UI["mode"] = valid_ui_mode(jload("settings.json", {}).get("ui_style"))
    load_ui_tuning(jload("settings.json", {}))
    apply_font(app, pick_font(jload("settings.json", {}).get("font")))
    win = Browser()
    win.setWindowOpacity(0.0)
    win.show()
    animate(win, b"windowOpacity", 0.0, 1.0, 380)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
