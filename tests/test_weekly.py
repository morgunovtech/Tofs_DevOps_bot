from reports.scheduler import pack_blocks
from reports.weekly import render_ascii_chart


def test_ascii_chart_blocks():
    series = {"ex.com": [{"day": "2026-09-08", "avg_ms": 100}, {"day": "2026-09-09", "avg_ms": 200}],
              "empty": []}
    blocks = render_ascii_chart(series)
    assert len(blocks) == 1 and blocks[0].startswith("<pre>ex.com")
    assert "08.09" in blocks[0] and "0.20 с" in blocks[0]
    assert render_ascii_chart({"x": [{"day": "2026-09-08", "avg_ms": None}]}) == []


def test_pack_blocks():
    blocks = ["a" * 300, "b" * 300, "c" * 300]
    assert pack_blocks(blocks, limit=650) == ["a" * 300 + "\n\n" + "b" * 300, "c" * 300]
    assert pack_blocks([], limit=10) == []
