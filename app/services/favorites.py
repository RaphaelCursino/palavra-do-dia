from datetime import date, datetime
from zoneinfo import ZoneInfo

from .daily_reading import format_date_pt, format_reference


FAVORITE_NOTICES = {
    "verse-added": "Versículo salvo nos favoritos.",
    "verse-exists": "Esse versículo já está salvo nos favoritos.",
    "verse-removed": "Versículo removido dos favoritos.",
    "reflection-added": "Reflexão salva nos favoritos.",
    "reflection-exists": "Essa reflexão já está salva nos favoritos.",
    "reflection-removed": "Reflexão removida dos favoritos.",
}


def serialize_favorites(rows, timezone_name="America/Sao_Paulo"):
    favorites = []
    for row in rows:
        favorite = dict(row)
        favorite["display_favorited_at"] = format_favorited_at(
            favorite["favorited_at"], timezone_name
        )
        if favorite["favorite_type"] == "reflection":
            favorite["reference_text"] = format_reference(favorite)
            favorite["display_reading_date"] = format_date_pt(
                date.fromisoformat(favorite["reading_date"])
            )
        favorites.append(favorite)
    favorites.sort(key=lambda favorite: favorite["favorited_at"], reverse=True)
    return favorites


def format_favorited_at(timestamp, timezone_name):
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return format_date_pt(parsed.astimezone(ZoneInfo(timezone_name)).date())
