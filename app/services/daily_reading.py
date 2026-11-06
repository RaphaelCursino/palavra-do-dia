from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


WEEKDAYS_PT = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)
MONTHS_PT = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def local_today(timezone_name, instant=None):
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError) as error:
        raise ValueError(f"Fuso horário IANA inválido: {timezone_name}") from error

    if instant is None:
        return datetime.now(timezone).date()
    if instant.tzinfo is None:
        raise ValueError("O instante de referência precisa ter fuso horário.")
    return instant.astimezone(timezone).date()


def format_date_pt(day):
    return (
        f"{WEEKDAYS_PT[day.weekday()]}, {day.day} de "
        f"{MONTHS_PT[day.month - 1]} de {day.year}"
    )


def format_reference(reading):
    start = reading["start_verse_number"]
    end = reading["end_verse_number"]
    reference = f'{reading["book_name"]} {reading["chapter_number"]}:{start}'
    if end != start:
        reference += f"–{end}"
    return reference
