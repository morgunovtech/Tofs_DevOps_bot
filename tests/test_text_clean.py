"""Someone else's text cannot redraw the owner's screen: direction overrides
and zero-width marks are removed on the way to Telegram, and a link quoted
from a visitor or a page is not clickable."""

from reports.formatter import format_feedback
from utils.text import clean, defang, esc

BIDI = "evil.com \u202emoc.elgoog\u202c"


def test_clean_removes_direction_and_format_characters_but_keeps_text():
    assert clean(BIDI) == "evil.com moc.elgoog"
    assert clean("a\u200bb\u200fc\u2066d\u2069e\ufeff") == "abcde"
    assert clean("line one\nline two\tx") == "line one\nline two\tx"      # newline and tab stay
    assert clean("bell\x07 null\x00") == "bell null"
    assert clean(None) == "" and clean(12) == "12"


def test_esc_goes_through_clean():
    out = esc("<b>" + BIDI)
    assert "\u202e" not in out and "\u202c" not in out
    assert out.startswith("&lt;b&gt;evil.com")


def test_defang_breaks_links_but_not_words():
    assert defang("see https://evil.example/x?y=1") == "see hxxps://evil[.]example/x?y=1"
    assert defang("http://a.b.c.test/") == "hxxp://a[.]b[.]c[.]test/"
    assert defang("go to www.bad.test now") == "go to www[.]bad[.]test now"
    assert defang("version 2.0.0 of exe.png") == "version 2.0.0 of exe.png"   # no scheme — untouched
    assert defang("https://ok.test " + BIDI) == "hxxps://ok[.]test evil.com moc.elgoog"   # cleaned first


def test_widget_message_in_the_chat_has_no_live_link_and_no_bidi():
    text = format_feedback("https://site.test", "/page", "click https://evil.example/login \u202egnp.exe",
                           7, "203.0.113.9", verified=True)
    assert "hxxps://evil[.]example/login" in text and "https://evil.example" not in text
    assert "\u202e" not in text and "gnp.exe" in text
