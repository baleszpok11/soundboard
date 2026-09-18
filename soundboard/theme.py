"""Design tokens: one look per platform, in light and dark.

CustomTkinter draws its own widgets, so "native" here means matching each
platform's design language - its system font, control density, corner
radius and surface colours - not hosting real Aqua or Fluent controls.
Three things carry most of that feeling, and all three live here:

  * the system UI font, which is what the eye reads as foreign first
  * control height and corner radius, where macOS is compact and round
    and Windows is taller and squarer
  * how surfaces separate: macOS leans on elevation and shadowless fills,
    Fluent draws a hairline border around its cards

Every colour token is a (light, dark) pair. CustomTkinter resolves such a
pair against the current appearance mode by itself, and re-resolves it
when the mode changes, so call sites pass the token and never care which
mode is on. Raw tkinter widgets cannot take a pair; they go through
resolve() and register_tk() below.

Metrics that widgets rarely pass explicitly (radius, height, font) are
pushed into CustomTkinter's ThemeManager by apply_theme(), so they reach
every widget in the app without each call site repeating them.
"""

import os
import sys
import tkinter as tk
import tkinter.font

import customtkinter as ctk


def _platform_key():
    # SOUNDBOARD_PLATFORM forces one platform's look on another, which is
    # the only way to see the Windows design from a Mac and the reason the
    # two are worth keeping in one build rather than one per installer.
    forced = os.environ.get("SOUNDBOARD_PLATFORM", "").strip().lower()
    if forced in _NEUTRALS:
        return forced
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


# -- palettes ------------------------------------------------------------
#
# Neutrals are the platform's own. macOS greys are warm and step in small
# increments; Fluent's are cooler with a visible card border; Linux gets a
# neutral Adwaita-ish set rather than a fourth designed look.

_NEUTRALS = {
    "macos": {
        "bg": ("#ECECEE", "#1C1C1E"),
        "surface": ("#FFFFFF", "#2C2C2E"),
        "row": ("#F4F4F6", "#3A3A3C"),
        "row_hover": ("#E8E8EC", "#48484A"),
        "border": ("#D6D6DA", "#3A3A3C"),
        "text": ("#1D1D1F", "#F5F5F7"),
        "text_dim": ("#6E6E73", "#98989D"),
    },
    "windows": {
        "bg": ("#F3F3F3", "#202020"),
        "surface": ("#FFFFFF", "#2B2B2B"),
        "row": ("#FAFAFA", "#323232"),
        "row_hover": ("#F0F0F0", "#3B3B3B"),
        "border": ("#E3E3E3", "#3D3D3D"),
        "text": ("#1A1A1A", "#FFFFFF"),
        "text_dim": ("#5D5D5D", "#A0A0A0"),
    },
    "linux": {
        "bg": ("#FAFAFA", "#242424"),
        "surface": ("#FFFFFF", "#303030"),
        "row": ("#F2F2F2", "#383838"),
        "row_hover": ("#E9E9E9", "#414141"),
        "border": ("#DCDCDC", "#454545"),
        "text": ("#202020", "#FFFFFF"),
        "text_dim": ("#5E5E5E", "#9A9A9A"),
    },
}

# The orange is the app's own and stays on every platform: the native part
# is the chrome around it. Light mode darkens it so it holds up on white.
# Text on an orange fill is near-black in both modes - white on orange is
# the one combination this palette cannot make readable.
_ACCENT = ("#EA7600", "#FF8C00")
_ACCENT_HOVER = ("#CC6500", "#E67600")
_ON_ACCENT = ("#FFFFFF", "#1A1A1A")
# Orange as *text* on a plain background needs to be darker still.
_ACCENT_TEXT = ("#A04C00", "#FFA033")
# Two reds: one to fill a destructive button, one to write with. The fill
# needs white on it in both modes, so it cannot be the light red that dark
# mode wants for text.
_ERROR = ("#C4271F", "#D64541")
_ERROR_HOVER = ("#A81E17", "#B93A33")
_ON_ERROR = ("#FFFFFF", "#FFFFFF")
_ERROR_TEXT = ("#B0221B", "#FF8A8A")

# -- metrics -------------------------------------------------------------
#
# macOS: compact controls, generous radii, 13pt system text.
# Windows: taller controls (Fluent's 32px minimum), 4px radii, 14pt Segoe.

PLATFORM = _platform_key()

_METRICS = {
    "macos": {
        "font": ("SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue"),
        "mono": ("SF Mono", "Menlo", "Courier"),
        "size": 13,
        "size_small": 11,
        "size_title": 15,
        "radius_control": 7,
        "radius_card": 10,
        "control_h": 28,
        "row_h": 40,
        "pad": 10,
        "gap": 8,
        "card_border": 0,
        "button_border": 0,
    },
    "windows": {
        "font": ("Segoe UI Variable Text", "Segoe UI"),
        "mono": ("Cascadia Mono", "Consolas", "Courier New"),
        "size": 14,
        "size_small": 12,
        "size_title": 16,
        "radius_control": 4,
        "radius_card": 8,
        "control_h": 32,
        "row_h": 44,
        "pad": 12,
        "gap": 8,
        "card_border": 1,
        "button_border": 1,
    },
    "linux": {
        "font": ("Cantarell", "DejaVu Sans", "Ubuntu"),
        "mono": ("DejaVu Sans Mono", "Monospace"),
        "size": 13,
        "size_small": 11,
        "size_title": 15,
        "radius_control": 6,
        "radius_card": 8,
        "control_h": 30,
        "row_h": 42,
        "pad": 10,
        "gap": 8,
        "card_border": 1,
        "button_border": 0,
    },
}


def _first_installed(families, fallback):
    """The first of these font families Tk actually has. Naming a missing
    family is not an error in Tk - it silently substitutes something that
    is usually wrong - so the choice is made against the real list."""
    try:
        available = {f.lower() for f in tk.font.families()}
    except Exception:
        return fallback
    for family in families:
        if family.lower() in available:
            return family
    return fallback


class _Tokens:
    """The active platform's tokens. Attributes rather than a dict so a
    typo raises instead of returning None into a colour argument."""

    def __init__(self, platform):
        self.platform = platform
        n = _NEUTRALS[platform]
        m = _METRICS[platform]
        self.bg = n["bg"]
        self.surface = n["surface"]
        self.row = n["row"]
        self.row_hover = n["row_hover"]
        self.border = n["border"]
        self.text = n["text"]
        self.text_dim = n["text_dim"]
        self.accent = _ACCENT
        self.accent_hover = _ACCENT_HOVER
        self.on_accent = _ON_ACCENT
        self.accent_text = _ACCENT_TEXT
        self.error = _ERROR
        self.error_hover = _ERROR_HOVER
        self.on_error = _ON_ERROR
        self.error_text = _ERROR_TEXT
        for key, value in m.items():
            if key not in ("font", "mono"):
                setattr(self, key, value)
        self._families = m["font"]
        self._mono_families = m["mono"]
        self.font_family = m["font"][-1]
        self.mono_family = m["mono"][-1]

    def pick_fonts(self):
        """Resolve the font families. Needs a Tk root, so apply_theme()
        calls this once the window exists."""
        self.font_family = _first_installed(self._families, self.font_family)
        self.mono_family = _first_installed(self._mono_families, self.mono_family)


T = _Tokens(PLATFORM)

# -- names the UI modules import ----------------------------------------
#
# The old flat names, now (light, dark) pairs. Keeping them means every
# existing widget gains light mode without its call site changing.

COLOR_BG = T.bg
COLOR_SURFACE = T.surface
COLOR_ROW = T.row
COLOR_ROW_HOVER = T.row_hover
COLOR_BORDER = T.border
COLOR_TEXT = T.text
COLOR_TEXT_DIM = T.text_dim
COLOR_ORANGE = T.accent
COLOR_ORANGE_HOVER = T.accent_hover
COLOR_ON_ACCENT = T.on_accent
COLOR_ACCENT_TEXT = T.accent_text
COLOR_ERROR = T.error
COLOR_ERROR_HOVER = T.error_hover
COLOR_ON_ERROR = T.on_error
COLOR_ERROR_TEXT = T.error_text

RADIUS_CONTROL = T.radius_control
RADIUS_CARD = T.radius_card
CONTROL_H = T.control_h
ROW_H = T.row_h
PAD = T.pad
GAP = T.gap
CARD_BORDER = T.card_border

VOLUME_MAX = 200  # percent
FADE_SLIDER_MAX_S = 10.0  # the longest fade the per-sound sliders offer

APPEARANCE_MODES = ("system", "light", "dark")

# -- fonts ---------------------------------------------------------------
#
# CTkFont needs a Tk root, so these are built on first use rather than at
# import, and cached: a CTkFont per label would be thousands of Tk font
# objects.

_font_cache = {}


def font(role="body"):
    """A CTkFont for one of: title, body, body_bold, small, small_bold,
    mono. Cached per role."""
    if role in _font_cache:
        return _font_cache[role]
    sizes = {
        "title": (T.size_title, "bold"),
        "body": (T.size, "normal"),
        "body_bold": (T.size, "bold"),
        "small": (T.size_small, "normal"),
        "small_bold": (T.size_small, "bold"),
        "mono": (T.size_small, "normal"),
    }
    size, weight = sizes.get(role, (T.size, "normal"))
    family = T.mono_family if role == "mono" else T.font_family
    _font_cache[role] = ctk.CTkFont(family=family, size=size, weight=weight)
    return _font_cache[role]


# -- appearance mode -----------------------------------------------------

_tk_widgets = []


def register_tk(widget, **token_options):
    """Track a raw tkinter widget so its colours follow the mode.

    Plain tkinter takes one colour, not a (light, dark) pair, so these
    widgets cannot re-resolve themselves the way CustomTkinter's do.
    Pass the tokens by option name, e.g. register_tk(text, bg=COLOR_ROW,
    fg=COLOR_TEXT); they are applied now and again on every mode change.
    """
    _tk_widgets.append((widget, token_options))
    _paint_tk(widget, token_options)


def _paint_tk(widget, token_options):
    try:
        widget.configure(**{k: resolve(v) for k, v in token_options.items()})
    except tk.TclError:
        pass  # the widget is gone; _refresh_tk drops it


_mode_callbacks = []


def on_appearance_change(callback):
    """Run this after every mode change. For anything drawn rather than
    configured - canvas items keep the colour they were given, so they
    have to be drawn again."""
    _mode_callbacks.append(callback)


def _run_mode_callbacks():
    for callback in list(_mode_callbacks):
        try:
            callback()
        except tk.TclError:
            _mode_callbacks.remove(callback)  # its widget is gone


def _refresh_tk():
    alive = []
    for widget, options in _tk_widgets:
        try:
            if not widget.winfo_exists():
                continue
        except tk.TclError:
            continue
        _paint_tk(widget, options)
        alive.append((widget, options))
    _tk_widgets[:] = alive


def resolve(token):
    """One colour from a (light, dark) pair, for the mode in effect. A
    plain colour string passes through, so callers can mix the two."""
    if isinstance(token, (tuple, list)):
        return token[1] if ctk.get_appearance_mode() == "Dark" else token[0]
    return token


def set_appearance(mode):
    """Switch between "system", "light" and "dark". CustomTkinter repaints
    its own widgets; the raw tkinter ones are repainted here."""
    if mode not in APPEARANCE_MODES:
        mode = "system"
    ctk.set_appearance_mode(mode)
    _refresh_tk()
    _run_mode_callbacks()
    return mode


def wrap_to_width(label, margin=24):
    """Keep a label wrapping at the width it actually has.

    A fixed wraplength is a guess about the window: set it wider than the
    label ever gets and the text runs past the edge and is cut off, which
    is what happens to every full-width label in a window narrower than
    the guess. The app's minimum is 640, so 800 was never safe. Binding
    to the parent's <Configure> costs nothing and is always right.
    """
    def resize(event):
        width = event.width - margin
        if width > 80 and label.cget("wraplength") != width:
            label.configure(wraplength=width)

    # add="+" so this does not displace a binding the parent already has;
    # CTkScrollableFrame keeps its scrollregion up to date this way.
    label.master.bind("<Configure>", resize, add="+")
    return label


def apply_theme(mode="system"):
    """Point CustomTkinter's defaults at this platform's metrics, then set
    the appearance mode. Call once, after the root window exists and
    before widgets are built."""
    T.pick_fonts()
    theme = ctk.ThemeManager.theme
    theme["CTkFont"] = {"family": T.font_family, "size": T.size, "weight": "normal"}
    for key in ("macOS", "Windows", "Linux"):
        if key in theme.get("CTkFont", {}):
            theme["CTkFont"][key] = {"family": T.font_family, "size": T.size}

    def merge(widget, **values):
        theme.setdefault(widget, {}).update(values)

    merge("CTkFrame",
          corner_radius=RADIUS_CARD, border_width=0,
          fg_color=list(COLOR_SURFACE), top_fg_color=list(COLOR_SURFACE),
          border_color=list(COLOR_BORDER))
    merge("CTkButton",
          corner_radius=RADIUS_CONTROL, border_width=0, height=CONTROL_H,
          fg_color=list(COLOR_ORANGE), hover_color=list(COLOR_ORANGE_HOVER),
          border_color=list(COLOR_BORDER), text_color=list(COLOR_ON_ACCENT),
          text_color_disabled=list(COLOR_TEXT_DIM))
    merge("CTkLabel", corner_radius=0, text_color=list(COLOR_TEXT))
    merge("CTkEntry",
          corner_radius=RADIUS_CONTROL, border_width=1, height=CONTROL_H,
          fg_color=list(COLOR_ROW), border_color=list(COLOR_BORDER),
          text_color=list(COLOR_TEXT), placeholder_text_color=list(COLOR_TEXT_DIM))
    merge("CTkOptionMenu",
          corner_radius=RADIUS_CONTROL, height=CONTROL_H,
          fg_color=list(COLOR_ROW), button_color=list(COLOR_ROW_HOVER),
          button_hover_color=list(COLOR_BORDER), text_color=list(COLOR_TEXT),
          text_color_disabled=list(COLOR_TEXT_DIM))
    merge("CTkComboBox",
          corner_radius=RADIUS_CONTROL, border_width=1, height=CONTROL_H,
          fg_color=list(COLOR_ROW), border_color=list(COLOR_BORDER),
          button_color=list(COLOR_BORDER), button_hover_color=list(COLOR_ROW_HOVER),
          text_color=list(COLOR_TEXT))
    merge("DropdownMenu",
          fg_color=list(COLOR_SURFACE), hover_color=list(COLOR_ROW_HOVER),
          text_color=list(COLOR_TEXT))
    merge("CTkCheckBox",
          corner_radius=4, border_width=2, checkmark_color=list(COLOR_ON_ACCENT),
          fg_color=list(COLOR_ORANGE), hover_color=list(COLOR_ORANGE_HOVER),
          border_color=list(COLOR_TEXT_DIM), text_color=list(COLOR_TEXT))
    merge("CTkSwitch",
          button_color=list(COLOR_SURFACE), button_hover_color=list(COLOR_SURFACE),
          progress_color=list(COLOR_ORANGE), fg_color=list(COLOR_ROW_HOVER),
          text_color=list(COLOR_TEXT))
    merge("CTkSlider",
          button_color=list(COLOR_ORANGE), button_hover_color=list(COLOR_ORANGE_HOVER),
          progress_color=list(COLOR_ORANGE), fg_color=list(COLOR_ROW_HOVER),
          button_corner_radius=1000)
    merge("CTkProgressBar",
          corner_radius=1000, border_width=0,
          fg_color=list(COLOR_ROW_HOVER), progress_color=list(COLOR_ORANGE))
    merge("CTkSegmentedButton",
          corner_radius=RADIUS_CONTROL,
          fg_color=list(COLOR_ROW), selected_color=list(COLOR_ORANGE),
          selected_hover_color=list(COLOR_ORANGE_HOVER),
          unselected_color=list(COLOR_ROW), unselected_hover_color=list(COLOR_ROW_HOVER),
          text_color=list(COLOR_TEXT), text_color_disabled=list(COLOR_TEXT_DIM))
    merge("CTkScrollbar",
          corner_radius=1000, border_spacing=4,
          fg_color="transparent", button_color=list(COLOR_BORDER),
          button_hover_color=list(COLOR_TEXT_DIM))
    merge("CTkTextbox",
          corner_radius=RADIUS_CONTROL, border_width=1,
          fg_color=list(COLOR_ROW), border_color=list(COLOR_BORDER),
          text_color=list(COLOR_TEXT), scrollbar_button_color=list(COLOR_BORDER))
    merge("CTk", fg_color=list(COLOR_BG))
    merge("CTkToplevel", fg_color=list(COLOR_BG))
    return set_appearance(mode)
