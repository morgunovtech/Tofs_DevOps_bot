from monitors.second_opinion import endpoint_for, node_up


def test_endpoint_for():
    assert endpoint_for("https://example.com") == ("check-http", "https://example.com")
    assert endpoint_for("tcp://mail.example.com:25") == ("check-tcp", "mail.example.com:25")
    assert endpoint_for("ping://example.com") == ("check-ping", "example.com")
    assert endpoint_for("ping://10.0.0.1") is None          # private
    assert endpoint_for("https://nas.local") is None
    assert endpoint_for("tcp://example.com") is None        # no port


def test_node_up_shapes():
    assert node_up("check-http", [[1, 0.12, "OK", "200", "1.2.3.4"]]) is True
    assert node_up("check-http", [[0, 0.12, "Timeout", None, "1.2.3.4"]]) is False
    assert node_up("check-tcp", [{"address": "1.2.3.4", "time": 0.03}]) is True
    assert node_up("check-tcp", [{"error": "refused"}]) is False
    assert node_up("check-ping", [[["OK", 0.05, "1.2.3.4"], ["TIMEOUT", None, None]]]) is True
    assert node_up("check-ping", [[["TIMEOUT", None, None]]]) is False
    assert node_up("check-http", "garbage") is None
