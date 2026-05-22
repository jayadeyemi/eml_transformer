from datetime import datetime, timezone
from zoneinfo import ZoneInfo


TZ_MAP = {
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
}

WEEKDAY_FIXES = {
    "Fr": "Fri",
    "Tu": "Tue",
    "Th": "Thu",
    "Sa": "Sat",
    "Su": "Sun",
    "Mo": "Mon",
    "We": "Wed",
}


def parse_issued_at(text: str) -> str | None:
    if not text:
        return None

    cleaned = text.strip()

    if cleaned.lower().startswith("issued at "):
        cleaned = cleaned[len("Issued at "):].strip()

    if cleaned.lower().startswith("issued by"):
        return None

    parts = cleaned.split()

    if len(parts) < 5:
        return cleaned

    time_part = parts[0]

    # Pad 242 -> 0242
    if len(time_part) == 3:
        time_part = f"0{time_part}"

    # Detect AM/PM presence
    has_ampm = parts[1] in {"AM", "PM"}

    try:
        if has_ampm:
            am_pm = parts[1]
            tz_abbr = parts[2]
            weekday = parts[3]

            date_parts = parts[4:]

            fmt = "%I%M %p %a %b %d %Y"

            cleaned_no_tz = (
                f"{time_part} {am_pm} "
                f"{weekday} {' '.join(date_parts)}"
            )

        else:
            tz_abbr = parts[1]
            weekday = parts[2]

            date_parts = parts[3:]

            fmt = "%H%M %a %b %d %Y"

            cleaned_no_tz = (
                f"{time_part} "
                f"{weekday} {' '.join(date_parts)}"
            )

        weekday = WEEKDAY_FIXES.get(weekday, weekday)

        cleaned_no_tz = cleaned_no_tz.replace(
            parts[3] if has_ampm else parts[2],
            weekday,
            1,
        )

        timezone_name = TZ_MAP.get(tz_abbr)

        if not timezone_name:
            return cleaned
        
        dt = datetime.strptime(
            cleaned_no_tz,
            fmt,
        )

        return (
            dt.replace(
                tzinfo=ZoneInfo(timezone_name)
            )
            .astimezone(timezone.utc)
            .isoformat()
        )

    except Exception:
        return cleaned