from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from techspire import image_generator as ig
from techspire.enums import Category
from techspire.exceptions import ImageGenerationError
from techspire.image_generator import CardRenderer, FontSet, image_path_for

SHORT = "Nvidia Unveils Faster AI Chips"
TYPICAL = "Google Brings Powerful New Gemini Features to Developers Worldwide"
LONG = "Ransomware Gang Exploits Unpatched VPN Flaw to Breach Hospitals Across Three Countries in One Week"
LONG_WORD = "Supercalifragilisticexpialidociousinfrastructureplatformnamethatneverends Launches Globally Today"
EXTREME = " ".join(["Extraordinarily"] * 40)


@pytest.fixture(scope="module")
def renderer() -> CardRenderer:
    return CardRenderer()


def _inside(box, area) -> bool:
    return box[0] >= area[0] and box[1] >= area[1] and box[2] <= area[2] and box[3] <= area[3]


def test_bundled_fonts_are_used():
    fonts = FontSet.resolve()
    assert fonts.headline.name == "Montserrat-ExtraBold.ttf"
    assert all(path is not None for path in (fonts.brand, fonts.label, fonts.small))


def test_canvas_size_mode_and_background(renderer):
    image, _ = renderer.render(Category.TECHNOLOGY, TYPICAL)
    assert image.size == (1200, 630)
    assert image.mode == "RGB"
    for xy in [(2, 2), (1197, 2), (2, 627), (1197, 627), (30, 315), (1170, 315), (600, 125)]:
        assert image.getpixel(xy) == (20, 24, 33)


@pytest.mark.parametrize("headline", [SHORT, TYPICAL, LONG, LONG_WORD, EXTREME])
def test_headline_stays_inside_the_safe_area(renderer, headline):
    _, layout = renderer.render(Category.FINANCE, headline)
    for box in layout.headline_boxes:
        assert _inside(box, layout.headline_area), (box, layout.headline_area)
    assert ig.HEADLINE_MIN_SIZE <= layout.headline_font_size <= ig.HEADLINE_MAX_SIZE
    assert 1 <= len(layout.headline_lines) <= 4


def test_short_headline_is_large_and_compact(renderer):
    _, layout = renderer.render(Category.BUSINESS, SHORT)
    assert layout.headline_font_size == ig.HEADLINE_MAX_SIZE
    assert len(layout.headline_lines) <= 2
    assert not layout.truncated


def test_long_headline_wraps_to_several_lines_and_shrinks(renderer):
    _, short = renderer.render(Category.FINANCE, SHORT)
    _, long = renderer.render(Category.FINANCE, LONG)
    assert len(long.headline_lines) >= 3
    assert long.headline_font_size < short.headline_font_size
    assert " ".join(long.headline_lines) == LONG  # nothing lost or reordered


def test_lines_are_balanced(renderer):
    _, layout = renderer.render(Category.TECHNOLOGY, TYPICAL)
    widths = [b[2] - b[0] for b in layout.headline_boxes]
    assert min(widths) / max(widths) > 0.6  # no stubby last line


@pytest.mark.parametrize("headline", [
    "Pixelworks Unveils 8K OLED Laptop Display With a Blazing 240Hz Refresh Rate",
    "Quantacore Raises $40 Million to Scale Production of Photonic AI Chips",
    "Google Brings Powerful New Gemini Features to Developers Worldwide",
])
def test_no_dangling_words_or_split_amounts(renderer, headline):
    _, layout = renderer.render(Category.ENTREPRENEURSHIP, headline)
    lines = layout.headline_lines
    for current, following in zip(lines, lines[1:]):
        last, first = current.split()[-1], following.split()[0]
        assert last.lower() not in {"a", "an", "the", "to", "of", "with", "for"}, lines
        assert not (last.startswith("$") and first.lower() == "million"), lines


def test_very_long_word_is_hyphenated_not_clipped(renderer):
    _, layout = renderer.render(Category.CAREER_DEVELOPMENT, LONG_WORD)
    assert layout.headline_font_size == ig.HEADLINE_MIN_SIZE
    assert layout.headline_lines[0].endswith("-")
    assert "".join(layout.headline_lines).replace("-", "").replace(" ", "") == LONG_WORD.replace(" ", "")


def test_overflow_is_truncated_at_minimum_size(renderer):
    _, layout = renderer.render(Category.ENTREPRENEURSHIP, EXTREME)
    assert layout.truncated
    assert layout.headline_font_size == ig.HEADLINE_MIN_SIZE
    assert layout.headline_lines[-1].endswith("…")
    assert len(layout.headline_lines) <= 4


def test_lines_do_not_overlap_and_are_centered(renderer):
    _, layout = renderer.render(Category.FINANCE, LONG)
    boxes = layout.headline_boxes
    for upper, lower in zip(boxes, boxes[1:]):
        assert upper[3] <= lower[1] + 2  # consecutive lines don't collide
    for box in boxes:
        assert abs((box[0] + box[2]) / 2 - 600) <= 3


@pytest.mark.parametrize("category", list(Category))
def test_category_badge_is_rendered_inside_its_pill(renderer, category):
    image, layout = renderer.render(category, TYPICAL)
    assert _inside(layout.pill_text_box, layout.pill_box)
    assert layout.pill_box[0] >= ig.MARGIN_X and layout.pill_box[2] <= 1200 - ig.MARGIN_X
    assert abs((layout.pill_box[0] + layout.pill_box[2]) / 2 - 600) <= 1
    assert layout.pill_box[3] < min(b[1] for b in layout.headline_boxes)  # badge sits above the headline
    # accent-coloured text pixels exist inside the pill
    x0, y0, x1, y1 = layout.pill_text_box
    region = image.crop((x0, y0, x1, y1)).getdata()
    assert any(abs(p[0] - ig.ACCENT[0]) < 30 and abs(p[2] - ig.ACCENT[2]) < 30 for p in region)


def test_brand_is_inside_top_left_margin(renderer):
    _, layout = renderer.render(Category.TECHNOLOGY, TYPICAL)
    x0, y0, x1, y1 = layout.brand_box
    assert x0 >= ig.MARGIN_X and y0 >= 40 and y1 < ig.HEADER_DIVIDER_Y and x1 < 600


def test_render_to_file_writes_a_valid_jpeg(renderer, tmp_path):
    path = tmp_path / "images" / "2026-09-28_0123456789abcdef.jpg"
    renderer.render_to_file(Category.TECHNOLOGY, TYPICAL, path)
    with Image.open(path) as image:
        assert image.format == "JPEG" and image.size == (1200, 630)
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temp files left behind


def test_optional_logo_is_drawn_next_to_the_wordmark(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (80, 80), (255, 0, 0, 255)).save(logo)
    _, plain = CardRenderer().render(Category.TECHNOLOGY, TYPICAL)
    image, with_logo = CardRenderer(logo_path=logo).render(Category.TECHNOLOGY, TYPICAL)
    assert image.getpixel((ig.MARGIN_X + 10, 69))[:3] == (255, 0, 0)
    assert with_logo.brand_box[2] > plain.brand_box[2]


def test_only_safe_logo_formats_and_sizes_are_used(tmp_path):
    gif = tmp_path / "logo.gif"
    Image.new("RGB", (40, 40), (255, 0, 0)).save(gif)
    _, plain = CardRenderer().render(Category.TECHNOLOGY, TYPICAL)
    _, with_gif = CardRenderer(logo_path=gif).render(Category.TECHNOLOGY, TYPICAL)
    assert with_gif.brand_box == plain.brand_box  # refused format: wordmark only
    huge = tmp_path / "huge.png"
    Image.new("L", (5000, 4000)).save(huge)
    _, with_huge = CardRenderer(logo_path=huge).render(Category.TECHNOLOGY, TYPICAL)
    assert with_huge.brand_box == plain.brand_box


def test_broken_logo_falls_back_to_wordmark(tmp_path):
    bad = tmp_path / "logo.png"
    bad.write_bytes(b"not an image")
    image, _ = CardRenderer(logo_path=bad).render(Category.TECHNOLOGY, TYPICAL)
    assert image.size == (1200, 630)


def test_missing_configured_font_falls_back(tmp_path):
    fonts = FontSet.resolve(headline=tmp_path / "missing.ttf")
    assert fonts.headline is not None and fonts.headline.name == "Montserrat-ExtraBold.ttf"


def test_empty_headline_is_refused(renderer):
    with pytest.raises(ImageGenerationError):
        renderer.render(Category.TECHNOLOGY, "   ")


def test_image_path_is_deterministic_and_safe(tmp_path):
    when = datetime(2026, 9, 28, 7, 0, tzinfo=UTC)
    digest = "a" * 64
    first = image_path_for(tmp_path, digest, when)
    assert first == image_path_for(tmp_path, digest, when)
    assert first.name == "2026-09-28_aaaaaaaaaaaaaaaa.jpg"
    assert first.parent == tmp_path.resolve()


@pytest.mark.parametrize("unsafe", ["../../etc/passwd", "..\\..\\windows", "abc", "ZZZZZZZZZZZZZZZZ", "a/b" * 10,
                                    "<script>.jpg"])
def test_unsafe_identifiers_never_become_file_names(tmp_path, unsafe):
    with pytest.raises(ImageGenerationError):
        image_path_for(tmp_path, unsafe, datetime(2026, 9, 28, tzinfo=UTC))


def test_title_never_influences_the_file_name(tmp_path):
    path = image_path_for(Path(tmp_path), "0" * 64, datetime(2026, 9, 28, tzinfo=UTC))
    assert path.name == "2026-09-28_0000000000000000.jpg"


def test_tagline_is_configurable():
    _, default = CardRenderer().render(Category.TECHNOLOGY, TYPICAL)
    image, custom = CardRenderer(tagline="YOUR DAILY BRIEF").render(Category.TECHNOLOGY, TYPICAL)
    assert image.size == (1200, 630)
    assert default.brand_box == custom.brand_box  # the wordmark does not move
