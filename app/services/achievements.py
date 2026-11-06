from datetime import date, timedelta

from .progress import calculate_streaks


ACHIEVEMENT_DEFINITIONS = (
    {
        "code": "first-reading",
        "category": "Leitura",
        "title": "Primeira leitura",
        "description": "Conclua sua primeira leitura diária.",
        "target": 1,
        "unit": "dia",
    },
    {
        "code": "streak-3",
        "category": "Leitura",
        "title": "Três dias consecutivos",
        "description": "Leia por 3 dias consecutivos.",
        "target": 3,
        "unit": "dias",
    },
    {
        "code": "streak-7",
        "category": "Leitura",
        "title": "Uma semana constante",
        "description": "Leia por 7 dias consecutivos.",
        "target": 7,
        "unit": "dias",
    },
    {
        "code": "streak-15",
        "category": "Leitura",
        "title": "Quinze dias de constância",
        "description": "Leia por 15 dias consecutivos.",
        "target": 15,
        "unit": "dias",
    },
    {
        "code": "streak-30",
        "category": "Leitura",
        "title": "Um mês de constância",
        "description": "Leia por 30 dias consecutivos.",
        "target": 30,
        "unit": "dias",
    },
    {
        "code": "chapters-10",
        "category": "Capítulos",
        "title": "Dez capítulos",
        "description": "Conclua leituras em 10 capítulos diferentes.",
        "target": 10,
        "unit": "capítulos",
    },
    {
        "code": "chapters-50",
        "category": "Capítulos",
        "title": "Cinquenta capítulos",
        "description": "Conclua leituras em 50 capítulos diferentes.",
        "target": 50,
        "unit": "capítulos",
    },
    {
        "code": "chapters-100",
        "category": "Capítulos",
        "title": "Cem capítulos",
        "description": "Conclua leituras em 100 capítulos diferentes.",
        "target": 100,
        "unit": "capítulos",
    },
    {
        "code": "chapters-500",
        "category": "Capítulos",
        "title": "Quinhentos capítulos",
        "description": "Conclua leituras em 500 capítulos diferentes.",
        "target": 500,
        "unit": "capítulos",
    },
    {
        "code": "books-1",
        "category": "Bíblia",
        "title": "Primeiro livro concluído",
        "description": "Leia todos os capítulos de um livro.",
        "target": 1,
        "unit": "livro",
    },
    {
        "code": "books-5",
        "category": "Bíblia",
        "title": "Cinco livros concluídos",
        "description": "Leia todos os capítulos de cinco livros.",
        "target": 5,
        "unit": "livros",
    },
    {
        "code": "books-10",
        "category": "Bíblia",
        "title": "Dez livros concluídos",
        "description": "Leia todos os capítulos de dez livros.",
        "target": 10,
        "unit": "livros",
    },
    {
        "code": "goals-1",
        "category": "Metas",
        "title": "Primeira meta alcançada",
        "description": "Alcance sua meta semanal pela primeira vez.",
        "target": 1,
        "unit": "meta",
    },
    {
        "code": "goals-4",
        "category": "Metas",
        "title": "Quatro metas alcançadas",
        "description": "Alcance sua meta semanal em quatro semanas.",
        "target": 4,
        "unit": "metas",
    },
    {
        "code": "goals-12",
        "category": "Metas",
        "title": "Doze metas alcançadas",
        "description": "Alcance sua meta semanal em doze semanas.",
        "target": 12,
        "unit": "metas",
    },
)


def _completion_facts(connection, owner_key):
    rows = connection.execute(
        """
        SELECT reading_completions.completed_date,
               daily_readings.chapter_id,
               books.id AS book_id,
               books.code AS book_code,
               chapters.chapter_number
        FROM reading_completions
        JOIN daily_readings ON daily_readings.id = reading_completions.daily_reading_id
        JOIN chapters ON chapters.id = daily_readings.chapter_id
        JOIN books ON books.id = chapters.book_id
        WHERE reading_completions.owner_key = ?
        ORDER BY reading_completions.completed_date
        """,
        (owner_key,),
    ).fetchall()
    dates = sorted({row["completed_date"] for row in rows})
    chapter_ids = {row["chapter_id"] for row in rows}
    book_ids = {row["book_id"] for row in rows}
    completed_books = set()
    for book_id in book_ids:
        chapter_total = connection.execute(
            "SELECT COUNT(*) FROM chapters WHERE book_id = ?",
            (book_id,),
        ).fetchone()[0]
        completed_total = connection.execute(
            """
            SELECT COUNT(DISTINCT daily_readings.chapter_id)
            FROM reading_completions
            JOIN daily_readings ON daily_readings.id = reading_completions.daily_reading_id
            JOIN chapters ON chapters.id = daily_readings.chapter_id
            WHERE reading_completions.owner_key = ? AND chapters.book_id = ?
            """,
            (owner_key, book_id),
        ).fetchone()[0]
        if chapter_total and completed_total >= chapter_total:
            completed_books.add(book_id)
    return dates, len(chapter_ids), len(completed_books)


def _goal_weeks(connection, owner_key, weekly_target):
    rows = connection.execute(
        """
        SELECT completed_date
        FROM reading_completions
        WHERE owner_key = ?
        ORDER BY completed_date
        """,
        (owner_key,),
    ).fetchall()
    by_week = {}
    for row in rows:
        completed = date.fromisoformat(row["completed_date"])
        week_start = completed - timedelta(days=completed.weekday())
        by_week.setdefault(week_start.isoformat(), set()).add(completed)
    achieved = {
        week: dates
        for week, dates in by_week.items()
        if len(dates) >= weekly_target
    }
    return achieved


def sync_achievements(connection, owner_key, today):
    dates, chapter_count, completed_book_count = _completion_facts(
        connection, owner_key
    )
    current_streak, longest_streak = calculate_streaks(dates, today)
    weekly_target_row = connection.execute(
        "SELECT weekly_target FROM reading_goals WHERE owner_key = ?",
        (owner_key,),
    ).fetchone()
    weekly_target = weekly_target_row["weekly_target"] if weekly_target_row else None
    goal_weeks = _goal_weeks(connection, owner_key, weekly_target) if weekly_target else {}
    if weekly_target:
        placeholders = ",".join("?" for _ in goal_weeks) or "''"
        connection.execute(
            f"""
            DELETE FROM weekly_goal_achievements
            WHERE owner_key = ? AND week_start NOT IN ({placeholders})
            """,
            [owner_key, *goal_weeks],
        )
    else:
        connection.execute(
            "DELETE FROM weekly_goal_achievements WHERE owner_key = ?",
            (owner_key,),
        )
    for week_start in goal_weeks:
        connection.execute(
            """
            INSERT INTO weekly_goal_achievements (owner_key, week_start, weekly_target)
            VALUES (?, ?, ?)
            ON CONFLICT(owner_key, week_start) DO UPDATE SET
                weekly_target = excluded.weekly_target
            """,
            (owner_key, week_start, weekly_target),
        )

    achieved_values = {
        "first-reading": len(dates),
        "streak-3": longest_streak,
        "streak-7": longest_streak,
        "streak-15": longest_streak,
        "streak-30": longest_streak,
        "chapters-10": chapter_count,
        "chapters-50": chapter_count,
        "chapters-100": chapter_count,
        "chapters-500": chapter_count,
        "books-1": completed_book_count,
        "books-5": completed_book_count,
        "books-10": completed_book_count,
        "goals-1": len(goal_weeks),
        "goals-4": len(goal_weeks),
        "goals-12": len(goal_weeks),
    }
    eligible = {
        item["code"]
        for item in ACHIEVEMENT_DEFINITIONS
        if achieved_values[item["code"]] >= item["target"]
    }
    existing = {
        row["achievement_code"]
        for row in connection.execute(
            "SELECT achievement_code FROM achievement_unlocks WHERE owner_key = ?",
            (owner_key,),
        ).fetchall()
    }
    for code in existing - eligible:
        connection.execute(
            "DELETE FROM achievement_unlocks WHERE owner_key = ? AND achievement_code = ?",
            (owner_key, code),
        )
    for code in eligible:
        connection.execute(
            """
            INSERT INTO achievement_unlocks (owner_key, achievement_code)
            VALUES (?, ?)
            ON CONFLICT(owner_key, achievement_code) DO NOTHING
            """,
            (owner_key, code),
        )
    return achievement_progress(connection, owner_key, achieved_values)


def achievement_progress(connection, owner_key, achieved_values=None):
    if achieved_values is None:
        dates, chapter_count, completed_book_count = _completion_facts(
            connection, owner_key
        )
        achieved_values = {
            "first-reading": len(dates),
            "chapters-10": chapter_count,
            "chapters-50": chapter_count,
            "chapters-100": chapter_count,
            "chapters-500": chapter_count,
            "books-1": completed_book_count,
            "books-5": completed_book_count,
            "books-10": completed_book_count,
        }
    unlocked = {
        row["achievement_code"]: row["unlocked_at"]
        for row in connection.execute(
            "SELECT achievement_code, unlocked_at FROM achievement_unlocks WHERE owner_key = ?",
            (owner_key,),
        ).fetchall()
    }
    output = []
    for definition in ACHIEVEMENT_DEFINITIONS:
        value = achieved_values.get(definition["code"], 0)
        output.append(
            {
                **definition,
                "progress": min(value, definition["target"]),
                "value": value,
                "unlocked": definition["code"] in unlocked,
                "unlocked_at": unlocked.get(definition["code"]),
            }
        )
    return output
