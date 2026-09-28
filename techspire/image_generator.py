"""1200x630 Techspire news card rendered with Pillow.

Layout is computed from real pixel measurements (ImageDraw.textbbox), never from
character counts: the headline is wrapped into balanced lines and shrunk until the
whole block fits the safe area; as a last resort long words are hyphen-broken and
the final line is ellipsized, so text can never leave the card.
"""

from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from techspire.config import PROJECT_ROOT, Settings
from techspire.enums import Category
from techspire.exceptions import ImageGenerationError
from techspire.fileio import atomic_write_bytes

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1200, 630
BACKGROUND = (20, 24, 33)  # required brand background RGB(20, 24, 33)
ACCENT = (56, 189, 248)
TEXT_PRIMARY = (248, 250, 252)
TEXT_MUTED = (148, 163, 184)
DIVIDER = (42, 50, 66)

BRAND_TEXT = "TECHSPIRE OFFICIAL"
LOGO_FORMATS = ("PNG", "JPEG", "WEBP")  # never EPS/PS (Ghostscript) or other exotic decoders
MAX_LOGO_PIXELS = 16_000_000
DEFAULT_TAGLINE = "BITESIZE NEWS"
FOOTER_TEXT = "FULL STORY: LINK IN THE COMMENTS"

MARGIN_X = 72  # outer safe margin for all elements
HEADLINE_MARGIN_X = 90  # narrower measure for the headline
BRAND_TOP = 52
HEADER_DIVIDER_Y = 112
FOOTER_DIVIDER_Y = 548
CONTENT_TOP, CONTENT_BOTTOM = 136, 526
PILL_GAP = 30
PILL_HEIGHT = 46
HEADLINE_AREA = (HEADLINE_MARGIN_X, CONTENT_TOP, WIDTH - HEADLINE_MARGIN_X, CONTENT_BOTTOM)

HEADLINE_MAX_SIZE = 76
HEADLINE_COMFORT_MIN_SIZE = 50  # pass 1: at most 3 lines, not smaller than this
HEADLINE_MIN_SIZE = 34  # pass 2: at most 4 lines, down to this
LINE_SPACING = 1.16
BAD_BREAK_PENALTY = WIDTH  # larger than any line: a clean layout always wins when one fits
BAD_BREAK_SHRINK = 6  # try up to 6 px smaller to avoid a bad break
_DANGLING_WORDS = frozenset({"a", "an", "the", "to", "of", "and", "or", "for", "in", "on", "at", "by",
                             "with", "as", "from", "into", "its"})
_MAGNITUDES = frozenset({"thousand", "million", "billion", "trillion", "percent"})
_NUMBER_TOKEN = re.compile(r"[$€£]?\d[\d.,]*")

FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"
_WIN_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
_SYSTEM_FALLBACKS = (
    _WIN_FONTS / "segoeuib.ttf",
    _WIN_FONTS / "arialbd.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
)
_HEX = re.compile(r"[0-9a-f]{16,64}")

Box = tuple[int, int, int, int]


def _candidates(bundled: str, explicit: Path | None) -> tuple[Path, ...]:
    head = (explicit,) if explicit else ()
    return (*head, FONTS_DIR / bundled, _WIN_FONTS / bundled, *_SYSTEM_FALLBACKS)


@dataclass(frozen=True)
class FontSet:
    """Resolved font files for each text role (None means Pillow's built-in font)."""

    headline: Path | None
    brand: Path | None
    label: Path | None
    small: Path | None

    @classmethod
    def from_settings(cls, settings: Settings) -> FontSet:
        return cls.resolve(settings.font_headline_path, settings.font_brand_path, settings.font_label_path)

    @classmethod
    def resolve(cls, headline: Path | None = None, brand: Path | None = None,
                label: Path | None = None) -> FontSet:
        for role, explicit in (("headline", headline), ("brand", brand), ("label", label)):
            if explicit and not explicit.is_file():
                log.warning("Configured %s font %s not found; using fallback fonts", role, explicit)
        return cls(
            headline=_first_existing(_candidates("Montserrat-ExtraBold.ttf", headline)),
            brand=_first_existing(_candidates("Montserrat-Bold.ttf", brand)),
            label=_first_existing(_candidates("Montserrat-SemiBold.ttf", label)),
            small=_first_existing(_candidates("Montserrat-Medium.ttf", label)),
        )

    def describe(self) -> dict[str, str]:
        return {role: str(path) if path else "Pillow built-in font (fallback)"
                for role, path in (("headline", self.headline), ("brand", self.brand),
                                   ("label", self.label), ("small", self.small))}


def _first_existing(paths: tuple[Path, ...]) -> Path | None:
    return next((p for p in paths if p and p.is_file()), None)


_MEASURE = ImageDraw.Draw(Image.new("RGB", (1, 1)))


@lru_cache(maxsize=16384)
def _text_width(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str) -> int:
    """Rendered width in pixels (fonts are cached objects, so repeated fits reuse measurements)."""
    left, _, right, _ = _MEASURE.textbbox((0, 0), text, font=font, anchor="ls")
    return int(right - left)


@lru_cache(maxsize=256)
def load_font(path: Path | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path is not None:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            log.warning("Could not load font %s; using Pillow's built-in font", path)
    return ImageFont.load_default(size=size)


@dataclass(frozen=True)
class CardLayout:
    """Geometry of a rendered card (used by tests and for debugging)."""

    headline_font_size: int
    headline_lines: tuple[str, ...]
    headline_boxes: tuple[Box, ...]
    headline_area: Box
    pill_box: Box
    pill_text_box: Box
    brand_box: Box
    truncated: bool


@dataclass(frozen=True)
class _Fit:
    size: int
    lines: tuple[str, ...]
    truncated: bool


class CardRenderer:
    def __init__(self, fonts: FontSet | None = None, logo_path: Path | None = None,
                 tagline: str = DEFAULT_TAGLINE) -> None:
        self.fonts = fonts or FontSet.resolve()
        self.logo_path = logo_path
        self.tagline = tagline
        self._base: tuple[Image.Image, Box] | None = None  # header + footer, drawn once per renderer

    # ------------------------------------------------------------- public
    def render(self, category: Category, headline: str) -> tuple[Image.Image, CardLayout]:
        headline = " ".join(headline.split())
        if not headline:
            raise ImageGenerationError("Cannot render a card without a headline.")
        image, brand_box = self._base_card()
        draw = ImageDraw.Draw(image)

        label_font = load_font(self.fonts.label, 22)
        label = category.value
        label_w = self._tracked_width(label, label_font, 2)
        pill_w = label_w + 2 * 24
        fit = self._fit_headline(headline, CONTENT_BOTTOM - (CONTENT_TOP + PILL_HEIGHT + PILL_GAP))
        font = load_font(self.fonts.headline, fit.size)
        pitch = round(fit.size * LINE_SPACING)
        block_top, block_h = self._block_extent(fit.lines, font, pitch)

        # Center pill + headline as one group inside the content area.
        group_h = PILL_HEIGHT + PILL_GAP + block_h
        group_top = CONTENT_TOP + max(0, (CONTENT_BOTTOM - CONTENT_TOP - group_h) // 2)
        pill_x0 = (WIDTH - pill_w) // 2
        pill_box = (pill_x0, group_top, pill_x0 + pill_w, group_top + PILL_HEIGHT)
        pill_text_box = self._draw_pill(draw, pill_box, label, label_font, label_w)

        shift = group_top + PILL_HEIGHT + PILL_GAP - block_top
        boxes = []
        for i, line in enumerate(fit.lines):
            baseline = i * pitch + shift
            draw.text((WIDTH // 2, baseline), line, font=font, fill=TEXT_PRIMARY, anchor="ms")
            boxes.append(self._ink_box(line, font, baseline))

        layout = CardLayout(
            headline_font_size=fit.size,
            headline_lines=fit.lines,
            headline_boxes=tuple(boxes),
            headline_area=HEADLINE_AREA,
            pill_box=pill_box,
            pill_text_box=pill_text_box,
            brand_box=brand_box,
            truncated=fit.truncated,
        )
        return image, layout

    def _base_card(self) -> tuple[Image.Image, Box]:
        """A fresh copy of the background with the fixed header and footer already drawn."""
        if self._base is None:
            image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
            draw = ImageDraw.Draw(image)
            brand_box = self._draw_header(image, draw)
            self._draw_footer(draw)
            self._base = (image, brand_box)
        image, brand_box = self._base
        return image.copy(), brand_box

    def render_to_file(self, category: Category, headline: str, path: Path) -> CardLayout:
        image, layout = self.render(category, headline)
        save_jpeg(image, path)
        return layout

    # ------------------------------------------------------------ headline fit
    def _fit_headline(self, headline: str, max_height: int) -> _Fit:
        max_width = WIDTH - 2 * HEADLINE_MARGIN_X
        words = _glue_amounts(headline.split())
        for max_lines, smallest in ((3, HEADLINE_COMFORT_MIN_SIZE), (4, HEADLINE_MIN_SIZE)):
            for size in range(HEADLINE_MAX_SIZE, smallest - 1, -2):
                lines = self._wrap_to_fit(words, size, max_width, max_height, max_lines)
                if lines is None:
                    continue
                if _has_bad_break(lines):
                    # A slightly smaller size may avoid "... with a / Blazing" or "$40 / Million".
                    for smaller in range(size - 2, max(smallest, size - BAD_BREAK_SHRINK) - 1, -2):
                        cleaner = self._wrap_to_fit(words, smaller, max_width, max_height, max_lines)
                        if cleaner is not None and not _has_bad_break(cleaner):
                            return _Fit(smaller, tuple(cleaner), truncated=False)
                return _Fit(size, tuple(lines), truncated=False)
        # Overflow protection: smallest size, hyphen-break long words, ellipsize.
        size = HEADLINE_MIN_SIZE
        font = load_font(self.fonts.headline, size)
        lines = self._greedy_wrap(self._break_long_words(words, font, max_width), font, max_width)
        max_lines = 4
        pitch = round(size * LINE_SPACING)
        while max_lines > 1 and self._block_extent(lines[:max_lines], font, pitch)[1] > max_height:
            max_lines -= 1
        truncated = len(lines) > max_lines
        lines = lines[:max_lines]
        if truncated:
            lines[-1] = self._ellipsize(lines[-1], font, max_width)
        log.warning("Headline did not fit comfortably; rendered at %dpx%s", size, " (truncated)" if truncated else "")
        return _Fit(size, tuple(lines), truncated=truncated)

    def _wrap_to_fit(self, words: list[str], size: int, max_width: int, max_height: int,
                     max_lines: int) -> list[str] | None:
        font = load_font(self.fonts.headline, size)
        lines = self._balanced_wrap(words, font, max_width, max_lines)
        if lines and self._block_extent(lines, font, round(size * LINE_SPACING))[1] <= max_height:
            return lines
        return None

    def _balanced_wrap(self, words: list[str], font: ImageFont.ImageFont, max_width: int,
                       max_lines: int) -> list[str] | None:
        """Fewest lines that fit, then the break points that minimize the widest line."""
        if any(self._width(w, font) > max_width for w in words):
            return None
        line_count = len(self._greedy_wrap(words, font, max_width))
        if line_count > max_lines:
            return None
        n = len(words)
        # Width of every word span that fits on one line (longer spans only get wider, so stop early).
        widths: dict[tuple[int, int], int] = {}
        for i in range(n):
            for j in range(i + 1, n + 1):
                width = self._width(" ".join(words[i:j]), font)
                if width > max_width:
                    break
                widths[(i, j)] = width
        # best[k][j]: minimal widest line covering words[:j] with k lines, and its split.
        # A bad break (dangling "a"/"to", or "$40 / Million") costs more than any clean layout,
        # so it is only used when no clean layout fits.
        inf = float("inf")
        best: list[list[tuple[float, int]]] = [[(inf, -1)] * (n + 1) for _ in range(line_count + 1)]
        best[0][0] = (0.0, -1)
        for k in range(1, line_count + 1):
            for j in range(1, n + 1):
                penalty = BAD_BREAK_PENALTY if j < n and _is_bad_break(words[j - 1]) else 0
                for i in range(k - 1, j):
                    width = widths.get((i, j))
                    if width is None or best[k - 1][i][0] == inf:
                        continue
                    candidate = max(best[k - 1][i][0], width + penalty)
                    if candidate < best[k][j][0]:
                        best[k][j] = (candidate, i)
        # The greedy split is itself a valid layout, so best[line_count][n] is always found.
        lines, j = [], n
        for k in range(line_count, 0, -1):
            i = best[k][j][1]
            lines.append(" ".join(words[i:j]))
            j = i
        return lines[::-1]

    def _greedy_wrap(self, words: list[str], font: ImageFont.ImageFont, max_width: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if not current or self._width(candidate, font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def _break_long_words(self, words: list[str], font: ImageFont.ImageFont, max_width: int) -> list[str]:
        out: list[str] = []
        for word in words:
            while self._width(word, font) > max_width:
                cut = len(word) - 1
                while cut > 1 and self._width(word[:cut] + "-", font) > max_width:
                    cut -= 1
                out.append(word[:cut] + "-")
                word = word[cut:]
            out.append(word)
        return out

    def _ellipsize(self, line: str, font: ImageFont.ImageFont, max_width: int) -> str:
        text = line.rstrip(" ,;:-")
        while text and self._width(text + "…", font) > max_width:
            text = text[:-1].rstrip(" ,;:-")
        return text + "…"

    # --------------------------------------------------------------- drawing
    def _draw_header(self, image: Image.Image, draw: ImageDraw.ImageDraw) -> Box:
        brand_font = load_font(self.fonts.brand, 26)
        x = MARGIN_X
        center_y = BRAND_TOP + 17
        logo = self._load_logo(40)
        if logo is not None:
            image.paste(logo, (x, center_y - logo.height // 2), logo)
            x += logo.width + 16
        else:
            draw.rectangle((x, center_y - 15, x + 5, center_y + 15), fill=ACCENT)
            x += 20
        text_box = self._draw_tracked(draw, (x, center_y), BRAND_TEXT, brand_font, 4, TEXT_PRIMARY)
        small = load_font(self.fonts.small, 17)
        tag_w = self._tracked_width(self.tagline, small, 3)
        self._draw_tracked(draw, (WIDTH - MARGIN_X - tag_w, center_y), self.tagline, small, 3, TEXT_MUTED)
        draw.line((MARGIN_X, HEADER_DIVIDER_Y, WIDTH - MARGIN_X, HEADER_DIVIDER_Y), fill=DIVIDER, width=2)
        return (MARGIN_X, text_box[1], text_box[2], text_box[3])

    def _draw_footer(self, draw: ImageDraw.ImageDraw) -> None:
        draw.line((MARGIN_X, FOOTER_DIVIDER_Y, WIDTH - MARGIN_X, FOOTER_DIVIDER_Y), fill=DIVIDER, width=2)
        small = load_font(self.fonts.small, 17)
        center_y = (FOOTER_DIVIDER_Y + HEIGHT) // 2 - 2
        draw.rectangle((MARGIN_X, center_y - 2, MARGIN_X + 28, center_y + 2), fill=ACCENT)
        self._draw_tracked(draw, (MARGIN_X + 44, center_y), FOOTER_TEXT, small, 3, TEXT_MUTED)

    def _draw_pill(self, draw: ImageDraw.ImageDraw, box: Box, label: str, font: ImageFont.ImageFont,
                   text_w: int) -> Box:
        fill = tuple(round(b + (a - b) * 0.14) for a, b in zip(ACCENT, BACKGROUND, strict=True))
        draw.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, fill=fill, outline=ACCENT, width=2)
        x = (box[0] + box[2] - text_w) // 2
        return self._draw_tracked(draw, (x, (box[1] + box[3]) // 2), label, font, 2, ACCENT)

    def _draw_tracked(self, draw: ImageDraw.ImageDraw, origin: tuple[int, int], text: str,
                      font: ImageFont.ImageFont, tracking: int, fill: tuple[int, int, int]) -> Box:
        """Draw letter-spaced text vertically centered on origin[1] ("lm" anchor). Returns its ink box."""
        x, y = origin
        x0 = y0 = 10**6
        x1 = y1 = -(10**6)
        for ch in text:
            if ch != " ":
                draw.text((x, y), ch, font=font, fill=fill, anchor="lm")
                bx0, by0, bx1, by1 = draw.textbbox((x, y), ch, font=font, anchor="lm")
                x0, y0, x1, y1 = min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1)
            x += round(font.getlength(ch)) + tracking
        return (int(x0), int(y0), int(x1), int(y1))

    def _load_logo(self, height: int) -> Image.Image | None:
        if not self.logo_path:
            return None
        try:
            with Image.open(self.logo_path, formats=LOGO_FORMATS) as raw:
                if raw.width * raw.height > MAX_LOGO_PIXELS:
                    raise ValueError(f"logo is larger than {MAX_LOGO_PIXELS:,} pixels")
                logo = raw.convert("RGBA")
            ratio = height / logo.height
            return logo.resize((max(1, round(logo.width * ratio)), height), Image.Resampling.LANCZOS)
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            log.warning("Brand logo %s could not be used (%s); drawing the text wordmark only", self.logo_path, exc)
            return None

    # ------------------------------------------------------------ measuring
    @staticmethod
    def _width(text: str, font: ImageFont.ImageFont) -> int:
        return _text_width(font, text)

    @staticmethod
    def _ink_box(text: str, font: ImageFont.ImageFont, baseline: int) -> Box:
        return tuple(int(v) for v in _MEASURE.textbbox((WIDTH // 2, baseline), text, font=font, anchor="ms"))

    def _block_extent(self, lines: tuple[str, ...] | list[str], font: ImageFont.ImageFont,
                      pitch: int) -> tuple[int, int]:
        """(top, height) of the inked text block with baselines `pitch` apart, first baseline at 0."""
        boxes = [self._ink_box(line, font, i * pitch) for i, line in enumerate(lines)]
        top = min(b[1] for b in boxes)
        return top, max(b[3] for b in boxes) - top

    @staticmethod
    def _tracked_width(text: str, font: ImageFont.ImageFont, tracking: int) -> int:
        return sum(round(font.getlength(ch)) + tracking for ch in text) - tracking


def _glue_amounts(words: list[str]) -> list[str]:
    """Keep amounts together: "$40", "Million" -> "$40 Million" (never split across lines)."""
    glued: list[str] = []
    for word in words:
        if glued and _NUMBER_TOKEN.fullmatch(glued[-1]) and word.lower().strip(".,;:") in _MAGNITUDES:
            glued[-1] = f"{glued[-1]} {word}"
        else:
            glued.append(word)
    return glued


def _is_bad_break(last_word: str) -> bool:
    """True when a line ending in `last_word` leaves a dangling connector ("a", "to", ...)."""
    return last_word.lower() in _DANGLING_WORDS


def _has_bad_break(lines: list[str]) -> bool:
    return any(_is_bad_break(line.split()[-1]) for line in lines[:-1])


def image_path_for(images_dir: Path, url_hash: str, when: datetime) -> Path:
    """Deterministic, collision-safe file name built only from trusted parts
    (date + URL hash), never from article titles."""
    if not _HEX.fullmatch(url_hash):
        raise ImageGenerationError("Refusing to build an image path from a non-hex identifier.")
    path = (images_dir / f"{when:%Y-%m-%d}_{url_hash[:16]}.jpg").resolve()
    if path.parent != images_dir.resolve():
        raise ImageGenerationError("Image path escaped the images directory.")
    return path


def save_jpeg(image: Image.Image, path: Path) -> None:
    """Encode in memory, then write atomically so a crash never leaves a half-written card."""
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92, subsampling=0, optimize=True, progressive=True)
    try:
        atomic_write_bytes(path, buffer.getvalue())
    except OSError as exc:
        raise ImageGenerationError(f"Could not write image {path}: {exc}") from exc
