import re
import unicodedata


MAX_NARRATION_CONTEXT_CHARS = 360
SCENE_VIEWPOINTS = (
    "Wide establishing documentary composition showing the location and spatial relationships",
    "Medium observational documentary shot centered on the main people and their activity",
    "Close documentary detail emphasizing the most important object, gesture, or expression",
    "Elevated documentary viewpoint revealing the arrangement of the scene below",
    "Low-angle documentary composition with strong foreground depth and a distinct background",
    "Over-the-shoulder documentary perspective following the primary subject's point of view",
    "Side-profile documentary composition capturing movement across the frame",
    "Layered documentary composition with meaningful foreground, middle ground, and background",
    "Environmental portrait placing the main subject clearly inside the described setting",
    "Candid documentary moment focused on a specific physical interaction",
    "Long-lens documentary view isolating the key event from its surroundings",
    "Symmetrical documentary composition built around the scene's central object or action",
)
REPEATED_CONTEXT_FOCUSES = (
    "Emphasize a different supporting person, object, and background from earlier scenes",
    "Show a later physical moment with a different subject arrangement and camera direction",
    "Focus on a separate environmental detail and a clearly different visual hierarchy",
    "Use a different distance, foreground element, and direction of movement",
)


def remove_vietnamese_accents(text: str) -> str:
    """Normalize and strip Vietnamese diacritics for robust text comparison."""
    nfkd = unicodedata.normalize("NFKD", str(text or ""))
    return (
        "".join(character for character in nfkd if not unicodedata.combining(character))
        .replace("đ", "d")
        .replace("Đ", "D")
    )


def normalize_visual_identity(text: str) -> str:
    """Remove structural scene labels so cosmetic numbering cannot hide duplicates."""
    normalized = remove_vietnamese_accents(str(text or "").casefold())
    normalized = re.sub(r"\b(?:scene|canh)\s*(?:number\s*)?#?\s*\d+\b", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return re.sub(r"[^\w\s]", " ", normalized).strip()


def _truncate_at_word_boundary(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    shortened = text[:max_chars].rsplit(" ", 1)[0].strip()
    return shortened or text[:max_chars].strip()


def _clean_narration_context(text: str) -> str:
    context = re.sub(r"https?://\S+", " ", str(text or ""))
    context = re.sub(r"\[IMAGE_URL:[^\]]*\]", " ", context, flags=re.IGNORECASE)
    context = re.sub(r"\s+", " ", context).strip(" \t\r\n-–—")
    return _truncate_at_word_boundary(context, MAX_NARRATION_CONTEXT_CHARS)


def translate_transcript_to_visual_action(
    transcript: str,
    *,
    video_title: str = "",
    reference_name: str = "",
    scene_index: int = 0,
    total_scenes: int = 10,
    prev_actions: list[str] | None = None,
) -> str:
    """Build a topic-agnostic visual direction grounded in each scene's narration."""
    del total_scenes
    narration = _clean_narration_context(transcript)
    if not narration:
        narration = _clean_narration_context(video_title) or "the current narration"

    viewpoint = SCENE_VIEWPOINTS[scene_index % len(SCENE_VIEWPOINTS)]
    reference_instruction = (
        f"Keep the supplied visual reference for {reference_name}. "
        if reference_name
        else ""
    )
    action = (
        f"{viewpoint}. {reference_instruction}"
        "Depict a concrete, physically observable moment grounded only in this narration: "
        f"{narration}"
    ).strip()

    narration_key = normalize_visual_identity(narration)
    previous_actions = prev_actions or []
    repeated_count = sum(
        narration_key and narration_key in normalize_visual_identity(previous)
        for previous in previous_actions
    )
    if repeated_count:
        focus = REPEATED_CONTEXT_FOCUSES[
            (repeated_count - 1) % len(REPEATED_CONTEXT_FOCUSES)
        ]
        action = f"{action}. {focus}."
    return re.sub(r"\s+", " ", action).strip()
