"""Case- and diacritic-insensitive text matching."""
import unicodedata


def norm(s: str | None) -> str:
    if not s:
        return ""
    s = s.lower().replace("ł", "l")
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
