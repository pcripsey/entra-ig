from __future__ import annotations


def sanitize_string(value: str | None) -> str:
    if value is None:
        return ''

    text = str(value)
    # Replace every carriage return and line feed so OpenText consumes a single
    # logical CSV row for each exported directory object.
    for token in ('\r\n', '\r', '\n'):
        text = text.replace(token, ' ')
    return text.strip()


def to_csv_value(value: object | None) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return sanitize_string(str(value))
