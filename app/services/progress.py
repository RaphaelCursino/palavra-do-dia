import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from .daily_reading import format_date_pt, format_reference


WEEKDAYS_PT_SHORT = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
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


@dataclass(frozen=True)
class MonthCalendar:
    year: int
    month: int
    title: str
    previous_month: str | None
    next_month: str | None
    weeks: list


def validate_month(year_text, month_text, today):
    if year_text is None and month_text is None:
        return date(today.year, today.month, 1)
    if year_text is None or month_text is None:
        raise ValueError("Informe o ano e o mês juntos.")
    try:
        selected = date(int(year_text), int(month_text), 1)
    except (ValueError, TypeError) as error:
        raise ValueError("Mês ou ano inválido.") from error
    if selected > date(today.year, today.month, 1):
        raise ValueError("Não é possível consultar meses futuros.")
    if selected < date(2000, 1, 1):
        raise ValueError("A data informada está fora do intervalo permitido.")
    return selected


def shift_month(month_start, offset):
    month_index = month_start.year * 12 + month_start.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def serialize_month_calendar(month_start, completed_dates, today):
    completed_set = set(completed_dates)
    month_weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(
        month_start.year, month_start.month
    )
    weeks = [
        [
            {
                "date": day.isoformat(),
                "day_number": day.day,
                "in_month": day.month == month_start.month,
                "completed": day.isoformat() in completed_set,
            }
            for day in week
        ]
        for week in month_weeks
    ]
    previous = shift_month(month_start, -1)
    following = shift_month(month_start, 1)
    return MonthCalendar(
        year=month_start.year,
        month=month_start.month,
        title=f"{MONTHS_PT[month_start.month - 1].capitalize()} {month_start.year}",
        previous_month=f"{previous.year:04d}-{previous.month:02d}",
        next_month=(
            f"{following.year:04d}-{following.month:02d}"
            if following <= date(today.year, today.month, 1)
            else None
        ),
        weeks=weeks,
    )


def calculate_streaks(completed_dates, today):
    completed = {date.fromisoformat(value) for value in completed_dates}
    longest = 0
    run = 0
    previous = None
    for completed_date in sorted(completed):
        run = run + 1 if previous and completed_date == previous + timedelta(days=1) else 1
        longest = max(longest, run)
        previous = completed_date

    current = 0
    cursor = today if today in completed else today - timedelta(days=1)
    while cursor in completed:
        current += 1
        cursor -= timedelta(days=1)
    return current, longest


def calculate_progress_metrics(completion_history, today):
    dates = sorted({row["completed_date"] for row in completion_history})
    current_streak, longest_streak = calculate_streaks(dates, today)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    weekly_total = sum(week_start.isoformat() <= value <= week_end.isoformat() for value in dates)
    return {
        "total_days": len(dates),
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "weekly_total": weekly_total,
    }


def serialize_completion_history(rows):
    history = []
    for row in rows:
        entry = dict(row)
        entry["reference"] = format_reference(entry)
        entry["display_completed_date"] = format_date_pt(
            date.fromisoformat(entry["completed_date"])
        )
        history.append(entry)
    return history


def month_days_bounds(month_start):
    last_day = calendar.monthrange(month_start.year, month_start.month)[1]
    return (
        month_start.isoformat(),
        date(month_start.year, month_start.month, last_day).isoformat(),
    )


def weekday_headers():
    return WEEKDAYS_PT_SHORT
