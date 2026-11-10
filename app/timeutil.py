"""IST (Asia/Kolkata) time helpers — v3.4.

Every time the site SHOWS is IST: notification and announcement
timestamps, submission times, cheat-log detections, account creation
dates, the CTF countdown, and the admin PDF export stamp. Internally
the platform still stores UTC (models use ``utc_now()``); only the
display converts, so no stored data changes meaning.

The admin config form also takes CTF start/end in IST now
(``_parse_config_dt`` in ``app/models.py`` attaches IST to a naive
admin input), so the admin types the time he means and the site
shows the same time back.

``zoneinfo`` needs the IANA database: Linux and Render ship it, but
Windows does not, so ``tzdata`` is a pinned dependency (see
requirements.txt) to keep local Windows dev working too.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def to_ist(dt: datetime | None) -> datetime | None:
    """Convert a datetime to IST.

    A naive datetime is treated as UTC (the pre-v3.4 storage
    convention); ``None`` passes through as ``None``.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def format_ist(dt: datetime | None, fmt: str = "%d %b %Y %H:%M") -> str:
    """Jinja ``ist`` filter: datetime -> IST wall-time string.

    Returns ``""`` for ``None`` so templates never crash on a missing
    timestamp. Usage: ``{{ a.created_at | ist("%d %b %Y %H:%M") }} IST``.
    """
    converted = to_ist(dt)
    if converted is None:
        return ""
    return converted.strftime(fmt)
