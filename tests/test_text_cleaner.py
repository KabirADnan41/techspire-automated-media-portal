from techspire.text_cleaner import (
    FINGERPRINT_MIN_WORDS,
    clean_summary,
    clean_title,
    html_to_text,
    title_fingerprint,
    truncate,
)


def test_strips_tags_scripts_and_styles():
    raw = "<p>Hello <b>world</b></p><script>alert('x')</script><style>p{color:red}</style><iframe src=x></iframe>"
    assert html_to_text(raw) == "Hello world"


def test_decodes_entities_including_double_encoded():
    assert html_to_text("Tom &amp; Jerry&#8217;s &quot;app&quot;") == "Tom & Jerry’s \"app\""
    assert html_to_text("It&amp;#8217;s here") == "It’s here"


def test_escaped_markup_is_stripped_too():
    assert html_to_text("&lt;p&gt;Hello &lt;b&gt;there&lt;/b&gt;&lt;/p&gt;") == "Hello there"


def test_whitespace_nbsp_zero_width_and_control_characters():
    raw = "Line one\n\n\tline​ two\x07  end"
    assert html_to_text(raw) == "Line one line two end"


def test_invisible_bidi_and_zero_width_characters_are_removed():
    raw = "Safe‮ txt.exe ⁦hidden⁩ zero​width﻿"
    assert html_to_text(raw) == "Safe txt.exe hidden zerowidth"


def test_untrusted_input_is_cut_before_parsing():
    huge = "<p>" + "x " * 500_000 + "</p>"
    assert len(html_to_text(huge)) <= 20_000


def test_nfc_keeps_symbols_for_display():
    assert html_to_text("Brand™ launch… café") == "Brand™ launch… café"


def test_truncates_at_word_boundary_with_ellipsis():
    text = "word " * 400
    out = truncate(text.strip(), 100)
    assert len(out) <= 100
    assert out.endswith("…")
    assert not out[:-1].endswith(" ")


def test_summary_is_bounded_and_boilerplate_removed():
    raw = "<p>" + "Big news today. " * 200 + "</p>"
    assert len(clean_summary(raw)) <= 1200
    assert clean_summary("Chips got faster. The post Chips got faster appeared first on Example Blog.") == \
        "Chips got faster."
    assert clean_summary("Chips got faster. Continue reading →") == "Chips got faster."


def test_empty_inputs():
    assert clean_title(None) == ""
    assert clean_summary("") == ""
    assert html_to_text("<p></p>") == ""


def test_fingerprint_ignores_case_and_punctuation():
    a = title_fingerprint("Nvidia unveils new AI chips at annual developer event")
    b = title_fingerprint("NVIDIA Unveils New AI Chips — At Annual Developer Event!")
    assert a is not None and a == b


def test_fingerprint_is_conservative():
    assert title_fingerprint("Week in review") is None  # too short/generic to compare
    assert len("Week in review".split()) < FINGERPRINT_MIN_WORDS
    assert title_fingerprint("Nvidia unveils new AI chips at annual developer event") != \
        title_fingerprint("AMD unveils new AI chips at annual developer event")
