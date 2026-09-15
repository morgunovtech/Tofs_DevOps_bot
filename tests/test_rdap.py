from datetime import UTC, datetime, timedelta

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from monitors import rdap
from monitors.rdap import ascii_tld, parse_rdap, parse_whois, rdap_lookup

EXP = (datetime.now(UTC) + timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
RDAP_DOC = {
    "objectClassName": "domain", "ldhName": "EXAMPLE.TECH",
    "events": [{"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"},
               {"eventAction": "expiration", "eventDate": EXP}],
    "entities": [{"roles": ["registrar"],
                  "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                           ["fn", {}, "text", "Namecheap, Inc."]]]}],
    "nameservers": [{"ldhName": "ns2.example.net."}, {"ldhName": "NS1.example.net"}],
}


def test_parse_rdap():
    info = parse_rdap(RDAP_DOC, "example.tech")
    assert info.registrar == "Namecheap, Inc."
    assert info.days_left in (39, 40)
    assert info.name_servers == ["ns1.example.net", "ns2.example.net"]
    assert info.creation_date.startswith("2020-01-01")
    assert info.source == "rdap"


def test_parse_whois_tcinet():
    text = ("% TCI Whois Service.\ndomain:        EXAMPLE.RU\nnserver:       ns1.example.ru.\n"
            "nserver:       ns2.example.ru.\nstate:         REGISTERED, DELEGATED\n"
            "registrar:     REGRU-RU\ncreated:       2010-05-20T12:00:00Z\n"
            f"paid-till:     {EXP}\n")
    info = parse_whois(text, "example.ru")
    assert info.registrar == "REGRU-RU" and info.source == "whois"
    assert info.days_left in (39, 40)
    assert info.name_servers == ["ns1.example.ru", "ns2.example.ru"]


def test_ascii_tld():
    assert ascii_tld("example.tech") == "tech"
    assert ascii_tld("пример.рф") == "xn--p1ai"


async def test_rdap_lookup_uses_bootstrap_then_redirector(monkeypatch):
    async def domain(request):
        name = request.match_info["name"]
        if name == "missing.tech":
            return web.Response(status=404)
        return web.json_response(RDAP_DOC)

    app = web.Application()
    app.router.add_get("/domain/{name}", domain)
    async with TestServer(app) as server:
        base = str(server.make_url("/"))
        monkeypatch.setattr(rdap, "_bootstrap", {"tech": [base]})
        monkeypatch.setattr(rdap, "_bootstrap_at", 1e12)
        monkeypatch.setattr(rdap, "RDAP_ORG", base + "domain/{domain}")
        async with aiohttp.ClientSession() as session:
            info = await rdap_lookup("example.tech", session)
            assert info.registrar == "Namecheap, Inc."
            with pytest.raises(rdap.DomainLookupError):
                await rdap_lookup("missing.tech", session)
