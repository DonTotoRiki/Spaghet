from spaghet.classify import classify, is_random_mac


def test_service_beats_vendor():
    # Une imprimante Samsung qui annonce IPP reste une imprimante, malgré le fabricant « téléphone ».
    assert classify("Samsung Electronics", ["_ipp._tcp.local."]) == "printer"


def test_vendor_keyword():
    assert classify("Espressif Inc.", []) == "iot"
    assert classify("Brother Industries", []) == "printer"


def test_random_mac_is_phone():
    assert is_random_mac("da:a1:19:00:00:01")
    assert not is_random_mac("3c:22:fb:00:00:01")
    assert classify(None, [], "da:a1:19:00:00:01") == "phone"


def test_unknown():
    assert classify("Obscure Corp", [], "3c:22:fb:00:00:01") is None
    assert classify(None, []) is None
