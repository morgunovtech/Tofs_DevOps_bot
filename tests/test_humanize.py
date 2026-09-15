from services.humanize import (
    classify,
    describe_error,
    downtime,
    explain,
    fmt_seconds,
    incident_headline,
    speed,
)


def test_describe_error_covers_the_common_cases():
    assert describe_error("HTTP 502") == "хостинг отвечает ошибкой 502"
    assert describe_error("Site down: HTTP 404") == "страница не найдена (ошибка 404)"
    assert describe_error("Timeout (15s)") == "не ответил за 15 секунд"
    assert describe_error("SSL expires in 5 days!") == "сертификат истекает через 5 дн."
    assert describe_error("SSL expired 3 days ago") == "сертификат истёк 3 дн. назад"
    assert describe_error("Domain expires in 12 days") == "домен истекает через 12 дн."
    assert describe_error("Slow responses: ~5000ms") == "открывается за 5.0 с"
    assert describe_error("3 broken link(s) found on https://x") == "3 ссылок ведут в никуда"
    assert describe_error("5xx на 3 из 10 проверенных страниц") == "3 из 10 проверенных страниц отдают ошибку"
    assert describe_error("Cannot connect to host x:443 ssl:default [Name or service not known]") \
        == "адрес сайта не находится (проблема с DNS)"
    assert describe_error("Bad content: на странице нет фразы «Корзина»") == "на странице нет фразы «Корзина»"
    assert describe_error(None) == "неизвестная ошибка"
    assert describe_error("что-то своё") == "что-то своё"


def test_classify_and_explain():
    assert classify("availability", "Site down: HTTP 503") == "http_5xx"
    assert classify("availability", "HTTP 403") == "http_4xx"
    assert classify("availability", "Timeout (15s)") == "timeout"
    assert classify("availability", "Bad content: на странице нет фразы «x»") == "keyword_missing"
    assert classify("availability", "Bad content: на странице стоп-фраза «Fatal»") == "stop_phrase"
    assert classify("ssl", "SSL expires in 5 days!") == "ssl_expiring"
    assert classify("ssl", "SSL certificate has expired") == "ssl_expired"
    assert classify("domain", "Domain expired 2 days ago!") == "domain_expired"
    assert classify("performance", "Slow responses: ~4000ms") == "slow"
    expl = explain("availability", "HTTP 502")
    assert expl.meaning and expl.cause and len(expl.steps) >= 2


def test_downtime_and_speed():
    assert downtime(0, 0, 5) == "нет данных"
    assert downtime(100, 100, 5) == "без сбоев"
    assert downtime(100, 97, 5) == "недоступен ≈15 мин"
    assert downtime(2000, 1900, 1) == "недоступен ≈1 ч 40 мин"
    assert speed(120) == "быстро (0.12 с)" and speed(900) == "нормально (0.90 с)"
    assert speed(4200) == "медленно (4.2 с)" and fmt_seconds(15000) == "15 с"


def test_incident_headline():
    assert incident_headline("availability", "Site down: HTTP 502", "ex.com") == \
        "ex.com не открывается: хостинг отвечает ошибкой 502"
    assert incident_headline("performance", "Slow responses: ~3000ms", "ex.com") == "ex.com открывается за 3.0 с"
    assert incident_headline("ssl", "SSL expires in 3 days!", "ex.com") == "ex.com: сертификат истекает через 3 дн."
