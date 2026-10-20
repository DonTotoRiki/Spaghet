from spaghet.probes import smooth


def test_smooth_first_value_is_kept():
    assert smooth(None, 12.0) == 12.0


def test_smooth_dampens_spike():
    assert smooth(2.0, 102.0) == 32.0  # 2 + 0.3 * 100
