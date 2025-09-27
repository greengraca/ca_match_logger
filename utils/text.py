def capitalize_words(deck_name: str) -> str:
    lowercase_words = {"and", "but"}
    if "/" in deck_name:
        return deck_name
    words = deck_name.split()
    return " ".join(w.capitalize() if w.lower() not in lowercase_words else w.lower() for w in words)

def format_deck_name(deck_name: str) -> str:
    parts = deck_name.split("/")
    sorted_parts = sorted(part.strip().capitalize() for part in parts)
    return "/".join(sorted_parts)

MAX_EMBED_CHARS = 4000
MAX_MSG_CHARS = 2000
PAGE_HEADER = "**📜 Full Game Dump:**\n"

def paginate_text(entries: list[str], header: str = PAGE_HEADER, limit: int = MAX_MSG_CHARS) -> list[str]:
    pages, cur = [], header
    for e in entries:
        e = e.strip()
        if len(cur) + len(e) + 1 > limit:
            pages.append(cur.strip())
            cur = header + e + "\n"
        else:
            cur += e + "\n"
    if cur.strip() != header.strip():
        pages.append(cur.strip())
    return pages
