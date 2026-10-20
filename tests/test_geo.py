from spaghet.geo import locate


class FakeReader(dict):
    """Même interface que maxminddb : get(ip) → dict ou None."""


def test_locate_reads_mmdb_record():
    reader = FakeReader({"142.250.1.1": {
        "location": {"latitude": 37.4, "longitude": -122.1},
        "city": {"names": {"en": "Mountain View"}},
        "country": {"names": {"en": "United States", "fr": "États-Unis"}},
    }})
    assert locate(reader, "142.250.1.1") == {"lat": 37.4, "lon": -122.1, "city": "Mountain View", "country": "États-Unis"}


def test_locate_without_base_or_record():
    assert locate(None, "1.1.1.1") == {}
    assert locate(FakeReader(), "1.1.1.1") == {}
    assert locate(FakeReader({"1.1.1.1": {"country": {"names": {"en": "Australia"}}}}), "1.1.1.1") == {}
