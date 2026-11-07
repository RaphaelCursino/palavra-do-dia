import re
import secrets
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)

from ..db import get_db
from ..identity import csrf_token, current_owner_key, validate_csrf_token
from ..repositories.reflections import (
    count_published_reflections,
    get_published_reflection,
    get_published_reflection_for_reading,
    list_published_reflections,
)
from ..repositories.favorites import (
    add_reflection_favorite,
    add_verse_favorite,
    get_favorite_type,
    get_favoriteable_verse_by_reference,
    get_favoriteable_verse,
    get_reflection_favorite_id,
    list_favorites,
    list_verse_favorite_ids_for_reading,
    list_verse_favorite_ids_for_chapter,
    remove_favorite,
)
from ..repositories.progress import (
    add_completion,
    get_completion_for_date,
    get_reading_for_completion,
    get_weekly_goal,
    list_completion_dates,
    list_completion_history,
    remove_completion,
    save_weekly_goal,
)
from ..repositories.personal import (
    create_prayer_entry,
    delete_annotation,
    delete_highlight,
    delete_prayer_entry,
    get_highlights_for_chapter,
    get_prayer_entry,
    get_reader_preferences,
    list_annotations,
    list_annotations_for_chapter,
    list_prayer_entries,
    mark_prayer_answered,
    save_annotation,
    save_highlight,
    save_reader_preferences,
    update_prayer_entry,
)
from ..repositories.scripture import (
    get_book_chapters,
    get_daily_reading,
    list_books,
    list_chapter_verses,
    list_daily_reading_translations,
    list_translations,
    list_verse_notes,
    search_verses,
    get_verse,
)
from ..services.daily_reading import format_date_pt, format_reference, local_today
from ..services.reflections import serialize_reflection, serialize_reflections
from ..services.favorites import FAVORITE_NOTICES, serialize_favorites
from ..services.progress import (
    calculate_progress_metrics,
    month_days_bounds,
    serialize_completion_history,
    serialize_month_calendar,
    validate_month,
    weekday_headers,
)
from ..services.achievements import sync_achievements
from ..services.personal import (
    HIGHLIGHT_CATEGORIES,
    HIGHLIGHT_COLORS,
    PRAYER_TYPES,
    validate_annotation,
    validate_highlight,
    validate_prayer_entry,
    validate_reader_preferences,
)


pages = Blueprint("pages", __name__)
REFLECTIONS_PER_PAGE = 10
MAX_SEARCH_QUERY_LENGTH = 120
FAVORITE_FILTERS = {
    "todos": None,
    "versiculos": "verse",
    "reflexoes": "reflection",
}
HIGHLIGHT_COLOR_LABELS = {
    "gold": "Dourado",
    "blue": "Azul",
    "green": "Verde",
    "rose": "Rosa",
    "lavender": "Lavanda",
}
HIGHLIGHT_CATEGORY_LABELS = {
    "faith": "Fé",
    "hope": "Esperança",
    "love": "Amor",
    "gratitude": "Gratidão",
    "prayer": "Oração",
    "reflection": "Reflexão",
}


def _owner_key():
    return current_owner_key()


def _favorite_notice():
    return FAVORITE_NOTICES.get(request.args.get("favorite_status"))


def _personal_notice(*query_keys):
    notices = {
        "saved": "Anotação salva neste navegador.",
        "deleted": "Anotação excluída.",
        "highlight-saved": "Marcação salva junto aos favoritos.",
        "highlight-removed": "Marcação removida; o favorito foi mantido.",
        "created": "Registro salvo no diário de oração.",
        "updated": "Registro atualizado.",
        "answered": "Pedido marcado como respondido.",
        "prayer-deleted": "Registro removido do diário de oração.",
        "settings-saved": "Preferências de leitura salvas neste navegador.",
    }
    for key in query_keys:
        value = request.args.get(key)
        if value in notices:
            return notices[value]
    return None


def _validate_favorite_post():
    token = request.form.get("csrf_token", "")
    if not validate_csrf_token("favorites", token):
        abort(400)


def _validate_progress_post():
    token = request.form.get("csrf_token", "")
    if not validate_csrf_token("progress", token):
        abort(400)


def _validate_personal_post():
    token = request.form.get("csrf_token", "")
    if not validate_csrf_token("personal", token):
        abort(400)


def _positive_form_integer(field_name):
    try:
        value = int(request.form.get(field_name, ""))
    except ValueError:
        abort(404)
    if value < 1:
        abort(404)
    return value


def _weekly_goal_form_integer():
    try:
        value = int(request.form.get("weekly_target", ""))
    except ValueError:
        abort(400)
    if not 1 <= value <= 7:
        abort(400)
    return value


def _redirect_with_favorite_status(default_path, status):
    raw_target = request.form.get("next", "")
    parsed = urlsplit(raw_target)
    if (
        not raw_target.startswith("/")
        or "\\" in raw_target
        or parsed.scheme
        or parsed.netloc
        or not (
            parsed.path == "/"
            or parsed.path == "/favoritos"
            or re.fullmatch(r"/reflexoes/[1-9][0-9]*", parsed.path)
            or re.fullmatch(
                r"/biblia(?:/[a-z0-9][a-z0-9._-]{0,63}"
                r"(?:/[A-Z0-9]{3}(?:/[1-9][0-9]*)?)?)?",
                parsed.path,
            )
        )
    ):
        parsed = urlsplit(default_path)

    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "favorite_status"
    ]
    query.append(("favorite_status", status))
    target = urlunsplit(("", "", parsed.path, urlencode(query), ""))
    return redirect(target)


@pages.get("/")
def home():
    if request.method == "HEAD":
        return "", 200

    today = local_today(current_app.config["DAILY_READING_TIMEZONE"])
    today_iso = today.isoformat()
    connection = get_db()
    translations = list_translations(connection)
    translation_codes = {translation["code"] for translation in translations}
    daily_translation_codes = {
        translation["code"]
        for translation in list_daily_reading_translations(connection, today_iso)
    }
    requested_translation = request.args.get("translation", "").strip()
    selected_translation = None

    if requested_translation in translation_codes:
        selected_translation = requested_translation
    elif not requested_translation and len(translations) == 1:
        selected_translation = translations[0]["code"]

    daily_reading = None
    if selected_translation is not None:
        daily_reading = get_daily_reading(
            get_db(),
            today_iso,
            selected_translation,
        )

    if requested_translation and requested_translation not in translation_codes:
        empty_message = (
            "Essa tradução não tem uma leitura autorizada cadastrada para hoje."
        )
    elif translations and selected_translation is None:
        empty_message = "Escolha uma tradução para consultar a leitura de hoje."
    elif (
        selected_translation is not None
        and selected_translation not in daily_translation_codes
    ):
        empty_message = (
            "Ainda não há uma leitura diária editorial cadastrada para esta "
            "tradução e data."
        )
    elif translations and daily_reading is None:
        empty_message = (
            "A passagem cadastrada não está completa para a tradução selecionada "
            "e não será exibida."
        )
    else:
        empty_message = (
            "Ainda não há uma leitura autorizada cadastrada para esta data. "
            "Nenhum texto foi inventado ou escolhido aleatoriamente."
        )

    reading = daily_reading["reading"] if daily_reading else None
    verses = daily_reading["verses"] if daily_reading else []
    if verses:
        notes_by_verse = {}
        for note in list_verse_notes(
            get_db(),
            [verse["verse_id"] for verse in verses],
        ):
            notes_by_verse.setdefault(note["verse_id"], []).append(dict(note))
        verses = [
            {**dict(verse), "notes": notes_by_verse.get(verse["verse_id"], [])}
            for verse in verses
        ]
    reflection = (
        get_published_reflection_for_reading(
            get_db(),
            reading["daily_reading_id"],
        )
        if reading
        else None
    )
    if translations and selected_translation is None:
        reflection_empty_message = (
            "Escolha uma tradução acima para ver a reflexão associada à leitura."
        )
    elif reading and reflection is None:
        reflection_empty_message = (
            "Novas reflexões serão disponibilizadas quando houver conteúdo "
            "revisado para esta leitura."
        )
    else:
        reflection_empty_message = (
            "Novas reflexões serão disponibilizadas quando houver uma leitura "
            "autorizada e conteúdo revisado para esta data."
        )
    reflection_favorite_id = (
        get_reflection_favorite_id(get_db(), reflection["id"], _owner_key())
        if reflection
        else None
    )
    completion = get_completion_for_date(
        get_db(),
        today_iso,
        _owner_key(),
    )
    return render_template(
        "index.html",
        today_label=format_date_pt(today),
        today_iso=today_iso,
        translations=translations,
        selected_translation=selected_translation,
        reading=reading,
        verses=verses,
        reference=format_reference(reading) if reading else None,
        empty_message=empty_message,
        reflection=serialize_reflection(reflection) if reflection else None,
        reflection_empty_message=reflection_empty_message,
        reflections_url=url_for("pages.reflections_index"),
        favorite_verse_ids=(
            list_verse_favorite_ids_for_reading(
                get_db(), reading["daily_reading_id"], _owner_key()
            )
            if reading
            else {}
        ),
        reflection_favorite_id=reflection_favorite_id,
        reflection_favorited=reflection_favorite_id is not None,
        favorite_notice=_favorite_notice(),
        today_reading_completion=completion,
        progress_csrf_token=csrf_token("progress"),
    )


def _approved_translation(connection, translation_code):
    return next(
        (
            translation
            for translation in list_translations(connection)
            if translation["code"] == translation_code
        ),
        None,
    )


def _normalize_search_text(value):
    return "".join(
        character
        for character in unicodedata.normalize("NFD", value.casefold())
        if unicodedata.category(character) != "Mn"
    )


def _chapter_destinations(connection, translation_code, books):
    destinations = []
    for book in books:
        for chapter in get_book_chapters(
            connection, translation_code, book["code"]
        ):
            destinations.append(
                {
                    "book_code": book["code"],
                    "book_name": book["name_pt"],
                    "chapter_number": chapter["chapter_number"],
                }
            )
    return destinations


@pages.get("/biblia")
def bible_index():
    connection = get_db()
    translations = list_translations(connection)
    requested_translation = request.args.get("translation", "").strip()
    codes = {translation["code"] for translation in translations}
    selected_code = (
        requested_translation
        if requested_translation in codes
        else translations[0]["code"] if not requested_translation and len(translations) == 1
        else None
    )
    selected_translation = _approved_translation(connection, selected_code) if selected_code else None
    books = list_books(connection, selected_code) if selected_translation else []
    return render_template(
        "bible/index.html",
        translations=translations,
        selected_translation=selected_translation,
        books=books,
        translation_requested=bool(requested_translation),
        favorite_notice=_favorite_notice(),
        search_query="",
        search_results=[],
        search_message=None,
    )


@pages.get("/biblia/busca")
def bible_search():
    connection = get_db()
    translations = list_translations(connection)
    requested_translation = request.args.get("translation", "").strip()
    query = request.args.get("q", "").strip()
    selected_code = (
        requested_translation
        if requested_translation in {item["code"] for item in translations}
        else translations[0]["code"] if len(translations) == 1 else None
    )


    selected_translation = (
        _approved_translation(connection, selected_code)
        if selected_code
        else None
    )
    results = []
    message = None
    if not selected_translation:
        message = "Escolha uma tradução autorizada para pesquisar."
    elif not query:
        message = "Digite uma referência ou uma palavra para pesquisar."
    elif len(query) < 2:
        message = "Digite pelo menos 2 caracteres para pesquisar."
    elif len(query) > MAX_SEARCH_QUERY_LENGTH:
        message = "A busca deve ter no máximo 120 caracteres."
    else:
        reference_match = re.fullmatch(
            r"\s*(.+?)\s+([1-9][0-9]*)\s*[:ï¼š]\s*([1-9][0-9]*)\s*",
            query,
        )
        if reference_match:
            book_query, chapter_text, verse_text = reference_match.groups()
            normalized_book = _normalize_search_text(book_query)
            book = next(
                (
                    item
                    for item in list_books(connection, selected_code)
                    if _normalize_search_text(item["name_pt"]) == normalized_book
                ),
                None,
            )
            if book:
                verse = get_verse(
                    connection,
                    selected_code,
                    book["code"],
                    int(chapter_text),
                    int(verse_text),
                )
                if verse:
                    results = [dict(verse)]
            if not results:
                message = "Essa referência não foi encontrada na tradução selecionada."
        else:
            results = [
                dict(row)
                for row in search_verses(connection, selected_code, query)
            ]
            if not results:
                message = "Nenhum versículo encontrado para essa busca."

    return render_template(
        "bible/search.html",
        translations=translations,
        selected_translation=selected_translation,
        search_query=query,
        search_results=results,
        search_message=message,
        favorite_notice=_favorite_notice(),
    )


@pages.get("/biblia/cartao")
def bible_card():
    connection = get_db()
    translations = list_translations(connection)
    requested_translation = request.args.get("translation", "").strip()
    selected_code = (
        requested_translation
        if requested_translation in {item["code"] for item in translations}
        else translations[0]["code"] if len(translations) == 1 else None
    )
    selected_translation = (
        _approved_translation(connection, selected_code)
        if selected_code
        else None
    )
    query = request.args.get("referencia", "").strip()
    verse = None
    message = None
    if query and selected_translation:
        match = re.fullmatch(
            r"\s*(.+?)\s+([1-9][0-9]*)\s*[:ï¼š]\s*([1-9][0-9]*)\s*",
            query,
        )
        if match:
            book_query, chapter_text, verse_text = match.groups()
            normalized_book = _normalize_search_text(book_query)
            book = next(
                (
                    item
                    for item in list_books(connection, selected_code)
                    if _normalize_search_text(item["name_pt"]) == normalized_book
                ),
                None,
            )
            if book:
                verse = get_verse(
                    connection,
                    selected_code,
                    book["code"],
                    int(chapter_text),
                    int(verse_text),
                )
        if verse is None:
            message = "Informe uma referência válida existente na BLIVRE, como João 3:16."
    elif query:
        message = "Escolha uma tradução autorizada antes de pesquisar."
    return render_template(
        "bible/card.html",
        translations=translations,
        selected_translation=selected_translation,
        verse=verse,
        reference=query,
        card_message=message,
    )


@pages.get("/biblia/<translation_code>/<book_code>")
def bible_book(translation_code, book_code):
    connection = get_db()
    translation = _approved_translation(connection, translation_code)
    if translation is None:
        abort(404)
    books = list_books(connection, translation_code)
    book = next((item for item in books if item["code"] == book_code), None)
    if book is None:
        abort(404)
    chapters = get_book_chapters(connection, translation_code, book_code)
    return render_template(
        "bible/book.html",
        translation=translation,
        books=books,
        book=book,
        chapters=chapters,
        favorite_notice=_favorite_notice(),
    )


@pages.get("/biblia/<translation_code>/<book_code>/<int:chapter_number>")
def bible_chapter(translation_code, book_code, chapter_number):
    connection = get_db()
    translation = _approved_translation(connection, translation_code)
    if translation is None:
        abort(404)
    books = list_books(connection, translation_code)
    book = next((item for item in books if item["code"] == book_code), None)
    if book is None:
        abort(404)
    chapters = get_book_chapters(connection, translation_code, book_code)
    chapter_numbers = [chapter["chapter_number"] for chapter in chapters]
    if chapter_number not in chapter_numbers:
        abort(404)
    verses = list_chapter_verses(
        connection,
        translation_code,
        book_code,
        chapter_number,
    )
    if not verses:
        abort(404)
    notes_by_verse = {}
    for note in list_verse_notes(
        connection,
        [verse["verse_id"] for verse in verses],
    ):
        notes_by_verse.setdefault(note["verse_id"], []).append(dict(note))
    verses = [
        {**dict(verse), "notes": notes_by_verse.get(verse["verse_id"], [])}
        for verse in verses
    ]
    favorite_ids = list_verse_favorite_ids_for_chapter(
        connection,
        translation_code,
        book_code,
        chapter_number,
        _owner_key(),
    )
    annotations_by_verse = list_annotations_for_chapter(
        connection,
        translation_code,
        book_code,
        chapter_number,
        _owner_key(),
    )
    highlights_by_verse = get_highlights_for_chapter(
        connection,
        translation_code,
        book_code,
        chapter_number,
        _owner_key(),
    )
    destinations = _chapter_destinations(connection, translation_code, books)
    destination_index = next(
        index
        for index, destination in enumerate(destinations)
        if destination["book_code"] == book_code
        and destination["chapter_number"] == chapter_number
    )
    previous_destination = (
        destinations[destination_index - 1] if destination_index > 0 else None
    )
    next_destination = (
        destinations[destination_index + 1]
        if destination_index + 1 < len(destinations)
        else None
    )
    return render_template(
        "bible/chapter.html",
        translation=translation,
        books=books,
        book=book,
        chapters=chapters,
        verses=verses,
        favorite_ids=favorite_ids,
        annotations_by_verse=annotations_by_verse,
        highlights_by_verse=highlights_by_verse,
        highlight_colors=HIGHLIGHT_COLOR_LABELS,
        highlight_categories=HIGHLIGHT_CATEGORY_LABELS,
        previous_chapter=(
            chapter_numbers[chapter_numbers.index(chapter_number) - 1]
            if chapter_numbers.index(chapter_number) > 0
            else None
        ),
        next_chapter=(
            chapter_numbers[chapter_numbers.index(chapter_number) + 1]
            if chapter_numbers.index(chapter_number) + 1 < len(chapter_numbers)
            else None
        ),
        previous_destination=previous_destination,
        next_destination=next_destination,
        quick_jump_books=books,
        favorite_notice=_favorite_notice(),
        personal_notice=_personal_notice("annotation_status", "highlight_status"),
    )


@pages.get("/reflexoes")
def reflections_index():
    raw_page = request.args.get("page", "1")
    try:
        page_number = int(raw_page)
    except ValueError:
        abort(404)
    if page_number < 1:
        abort(404)

    connection = get_db()
    total = count_published_reflections(connection)
    page_count = (total + REFLECTIONS_PER_PAGE - 1) // REFLECTIONS_PER_PAGE
    if page_number > max(page_count, 1):
        abort(404)

    reflections = serialize_reflections(
        list_published_reflections(
            connection,
            limit=REFLECTIONS_PER_PAGE,
            offset=(page_number - 1) * REFLECTIONS_PER_PAGE,
        )
    )
    return render_template(
        "reflections/index.html",
        reflections=reflections,
        total=total,
        page=page_number,
        page_count=page_count,
        previous_url=(
            url_for("pages.reflections_index", page=page_number - 1)
            if page_number > 1
            else None
        ),
        next_url=(
            url_for("pages.reflections_index", page=page_number + 1)
            if page_number < page_count
            else None
        ),
    )


@pages.get("/reflexoes/<int:reflection_id>")
def reflection_detail(reflection_id):
    reflection = get_published_reflection(get_db(), reflection_id)
    if reflection is None:
        abort(404)
    reflection_favorite_id = get_reflection_favorite_id(
        get_db(), reflection_id, _owner_key()
    )
    return render_template(
        "reflections/detail.html",
        reflection=serialize_reflection(reflection),
        reflection_favorite_id=reflection_favorite_id,
        reflection_favorited=reflection_favorite_id is not None,
        favorite_notice=_favorite_notice(),
    )


@pages.get("/favoritos")
def favorites_index():
    requested_filter = request.args.get("tipo", "todos")
    if requested_filter not in FAVORITE_FILTERS:
        abort(404)
    highlight_category = request.args.get("categoria") or None
    highlight_color = request.args.get("cor") or None
    if highlight_category not in (None, *HIGHLIGHT_CATEGORIES):
        abort(404)
    if highlight_color not in (None, *HIGHLIGHT_COLORS):
        abort(404)

    favorites = serialize_favorites(
        list_favorites(
            get_db(),
            _owner_key(),
            FAVORITE_FILTERS[requested_filter],
            highlight_category=highlight_category,
            highlight_color=highlight_color,
        ),
        current_app.config["DAILY_READING_TIMEZONE"],
    )
    return render_template(
        "favorites/index.html",
        favorites=favorites,
        selected_filter=requested_filter,
        selected_category=highlight_category,
        selected_color=highlight_color,
        highlight_categories=HIGHLIGHT_CATEGORY_LABELS,
        highlight_colors=HIGHLIGHT_COLOR_LABELS,
        favorite_notice=_favorite_notice(),
        personal_notice=_personal_notice("highlight_status"),
    )


@pages.get("/anotacoes")
def annotations_index():
    rows = list_annotations(get_db(), _owner_key())
    annotations = []
    for row in rows:
        entry = dict(row)
        entry["reference"] = (
            f'{entry["book_name"]} {entry["chapter_number"]}:'
            f'{entry["verse_number"]}'
        )
        annotations.append(entry)
    return render_template(
        "personal/annotations.html",
        annotations=annotations,
        personal_notice=_personal_notice("annotation_status"),
    )


@pages.post("/anotacoes/salvar")
def save_verse_annotation():
    _validate_personal_post()
    verse_id = _positive_form_integer("verse_id")
    try:
        note_text = validate_annotation(request.form.get("note_text", ""))
    except ValueError:
        abort(400)
    connection = get_db()
    verse = connection.execute(
        """
        SELECT verses.id, translations.code AS translation_code,
               books.code AS book_code, chapters.chapter_number,
               verses.verse_number
        FROM verses
        JOIN translations ON translations.id = verses.translation_id
        JOIN chapters ON chapters.id = verses.chapter_id
        JOIN books ON books.id = chapters.book_id
        WHERE verses.id = ? AND translations.license_status = 'approved'
        """,
        (verse_id,),
    ).fetchone()
    if verse is None:
        abort(404)
    save_annotation(connection, verse_id, note_text, _owner_key())
    connection.commit()
    return redirect(
        url_for(
            "pages.bible_chapter",
            translation_code=verse["translation_code"],
            book_code=verse["book_code"],
            chapter_number=verse["chapter_number"],
            annotation_status="saved",
            _anchor=f'verse-{verse["verse_number"]}',
        )
    )


@pages.post("/anotacoes/<int:annotation_id>/excluir")
def remove_verse_annotation(annotation_id):
    _validate_personal_post()
    connection = get_db()
    source = connection.execute(
        """
        SELECT translations.code AS translation_code, books.code AS book_code,
               chapters.chapter_number, verses.verse_number
        FROM verse_annotations
        JOIN verses ON verses.id = verse_annotations.verse_id
        JOIN translations ON translations.id = verses.translation_id
        JOIN chapters ON chapters.id = verses.chapter_id
        JOIN books ON books.id = chapters.book_id
        WHERE verse_annotations.id = ?
          AND verse_annotations.owner_key = ?
          AND translations.license_status = 'approved'
        """,
        (annotation_id, _owner_key()),
    ).fetchone()
    if source is None or not delete_annotation(
        connection, annotation_id, _owner_key()
    ):
        abort(404)
    connection.commit()
    return redirect(
        url_for(
            "pages.bible_chapter",
            translation_code=source["translation_code"],
            book_code=source["book_code"],
            chapter_number=source["chapter_number"],
            annotation_status="deleted",
            _anchor=f'verse-{source["verse_number"]}',
        )
    )


@pages.post("/favoritos/marcacao")
def mark_favorite_verse():
    _validate_personal_post()
    verse_number = _positive_form_integer("verse_number")
    try:
        color, category = validate_highlight(
            request.form.get("color", ""),
            request.form.get("category", ""),
        )
    except ValueError:
        abort(400)
    translation_code = request.form.get("translation_code", "").strip()
    book_code = request.form.get("book_code", "").strip()
    verse = get_favoriteable_verse_by_reference(
        get_db(),
        translation_code,
        book_code,
        _positive_form_integer("chapter_number"),
        verse_number,
    )
    if verse is None:
        abort(404)
    connection = get_db()
    add_verse_favorite(connection, verse, _owner_key())
    favorite = connection.execute(
        """
        SELECT id FROM favorites
        WHERE owner_key = ? AND favorite_type = 'verse'
          AND translation_id = ? AND chapter_id = ? AND verse_number = ?
        """,
        (
            _owner_key(),
            verse["translation_id"],
            verse["chapter_id"],
            verse_number,
        ),
    ).fetchone()
    if favorite is None:
        abort(500)
    save_highlight(connection, favorite["id"], color, category)
    connection.commit()
    return redirect(
        url_for(
            "pages.bible_chapter",
            translation_code=translation_code,
            book_code=book_code,
            chapter_number=request.form.get("chapter_number"),
            highlight_status="highlight-saved",
            _anchor=f"verse-{verse_number}",
        )
    )


@pages.post("/favoritos/<int:favorite_id>/marcacao/remover")
def remove_verse_highlight(favorite_id):
    _validate_personal_post()
    if not delete_highlight(get_db(), favorite_id, _owner_key()):
        abort(404)
    get_db().commit()
    return redirect(
        url_for(
            "pages.favorites_index",
            highlight_status="highlight-removed",
        )
    )


@pages.post("/favoritos/<int:favorite_id>/marcacao")
def update_verse_highlight(favorite_id):
    _validate_personal_post()
    try:
        color, category = validate_highlight(
            request.form.get("color", ""),
            request.form.get("category", ""),
        )
    except ValueError:
        abort(400)
    connection = get_db()
    favorite = connection.execute(
        """
        SELECT id FROM favorites
        WHERE id = ? AND owner_key = ? AND favorite_type = 'verse'
        """,
        (favorite_id, _owner_key()),
    ).fetchone()
    if favorite is None:
        abort(404)
    save_highlight(connection, favorite_id, color, category)
    connection.commit()
    return redirect(
        url_for("pages.favorites_index", highlight_status="highlight-saved")
    )


@pages.get("/oracao")
def prayer_journal():
    status_filter = request.args.get("status", "all")
    type_filter = request.args.get("tipo", "all")
    if status_filter not in {"all", "active", "answered"}:
        abort(404)
    if type_filter not in {"all", *PRAYER_TYPES}:
        abort(404)
    edit_id_text = request.args.get("editar")
    entry_to_edit = None
    if edit_id_text:
        try:
            edit_id = int(edit_id_text)
        except ValueError:
            abort(404)
        if edit_id < 1:
            abort(404)
        entry_to_edit = get_prayer_entry(get_db(), edit_id, _owner_key())
        if entry_to_edit is None or entry_to_edit["status"] != "active":
            abort(404)
    entries = list_prayer_entries(
        get_db(),
        _owner_key(),
        None if status_filter == "all" else status_filter,
        None if type_filter == "all" else type_filter,
    )
    return render_template(
        "personal/prayer_journal.html",
        entries=entries,
        selected_status=status_filter,
        selected_type=type_filter,
        entry_to_edit=entry_to_edit,
        personal_notice=_personal_notice("prayer_status"),
    )


@pages.post("/oracao/salvar")
def save_prayer():
    _validate_personal_post()
    try:
        entry_type, title, description = validate_prayer_entry(
            request.form.get("entry_type", ""),
            request.form.get("title", ""),
            request.form.get("description", ""),
        )
    except ValueError:
        abort(400)
    connection = get_db()
    entry_id = request.form.get("entry_id", "").strip()
    if entry_id:
        try:
            identifier = int(entry_id)
        except ValueError:
            abort(404)
        if identifier < 1 or not update_prayer_entry(
            connection,
            identifier,
            entry_type,
            title,
            description,
            _owner_key(),
        ):
            abort(404)
        status = "updated"
    else:
        create_prayer_entry(
            connection,
            entry_type,
            title,
            description,
            _owner_key(),
        )
        status = "created"
    connection.commit()
    return redirect(url_for("pages.prayer_journal", prayer_status=status))


@pages.post("/oracao/<int:entry_id>/respondida")
def answer_prayer(entry_id):
    _validate_personal_post()
    if not mark_prayer_answered(
        get_db(), entry_id, _owner_key()
    ):
        abort(404)
    get_db().commit()
    return redirect(url_for("pages.prayer_journal", prayer_status="answered"))


@pages.post("/oracao/<int:entry_id>/excluir")
def remove_prayer(entry_id):
    _validate_personal_post()
    if not delete_prayer_entry(
        get_db(), entry_id, _owner_key()
    ):
        abort(404)
    get_db().commit()
    return redirect(
        url_for("pages.prayer_journal", prayer_status="prayer-deleted")
    )


@pages.get("/configuracoes")
def reader_settings():
    return render_template(
        "personal/settings.html",
        preferences=get_reader_preferences(get_db(), _owner_key()),
        personal_notice=_personal_notice("settings_status"),
    )


@pages.post("/configuracoes")
def save_reader_settings():
    _validate_personal_post()
    try:
        theme, font_scale, verse_spacing = validate_reader_preferences(
            request.form.get("theme", ""),
            request.form.get("font_scale", ""),
            request.form.get("verse_spacing", ""),
        )
    except ValueError:
        abort(400)
    save_reader_preferences(
        get_db(),
        theme,
        font_scale,
        verse_spacing,
        _owner_key(),
    )
    get_db().commit()
    return redirect(
        url_for("pages.reader_settings", settings_status="settings-saved")
    )


@pages.post("/favoritos/versiculo")
def favorite_verse():
    _validate_favorite_post()
    verse_number = _positive_form_integer("verse_number")
    connection = get_db()
    if "daily_reading_id" in request.form:
        daily_reading_id = _positive_form_integer("daily_reading_id")
        verse = get_favoriteable_verse(
            connection,
            daily_reading_id,
            verse_number,
        )
    else:
        translation_code = request.form.get("translation_code", "").strip()
        book_code = request.form.get("book_code", "").strip()
        if not translation_code or not book_code:
            abort(404)
        verse = get_favoriteable_verse_by_reference(
            connection,
            translation_code,
            book_code,
            _positive_form_integer("chapter_number"),
            verse_number,
        )
    if verse is None:
        abort(404)

    added = add_verse_favorite(connection, verse, _owner_key())
    connection.commit()
    return _redirect_with_favorite_status(
        url_for("pages.home"),
        "verse-added" if added else "verse-exists",
    )


@pages.post("/favoritos/reflexao")
def favorite_reflection():
    _validate_favorite_post()
    reflection_id = _positive_form_integer("reflection_id")
    connection = get_db()
    reflection = get_published_reflection(connection, reflection_id)
    if reflection is None:
        abort(404)

    added = add_reflection_favorite(connection, reflection_id, _owner_key())
    connection.commit()
    return _redirect_with_favorite_status(
        url_for("pages.reflection_detail", reflection_id=reflection_id),
        "reflection-added" if added else "reflection-exists",
    )


@pages.post("/favoritos/<int:favorite_id>/remover")
def unfavorite(favorite_id):
    _validate_favorite_post()
    connection = get_db()
    favorite_type = get_favorite_type(connection, favorite_id, _owner_key())
    if favorite_type is None or not remove_favorite(
        connection, favorite_id, _owner_key()
    ):
        abort(404)

    connection.commit()
    status = (
        "verse-removed"
        if favorite_type == "verse"
        else "reflection-removed"
    )
    return _redirect_with_favorite_status(url_for("pages.favorites_index"), status)


@pages.get("/progresso")
def progress_dashboard():
    today = local_today(current_app.config["DAILY_READING_TIMEZONE"])
    try:
        month_start = validate_month(
            request.args.get("ano"),
            request.args.get("mes"),
            today,
        )
    except ValueError:
        abort(404)

    connection = get_db()
    achievements = sync_achievements(
        connection, _owner_key(), today
    )
    connection.commit()
    history = serialize_completion_history(
        list_completion_history(connection, _owner_key())
    )
    metrics = calculate_progress_metrics(history, today)
    month_start_date, month_end_date = month_days_bounds(month_start)
    completed_dates = list_completion_dates(
        connection,
        month_start_date,
        month_end_date,
        _owner_key(),
    )
    calendar_data = serialize_month_calendar(month_start, completed_dates, today)
    weekly_goal = get_weekly_goal(connection, _owner_key())
    week_start = today.fromordinal(today.toordinal() - today.weekday())
    week_end = today.fromordinal(week_start.toordinal() + 6)
    weekly_dates = set(
        list_completion_dates(
            connection,
            week_start.isoformat(),
            min(week_end, today).isoformat(),
            _owner_key(),
        )
    )
    weekly_days = [
        {
            "date": (week_start.fromordinal(week_start.toordinal() + offset)).isoformat(),
            "label": ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")[offset],
            "day_number": week_start.fromordinal(week_start.toordinal() + offset).day,
            "completed": (
                week_start.fromordinal(week_start.toordinal() + offset).isoformat()
                in weekly_dates
            ),
            "is_today": offset == today.weekday(),
            "available": offset <= today.weekday(),
        }
        for offset in range(7)
    ]
    weekly_remaining = max((weekly_goal or 0) - metrics["weekly_total"], 0)
    return render_template(
        "progress/index.html",
        today=today,
        today_iso=today.isoformat(),
        calendar=calendar_data,
        weekdays=weekday_headers(),
        metrics=metrics,
        history=history,
        weekly_goal=weekly_goal,
        weekly_days=weekly_days,
        weekly_remaining=weekly_remaining,
        weekly_percent=(
            min(metrics["weekly_total"] * 100 // weekly_goal, 100)
            if weekly_goal
            else 0
        ),
        monthly_total=len(completed_dates),
        progress_notice=_progress_notice(),
        progress_csrf_token=csrf_token("progress"),
        achievement_summary=[
            item for item in achievements if item["unlocked"]
        ][:3],
        achievement_total=len(achievements),
        achievement_unlocked_count=sum(item["unlocked"] for item in achievements),
    )


@pages.get("/conquistas")
def achievements_dashboard():
    today = local_today(current_app.config["DAILY_READING_TIMEZONE"])
    connection = get_db()
    achievements = sync_achievements(
        connection, _owner_key(), today
    )
    connection.commit()
    return render_template(
        "achievements/index.html",
        achievements=achievements,
        unlocked_count=sum(item["unlocked"] for item in achievements),
    )


def _progress_notice():
    notices = {
        "completion-added": "Leitura marcada como concluída.",
        "completion-exists": "Essa leitura já está concluída.",
        "completion-removed": "Conclusão desfeita.",
        "goal-saved": "Meta semanal atualizada.",
    }
    return notices.get(request.args.get("status"))


@pages.post("/progresso/concluir")
def complete_reading():
    _validate_progress_post()
    daily_reading_id = _positive_form_integer("daily_reading_id")
    today = local_today(current_app.config["DAILY_READING_TIMEZONE"])
    reading_date = today.isoformat()

    connection = get_db()
    reading = connection.execute(
        """
        SELECT daily_readings.reading_date, translations.code
        FROM daily_readings
        JOIN translations ON translations.id = daily_readings.translation_id
        WHERE daily_readings.id = ?
          AND translations.license_status = 'approved'
        """,
        (daily_reading_id,),
    ).fetchone()
    if reading is None or reading["reading_date"] != reading_date:
        abort(404)

    valid_reading = get_reading_for_completion(
        connection,
        reading_date,
        reading["code"],
    )
    if valid_reading is None or valid_reading["daily_reading_id"] != daily_reading_id:
        abort(404)

    added = add_completion(
        connection,
        daily_reading_id,
        reading_date,
        _owner_key(),
    )
    sync_achievements(connection, _owner_key(), today)
    connection.commit()
    status = "completion-added" if added else "completion-exists"
    return redirect(url_for("pages.progress_dashboard", status=status))


@pages.post("/progresso/<int:completion_id>/desfazer")
def undo_completion(completion_id):
    _validate_progress_post()
    connection = get_db()
    if not remove_completion(
        connection,
        completion_id,
        _owner_key(),
    ):
        abort(404)
    today = local_today(current_app.config["DAILY_READING_TIMEZONE"])
    sync_achievements(connection, _owner_key(), today)
    connection.commit()
    return redirect(
        url_for("pages.progress_dashboard", status="completion-removed")
    )


@pages.post("/progresso/meta")
def update_reading_goal():
    _validate_progress_post()
    weekly_target = _weekly_goal_form_integer()
    connection = get_db()
    save_weekly_goal(connection, weekly_target, _owner_key())
    today = local_today(current_app.config["DAILY_READING_TIMEZONE"])
    sync_achievements(connection, _owner_key(), today)
    connection.commit()
    return redirect(url_for("pages.progress_dashboard", status="goal-saved"))
