"""Portable built-in avatars; no model HTML, remote URLs, or generated binary files."""

AVATAR_PATHS = {
    "strength": '<path d="M28 64h72M28 46v36M38 50v28M90 50v28M100 46v36"/>',
    "cardio": (
        '<circle cx="76" cy="29" r="8"/>'
        '<path d="m47 55 19-12 15 16 17 4M66 43l-9 29-22 20m22-20 20 7 5 22M47 55l-13 2"/>'
    ),
    "mobility": (
        '<circle cx="64" cy="34" r="8"/>'
        '<path d="M64 50v28m0-19-25-9-7-19m32 28 25-9 7-19M64 78 46 100m18-22 18 22"/>'
    ),
    "stretching": (
        '<circle cx="69" cy="32" r="8"/>'
        '<path d="m63 48-12 27 26 5 15 20M51 75 31 99m28-43 27-3 14-17M59 56 38 44"/>'
    ),
    "other": ('<circle cx="64" cy="64" r="31"/><path d="M49 64h30M64 49v30"/>'),
}


def avatar_svg(key: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" '
        'aria-label="自定义动作头像">'
        '<rect width="128" height="128" rx="28" fill="#e8f2ee"/>'
        '<g fill="none" stroke="#356e60" stroke-width="6" stroke-linecap="round" '
        f'stroke-linejoin="round">{AVATAR_PATHS.get(key, AVATAR_PATHS["other"])}</g></svg>'
    )
