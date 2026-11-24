"""Technical findability, the small pieces: a proof-of-ownership meta tag
left as a template or doubled (item 6), a site Yandex does not confirm as
the owner's, and the links of /llms.txt crawled like the page's (item 11)."""

from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import buttons

from monitors.links_checker import check_links, llms_links
from monitors.seo_checker import analyze_page
from reports import scheduler
from reports.formatter import format_links_report
from services import yandex_webmaster

PAGE = ('<html lang="ru"><head><title>Пример сайта для проверки</title>'
        '<meta name="description" content="{desc}">'
        '<meta property="og:title" content="x"><meta property="og:image" content="x">{tags}</head>'
        "<body><h1>Привет</h1></body></html>")
DESC = "Описание страницы достаточной длины, чтобы проверка его не трогала вовсе."


def codes(tags: str) -> list[str]:
    problems, _ = analyze_page(PAGE.format(desc=DESC, tags=tags), "https://ex.test/")
    return [p.code for p in problems]


def test_a_verification_tag_with_a_placeholder_or_two_codes():
    assert codes('<meta name="google-site-verification" content="YOUR_CODE">') == ["verify_placeholder"]
    assert codes('<meta name="yandex-verification" content="">') == ["verify_placeholder"]
    assert codes('<meta name="msvalidate.01" content="xxxxxxxxxx">') == ["verify_placeholder"]
    assert codes('<meta name="yandex-verification" content="0123456789abcdef">'
                 '<meta name="yandex-verification" content="fedcba9876543210">') == ["verify_conflict"]
    real = ('<meta name="google-site-verification" content="Abc_DEF-ghi123JKL456mno789PQR012stu345VWX">'
            '<meta name="yandex-verification" content="0123456789abcdef">'
            '<meta name="msvalidate.01" content="0123456789ABCDEF0123456789ABCDEF">')
    assert codes(real) == []
    problems, _ = analyze_page(PAGE.format(desc=DESC, tags='<meta name="google-site-verification" content="YOUR_CODE">'),
                               "https://ex.test/")
    assert problems[0].message == "главная: подтверждение Google с заглушкой «YOUR_CODE» — в Google сайт не подтверждён"
    # an inner page is not where the proof lives: not checked there
    problems, _ = analyze_page(PAGE.format(desc=DESC, tags='<meta name="google-site-verification" content="YOUR_CODE">'),
                               "https://ex.test/about")
    assert "verify_placeholder" not in [p.code for p in problems]


async def test_a_site_yandex_does_not_confirm_is_said_once(bot, db):
    sid = await db.activate_or_create_site("https://shop.ex.test")
    site = await db.get_site(sid)
    yandex_webmaster._remember_hosts([{"host_id": "https:shop.ex.test:443", "verified": False,
                                       "ascii_host_url": "https://shop.ex.test/"}], set())
    await scheduler._yandex_rights(site)
    await scheduler._yandex_rights(site)
    [msg] = bot.sent
    assert msg["text"].startswith("🟠 Яндекс.Вебмастер: shop.ex.test не подтверждён")
    assert msg["silent"] is True and "↗️ Вебмастер" in buttons(msg["reply_markup"])
    yandex_webmaster.last_hosts.clear()                       # no read: no word either way
    await scheduler._yandex_rights(site)
    yandex_webmaster._remember_hosts([], {"shop.ex.test"})
    await scheduler._yandex_rights(site)
    assert bot.sent[-1]["text"] == "✅ Яндекс.Вебмастер: shop.ex.test снова подтверждён."
    assert len(bot.sent) == 2


def test_the_links_of_an_llms_txt():
    text = ("# Пример\n> Сайт о примерах\n\n## Docs\n- [Главная](https://ex.test/)\n- [API](/docs/api.md): как звать API\n"
            "- старый адрес: https://old.ex.test/tool.\n")
    assert llms_links(text, "https://ex.test/llms.txt") == [
        "https://ex.test/", "https://ex.test/docs/api.md", "https://old.ex.test/tool"]


async def test_a_dead_link_in_llms_txt_is_crawled_and_named(db):
    async def page(_):
        return web.Response(text='<a href="/ok">ok</a>', content_type="text/html")

    async def llms(_):
        return web.Response(text="# Site\n- [Tool](/gone): the old tool\n- [Ok](/ok)\n", content_type="text/plain")

    async def ok(_):
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", page)
    app.router.add_get("/llms.txt", llms)
    app.router.add_get("/ok", ok)
    async with TestServer(app, host="localhost") as site:
        url, gone = str(site.make_url("/")), str(site.make_url("/gone"))
        r = await check_links(url)
    assert {c.url for c in r.broken} == {gone}
    assert r.from_llms == {gone}
    assert f"• {gone} — " in format_links_report(r) and " (в llms.txt)" in format_links_report(r)


async def test_an_spa_answering_html_for_llms_txt_has_none(db):
    async def page(_):
        return web.Response(text='<a href="/ok">ok</a>', content_type="text/html")

    app = web.Application()
    app.router.add_get("/{tail:.*}", page)
    async with TestServer(app, host="localhost") as site:
        r = await check_links(str(site.make_url("/")))
    assert r.from_llms == set() and not r.broken
