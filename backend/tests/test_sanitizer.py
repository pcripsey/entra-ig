from app.services.sanitizer import sanitize_string, to_csv_value


def test_sanitize_string_replaces_newlines() -> None:
    assert sanitize_string('Alpha\r\nBeta\nGamma\rDelta') == 'Alpha Beta Gamma Delta'


def test_to_csv_value_normalizes_nones_and_booleans() -> None:
    assert to_csv_value(None) == ''
    assert to_csv_value(True) == 'true'
    assert to_csv_value(False) == 'false'
