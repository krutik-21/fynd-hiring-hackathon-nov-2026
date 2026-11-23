from datetime import datetime, timezone
from typing import Tuple
from dateutil import parser as date_parser


def parse_and_normalize_timestamp(time_input: str | datetime | float | int) -> Tuple[str, float]:
    """
    Parses a timestamp input with or without timezone offsets and normalizes to UTC.
    Returns:
        (iso_utc_str, unix_timestamp_float)
    Raises:
        ValueError: if input cannot be parsed or is empty/invalid.
    """
    if time_input is None:
        raise ValueError("Timestamp cannot be null or empty")

    if isinstance(time_input, (int, float)):
        # Treat as unix epoch timestamp
        dt = datetime.fromtimestamp(float(time_input), tz=timezone.utc)
    elif isinstance(time_input, datetime):
        dt = time_input
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
    elif isinstance(time_input, str):
        cleaned = time_input.strip()
        if not cleaned:
            raise ValueError("Timestamp string is empty")
        try:
            dt = date_parser.isoparse(cleaned)
        except Exception:
            try:
                dt = date_parser.parse(cleaned)
            except Exception as exc:
                raise ValueError(f"Unable to parse timestamp '{cleaned}': {exc}") from exc

        if dt.tzinfo is None:
            # If naive, assume UTC
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
    else:
        raise ValueError(f"Unsupported timestamp type: {type(time_input)}")

    iso_utc = dt.isoformat().replace("+00:00", "Z")
    unix_epoch = dt.timestamp()
    return iso_utc, unix_epoch


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
