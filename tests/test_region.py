from handlers import sites
from monitors import region
from reports import scheduler
from services import integrations, sitestatus


def test_country_for_timezone():
    assert region.country_for_timezone("Europe/Moscow") == "ru"
    assert region.country_for_timezone("Asia/Novosibirsk") == "ru"
    assert region.country_for_timezone("Europe/Berlin") == "de"
    assert region.country_for_timezone("America/Chicago") == "us"
    assert region.country_for_timezone("Asia/Almaty") == "kz"
    assert region.country_for_timezone("Mars/Olympus") == "de"


def test_parse_ms_and_node_name():
    assert region.parse_ms([[1, 0.123, "OK", "200", "1.2.3.4"]]) == 123
    assert region.parse_ms([[0, None, "Timeout"]]) is None
    assert region.parse_ms(None) is None and region.parse_ms([]) is None
    assert region.node_name("ru1.node.check-host.net") == "из России"
    assert region.node_name("xx9.node.check-host.net") == "снаружи"


async def test_measure_never_sends_private_hosts_outside():
    assert await region.measure("http://192.168.1.1/", "ru") is None
    assert await region.measure("http://intranet/", "ru") is None


async def test_region_job_writes_the_snapshot_and_the_card_shows_it(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://r.test")
    await sitestatus.update(sid, "avail", status="ok", ms=900, error=None)
    monkeypatch.setattr(scheduler, "REGION_PAUSE", 0)

    async def fake_measure(url, country):
        return (250, "из России") if country == "ru" else None
    monkeypatch.setattr(region, "measure", fake_measure)

    await scheduler.run_region_checks()                 # second opinion off → external service untouched
    assert "region" not in await sitestatus.get(sid)
    await integrations.set_value("second_opinion", "on")
    await scheduler.run_region_checks()
    assert (await sitestatus.get(sid))["region"]["ms"] == 250
    lines = await sites.card_lines(await db.get_site(sid))
    assert any("из России" in line and "0.25 с" in line for line in lines)
    await integrations.set_value("second_opinion", None)
