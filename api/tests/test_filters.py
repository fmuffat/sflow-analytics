"""Unit tests: filter parsing and SQL generation (no database)."""

from datetime import datetime, timedelta, timezone

import pytest

from app import catalog
from app.filters import FilterError, build_filter, ip_range, normalize_service, parse_time

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def bf(**kw):
    return build_filter(kw.pop("from_", None), kw.pop("to", None), kw.pop("range_", None), now=NOW, **kw)


def test_default_window_is_last_hour():
    f = bf()
    assert f.end == NOW and f.start == NOW - timedelta(hours=1)


@pytest.mark.parametrize("r,seconds", [("5m", 300), ("24h", 86400), ("7d", 604800)])
def test_relative_ranges(r, seconds):
    assert bf(range_=r).seconds == seconds


def test_absolute_times():
    f = bf(from_="2026-01-01T10:00:00Z", to="1767265200")  # 2026-01-01T11:00:00Z
    assert f.start == datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    assert f.end == datetime(2026, 1, 1, 11, tzinfo=timezone.utc)
    assert parse_time("2026-01-01T12:00:00+02:00") == datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    assert parse_time("2026-01-01T10:00:00").tzinfo == timezone.utc  # naive = UTC


@pytest.mark.parametrize("kw,msg", [
    ({"from_": "2026-01-01T11:00:00Z", "to": "2026-01-01T10:00:00Z"}, "before"),
    ({"range_": "2w"}, "range"),
    ({"from_": "yesterday"}, "invalid time"),
    ({"src_ip": "10.0.0.300"}, "invalid IP"),
    ({"ip": "1.1.1.1' OR 1=1 --"}, "invalid IP"),
    ({"vlan": "5000"}, "vlan"),
    ({"dst_port": "-1"}, "dst_port"),
    ({"protocol": "foo"}, "unknown protocol"),
    ({"service": "nope"}, "unknown service"),
    ({"service": "x'); DROP TABLE flow_records; --"}, "unknown service"),
    ({"exporter": "switch-1"}, "exporter"),
])
def test_invalid_values(kw, msg):
    with pytest.raises(FilterError, match=msg):
        bf(**kw)


def test_range_too_long():
    with pytest.raises(FilterError, match="longer"):
        build_filter("2020-01-01", "2026-01-01", None, max_days=31)


def test_ip_range_maps_ipv4():
    assert ip_range("10.1.2.3") == ("::ffff:10.1.2.3", "::ffff:10.1.2.3")
    assert ip_range("10.1.0.0/16") == ("::ffff:10.1.0.0", "::ffff:10.1.255.255")
    assert ip_range("10.1.2.3/24") == ("::ffff:10.1.2.0", "::ffff:10.1.2.255")  # host bits ignored
    assert ip_range("2001:db8::/32") == ("2001:db8::", "2001:db8:ffff:ffff:ffff:ffff:ffff:ffff")


@pytest.mark.parametrize("raw,canon", [
    ("https", "HTTPS"), ("dns", "DNS"), ("smb submission", None), ("tcp/9999", "TCP/9999"),
    ("ICMPV6", "ICMPv6"), ("unknown", "Unknown"), ("non-ip", "Non-IP"), ("ip-99", "IP-99"),
    ("udp/70000", None), ("foo/80", None),
])
def test_normalize_service(raw, canon):
    if canon is None:
        with pytest.raises(FilterError):
            normalize_service(raw)
    else:
        assert normalize_service(raw) == canon


def test_protocol_number():
    assert catalog.protocol_number("tcp") == 6
    assert catalog.protocol_number("ICMPv6") == 58
    assert catalog.protocol_number("47") == 47
    with pytest.raises(ValueError):
        catalog.protocol_number("256")


def test_where_is_parameterized():
    f = bf(src_ip="10.0.0.0/8,2001:db8::1", protocol="tcp,udp", vlan="10", exporter="192.0.2.1/10.0.0.1/0",
           service="HTTPS", ifindex="48")
    where, params = f.where()
    # User values only ever appear as parameters, never inline.
    for literal in ("10.0.0.0", "2001:db8", "HTTPS", "192.0.2.1"):
        assert literal not in where
    assert "(src_ip BETWEEN" in where and "ip_protocol IN" in where and "service IN" in where
    assert "(input_ifindex IN" in where and "OR output_ifindex IN" in where
    assert params["f_start"] == f.start
    assert [6, 17] in params.values() and ["HTTPS"] in params.values()


def test_describe_echoes_only_set_filters():
    d = bf(vlan="10,20").describe()
    assert d["vlans"] == [10, 20] and "src_ips" not in d and "from" in d


def test_service_expression_only_embeds_catalog_constants():
    expr = catalog.service_expr()
    assert expr.count("transform(") >= 2 and "'HTTPS'" in expr and "{" not in expr
