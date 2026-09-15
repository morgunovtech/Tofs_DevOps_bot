import json

from aiohttp import web
from aiohttp.test_utils import TestServer

from monitors.availability import check_availability, code_accepted, decode_body, request_options


def test_code_accepted_and_decode():
    assert code_accepted(302, None) and not code_accepted(404, None)
    assert code_accepted(401, [(200, 299), (401, 401)])
    assert not code_accepted(302, [(200, 299)])
    body = "Привет".encode("cp1251")
    assert decode_body(b'<meta charset="windows-1251">' + body, None).endswith("Привет")
    assert decode_body(body, "cp1251") == "Привет"


def test_request_options():
    method, headers, body = request_options({
        "http_method": "post", "http_headers": json.dumps({"X-Token": "1"}), "http_body": "{}"})
    assert method == "POST" and headers["X-Token"] == "1" and body == "{}"
    method, _, body = request_options({"http_method": "GET", "http_body": "ignored"})
    assert method == "GET" and body is None
    assert request_options({"http_method": "TRACE"})[0] == "GET"
    assert request_options({"http_headers": "not json"})[1]["User-Agent"]


async def _server(seen: list):
    async def handler(request: web.Request):
        seen.append({"method": request.method, "path": request.path,
                     "headers": dict(request.headers), "body": await request.text()})
        if request.path == "/500":
            return web.Response(status=500, text="boom")
        if request.path == "/login":
            return web.Response(status=401, text="auth")
        if request.path == "/broken":
            return web.Response(text="<html>Fatal error: db</html>", content_type="text/html")
        return web.Response(text="<html><body>Корзина работает</body></html>",
                            content_type="text/html")
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    return TestServer(app)


async def test_http_probe_status_keyword_and_custom_request(db):
    seen: list = []
    async with await _server(seen) as server:
        base = str(server.make_url("")).rstrip("/")

        r = await check_availability(f"{base}/ok", manage=False)
        assert r.ok and r.status_code == 200 and r.response_time_ms is not None

        r = await check_availability(f"{base}/500", manage=False)
        assert r.status == "error" and r.error == "HTTP 500"

        # Accepted codes: 401 is fine for a page behind auth.
        sid = await db.get_or_create_site(f"{base}/login")
        await db.update_site_settings(sid, accepted_codes="200-299,401")
        assert (await check_availability(f"{base}/login", manage=False)).ok

        # Keyword present / stop-phrase.
        sid = await db.get_or_create_site(f"{base}/ok")
        await db.update_site_settings(sid, keyword="корзина", keyword_mode="present")
        assert (await check_availability(f"{base}/ok", manage=False)).ok
        await db.update_site_settings(sid, keyword="нет такого", keyword_mode="present")
        r = await check_availability(f"{base}/ok", manage=False)
        assert r.status == "error" and r.keyword_failed and "нет фразы" in r.error
        sid = await db.get_or_create_site(f"{base}/broken")
        await db.update_site_settings(sid, keyword="fatal error", keyword_mode="absent")
        r = await check_availability(f"{base}/broken", manage=False)
        assert r.status == "error" and r.keyword_failed

        # Custom method, headers and body reach the server.
        sid = await db.get_or_create_site(f"{base}/api")
        await db.update_site_settings(sid, http_method="POST", http_body='{"ping":1}',
                                      http_headers=json.dumps({"Authorization": "Bearer t"}))
        assert (await check_availability(f"{base}/api", manage=False)).ok
        last = seen[-1]
        assert last["method"] == "POST" and last["body"] == '{"ping":1}'
        assert last["headers"]["Authorization"] == "Bearer t"


async def test_incident_opens_after_threshold_and_recovers(db):
    seen: list = []
    async with await _server(seen) as server:
        url = str(server.make_url("/500")).rstrip("/")
        r1 = await check_availability(url)          # 1st failure: no incident yet
        assert not r1.incident_new
        r2 = await check_availability(url)          # 2nd consecutive failure
        assert r2.incident_new and r2.incident_id
        r3 = await check_availability(url)
        assert not r3.incident_new                  # already open
        assert len(await db.get_active_incidents()) == 1
    async with await _server(seen) as server:
        pass
    # Site comes back on a different server instance → recovery.
    async with await _server(seen) as server:
        ok_url = str(server.make_url("/ok")).rstrip("/")
        sid = await db.get_or_create_site(ok_url)
        await db.save_incident(sid, "availability", "Site down: HTTP 500", "critical")
        r = await check_availability(ok_url)
        assert r.recovered and r.resolved_incident["message"].startswith("Site down")


async def test_tcp_and_ping_inputs(db):
    r = await check_availability("tcp://localhost", manage=False)
    assert r.status == "error" and "порт" in r.error
    r = await check_availability("tcp://127.0.0.1:1", manage=False)   # nothing listens on :1
    assert r.status == "error"
