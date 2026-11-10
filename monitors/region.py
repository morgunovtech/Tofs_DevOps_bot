"""Response time from where the audience is — every 6 hours, via check-host.net.

The bot's own probe runs from wherever the bot is hosted: Railway in the
US for a site whose visitors are in Russia. One request every six hours
from a check-host node in the owner's region gives the number the owner
cares about: «из России, 0.8 с». Its own opt-in (REGION_SPEED, off by
default): the address of every site goes to the external service, and that
is a choice the owner makes, not a side effect of the second opinion. Only
the site's origin goes there — never a path or a query."""

import asyncio
import logging
from urllib.parse import urlparse

import aiohttp

from monitors.second_opinion import API, TIMEOUT, endpoint_for

logger = logging.getLogger(__name__)

# check-host node country → words on the site card
NODE_NAMES = {
    "ru": "из России", "ua": "из Украины", "kz": "из Казахстана", "de": "из Германии",
    "nl": "из Нидерландов", "fr": "из Франции", "uk": "из Британии", "us": "из США",
    "pl": "из Польши", "tr": "из Турции", "br": "из Бразилии", "jp": "из Японии",
    "in": "из Индии", "il": "из Израиля", "ch": "из Швейцарии", "fi": "из Финляндии",
    "it": "из Италии", "cz": "из Чехии", "es": "из Испании", "pt": "из Португалии",
    "bg": "из Болгарии", "hk": "из Гонконга", "md": "из Молдовы", "rs": "из Сербии",
    "se": "из Швеции", "lt": "из Литвы", "ir": "из Ирана", "hu": "из Венгрии",
}

_TZ_COUNTRY = {
    "Europe/Kyiv": "ua", "Europe/Kiev": "ua", "Europe/Minsk": "pl", "Europe/Chisinau": "md",
    "Europe/Berlin": "de", "Europe/Vienna": "de", "Europe/Copenhagen": "de", "Europe/Prague": "cz",
    "Europe/Zurich": "ch", "Europe/Amsterdam": "nl", "Europe/Brussels": "nl", "Europe/Paris": "fr",
    "Europe/Madrid": "es", "Europe/Lisbon": "pt", "Europe/Rome": "it", "Europe/Warsaw": "pl",
    "Europe/Stockholm": "se", "Europe/Oslo": "se", "Europe/Helsinki": "fi", "Europe/Tallinn": "fi",
    "Europe/Riga": "lt", "Europe/Vilnius": "lt", "Europe/London": "uk", "Europe/Dublin": "uk",
    "Europe/Istanbul": "tr", "Europe/Sofia": "bg", "Europe/Belgrade": "rs", "Europe/Budapest": "hu",
    "Asia/Almaty": "kz", "Asia/Tashkent": "kz", "Asia/Bishkek": "kz", "Asia/Tbilisi": "tr",
    "Asia/Yerevan": "tr", "Asia/Baku": "tr", "Asia/Jerusalem": "il", "Asia/Tel_Aviv": "il",
    "Asia/Tehran": "ir", "Asia/Tokyo": "jp", "Asia/Seoul": "jp", "Asia/Kolkata": "in",
    "Asia/Dubai": "in", "Asia/Hong_Kong": "hk", "Asia/Shanghai": "hk", "Asia/Singapore": "hk",
    "America/Sao_Paulo": "br", "UTC": "de",
}


def country_for_timezone(tz: str) -> str:
    """check-host country code for the owner's timezone: every Russian zone →
    ru, the rest by city, continents as a fallback."""
    if tz in _TZ_COUNTRY:
        return _TZ_COUNTRY[tz]
    if tz in ("Europe/Moscow", "Europe/Kaliningrad", "Europe/Samara", "Europe/Volgograd",
              "Europe/Saratov", "Europe/Ulyanovsk", "Europe/Astrakhan", "Europe/Kirov") or tz.startswith(
            ("Asia/Yekaterinburg", "Asia/Omsk", "Asia/Novosibirsk", "Asia/Barnaul", "Asia/Tomsk",
             "Asia/Krasnoyarsk", "Asia/Irkutsk", "Asia/Chita", "Asia/Yakutsk", "Asia/Vladivostok",
             "Asia/Magadan", "Asia/Sakhalin", "Asia/Kamchatka", "Asia/Anadyr", "Asia/Novokuznetsk")):
        return "ru"
    continent = tz.split("/")[0]
    return {"America": "us", "Asia": "hk", "Australia": "jp", "Europe": "de", "Africa": "de"}.get(continent, "de")


def node_name(node_key: str) -> str:
    return NODE_NAMES.get(node_key[:2].lower(), "снаружи")


def region_words(country: str) -> str:
    """«из России» for a check-host country code; a country without words
    of its own is named by its code."""
    return NODE_NAMES.get(country[:2].lower(), f"из страны {country[:2].upper()}")


def parse_ms(node_result) -> int | None:
    """check-http node result → milliseconds; None when that node failed:
    [[1, 0.123, "OK", "200", "1.2.3.4"]]"""
    try:
        first = node_result[0]
        if first[0] == 1 and first[1] is not None:
            return int(float(first[1]) * 1000)
    except (TypeError, IndexError, KeyError, ValueError):
        return None
    return None


def origin(url: str) -> str:
    """scheme://host[:port]/ — the speed of the site, not of one page: no
    path, no query (it may hold a token) goes to check-host."""
    try:
        p = urlparse(url)
        port = f":{p.port}" if p.port else ""
    except ValueError:
        return url                       # malformed: endpoint_for refuses it as it is
    return f"{p.scheme}://{p.hostname}{port}/"


async def measure(url: str, country: str) -> tuple[int, str] | None:
    """(ms, «из России») from a node in the country — or from whichever node
    check-host picks when that country has none. None on any failure."""
    page = origin(url)
    # The same gate as the second opinion (an internal address never leaves
    # the bot) — on the origin: a query of the monitor's address stays home
    # either way, and the site's speed is still worth measuring.
    target = endpoint_for(page)
    if not target or target[0] != "check-http":
        return None
    attempts = ({"host": page, "node": f"{country}1.node.check-host.net"}, {"host": page, "max_nodes": 1})
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT, headers={"Accept": "application/json"}) as session:
            for params in attempts:
                async with session.get(f"{API}/check-http", params=params) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                request_id = data.get("request_id")
                if not request_id or not data.get("nodes"):
                    continue
                for _ in range(4):
                    await asyncio.sleep(3)
                    async with session.get(f"{API}/check-result/{request_id}") as resp:
                        if resp.status != 200:
                            continue
                        results = await resp.json()
                    for node, node_result in (results or {}).items():
                        if node_result is None:
                            continue
                        ms = parse_ms(node_result)
                        return (ms, node_name(node)) if ms is not None else None
                return None
    except Exception as e:
        logger.debug("region measure %s failed: %s", url, e)
    return None
