NOTE_MAX_LENGTH = 5000
PRAYER_TITLE_MAX_LENGTH = 120
PRAYER_DESCRIPTION_MAX_LENGTH = 5000
HIGHLIGHT_COLORS = {"gold", "blue", "green", "rose", "lavender"}
HIGHLIGHT_CATEGORIES = {
    "faith",
    "hope",
    "love",
    "gratitude",
    "prayer",
    "reflection",
}
PRAYER_TYPES = {"request", "gratitude"}
THEMES = {"light", "dark"}
VERSE_SPACINGS = {"comfortable", "compact"}


def validate_annotation(text):
    value = text.strip()
    if not value or len(value) > NOTE_MAX_LENGTH:
        raise ValueError("A anotação deve ter entre 1 e 5.000 caracteres.")
    return value


def validate_prayer_entry(entry_type, title, description):
    normalized_type = entry_type.strip()
    normalized_title = title.strip()
    normalized_description = description.strip()
    if normalized_type not in PRAYER_TYPES:
        raise ValueError("Escolha pedidos de oração ou agradecimentos.")
    if not normalized_title or len(normalized_title) > PRAYER_TITLE_MAX_LENGTH:
        raise ValueError("O título deve ter entre 1 e 120 caracteres.")
    if (
        not normalized_description
        or len(normalized_description) > PRAYER_DESCRIPTION_MAX_LENGTH
    ):
        raise ValueError("A descrição deve ter entre 1 e 5.000 caracteres.")
    return normalized_type, normalized_title, normalized_description


def validate_highlight(color, category):
    normalized_color = color.strip()
    normalized_category = category.strip()
    if normalized_color not in HIGHLIGHT_COLORS:
        raise ValueError("Escolha uma cor de marcação válida.")
    if normalized_category not in HIGHLIGHT_CATEGORIES:
        raise ValueError("Escolha uma categoria válida.")
    return normalized_color, normalized_category


def validate_reader_preferences(theme, font_scale, verse_spacing):
    normalized_theme = theme.strip()
    normalized_spacing = verse_spacing.strip()
    try:
        normalized_scale = int(font_scale)
    except (TypeError, ValueError) as error:
        raise ValueError("O tamanho da fonte deve estar entre 80% e 150%.") from error
    if normalized_theme not in THEMES:
        raise ValueError("Escolha um tema válido.")
    if normalized_scale not in range(80, 151, 10):
        raise ValueError("O tamanho da fonte deve estar entre 80% e 150%.")
    if normalized_spacing not in VERSE_SPACINGS:
        raise ValueError("Escolha um espaçamento válido.")
    return normalized_theme, normalized_scale, normalized_spacing
