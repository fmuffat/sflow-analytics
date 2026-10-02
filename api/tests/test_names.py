"""Aliases (normalization, CSV) and reverse DNS (with a fake resolver)."""

import pytest

from app import names, rdns


@pytest.mark.parametrize("kind,key,expected", [
    ("ip", " 192.168.1.30 ", "192.168.1.30"), ("ip", "2001:DB8::0001", "2001:db8::1"),
    ("cidr", "192.168.101.7/24", "192.168.101.0/24"), ("mac", "AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff"),
    ("mac", "aabb.ccdd.eeff", "aa:bb:cc:dd:ee:ff"), ("mac", "aabbccddeeff", "aa:bb:cc:dd:ee:ff"),
])
def test_normalize_key(kind, key, expected):
    assert names.normalize_key(kind, key) == expected


@pytest.mark.parametrize("kind,key", [("ip", "300.1.1.1"), ("cidr", "10.0.0.0/33"), ("mac", "zz:bb:cc:dd:ee:ff"),
                                      ("host", "x"), ("ip", "1.1.1.1' OR 1=1")])
def test_normalize_key_rejects(kind, key):
    with pytest.raises(names.AliasError):
        names.normalize_key(kind, key)


def test_names_are_bounded():
    with pytest.raises(names.AliasError):
        names.normalize_name("")
    with pytest.raises(names.AliasError):
        names.normalize_name("x" * 65)


def test_dns_settings_validation(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "config_dir", str(tmp_path))
    assert rdns.get_config() == {"enabled": True, "servers": [], "max_per_run": 300}
    rdns.save_config({"enabled": False, "servers": ["192.168.1.1", " 1.1.1.1 "], "max_per_run": 50})
    assert rdns.get_config() == {"enabled": False, "servers": ["192.168.1.1", "1.1.1.1"], "max_per_run": 50}
    for bad in ({"servers": ["dns.example.com"]}, {"servers": ["1.1.1.1"] * 5}, {"max_per_run": 1}):
        with pytest.raises(ValueError):
            rdns.save_config(bad)


@pytest.mark.parametrize("mac,vendor", [
    ("00:11:32:aa:bb:cc", "Synology"), ("00-0C-29-12-34-56", "VMware"), ("b8ca.3a00.0000", "Dell"),
    ("02:00:00:00:00:01", "Randomized MAC"), ("da:a1:19:00:00:01", "Randomized MAC"),
    ("01:00:5e:00:00:fb", "Multicast"), ("ff:ff:ff:ff:ff:ff", "Multicast"), ("nope", None),
])
def test_oui_lookup(mac, vendor):
    from app import oui

    assert oui.vendor(mac) == vendor


def test_oui_database_and_short_names():
    from app import oui

    assert oui.size() > 50_000
    assert oui.short_name("Synology Incorporated") == "Synology"
    assert oui.short_name("Hon Hai Precision Ind. Co.,Ltd.") == "Hon Hai Precision Ind"
    assert oui.short_name("Ruckus Wireless") == "Ruckus Wireless"
    assert oui.short_name("Intel Corporate") == "Intel"
