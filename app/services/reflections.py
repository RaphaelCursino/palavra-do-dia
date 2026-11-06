from datetime import date

from .daily_reading import format_date_pt, format_reference


def serialize_reflection(row):
    reflection = dict(row)
    reflection["reference"] = format_reference(reflection)
    reflection["display_date"] = format_date(reflection["reading_date"])
    return reflection


def serialize_reflections(rows):
    return [serialize_reflection(row) for row in rows]


def format_date(reading_date):
    return format_date_pt(date.fromisoformat(reading_date))
