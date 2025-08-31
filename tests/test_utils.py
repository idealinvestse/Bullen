from app.engine.audio_engine import db_to_linear, linear_to_db


def approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def test_db_to_linear_basic():
    assert approx(db_to_linear(0.0), 1.0)
    assert approx(db_to_linear(20.0), 10.0)
    assert 0.49 <= db_to_linear(-6.0) <= 0.51


def test_linear_to_db_basic():
    assert approx(linear_to_db(1.0), 0.0)
    assert approx(linear_to_db(10.0), 20.0)


def test_roundtrip_values():
    for db in (-60.0, -20.0, -6.0, 0.0, 6.0, 12.0):
        lin = db_to_linear(db)
        db2 = linear_to_db(lin)
        assert abs(db - db2) < 1e-6


def test_linear_to_db_zero_floor():
    # linear_to_db guards against log10(0)
    assert linear_to_db(0.0) < -200.0
