"""
Turkish text normalization and case-folding utilities.
Correctly handles Turkish dotted/dotless I (İ/i, I/ı) and special characters.
"""
from __future__ import annotations

import unicodedata

TURKISH_LOWER_MAP = {
    'İ': 'i',
    'I': 'ı',
    'Ğ': 'ğ',
    'Ü': 'ü',
    'Ş': 'ş',
    'Ö': 'ö',
    'Ç': 'ç',
}

TURKISH_UPPER_MAP = {
    'i': 'İ',
    'ı': 'I',
    'ğ': 'Ğ',
    'ü': 'Ü',
    'ş': 'Ş',
    'ö': 'Ö',
    'ç': 'Ç',
}

# Mapping to ASCII simplified characters (for fuzzy accent-insensitive search)
ASCII_SIMPLIFY_MAP = {
    'ç': 'c', 'Ç': 'c',
    'ğ': 'g', 'Ğ': 'g',
    'ı': 'i', 'I': 'i',
    'i': 'i', 'İ': 'i',
    'ö': 'o', 'Ö': 'o',
    'ş': 's', 'Ş': 's',
    'ü': 'u', 'Ü': 'u',
}


def turkish_casefold(text: str) -> str:
    """
    Lowercases a string applying Turkish rules for 'İ' and 'I'.
    Example:
        'İSTANBUL' -> 'istanbul'
        'IŞIK' -> 'ışık'
    """
    if not text:
        return ""
    chars = [TURKISH_LOWER_MAP.get(c, c) for c in text]
    return "".join(chars).lower()


def turkish_strip_accents(text: str) -> str:
    """
    Converts Turkish accented characters to closest ASCII equivalent.
    Useful for fallback fuzzy matching.
    Example:
        'İş Sağlığı' -> 'is sagligi'
    """
    if not text:
        return ""
    folded = turkish_casefold(text)
    simplified = "".join(ASCII_SIMPLIFY_MAP.get(c, c) for c in folded)
    # Further strip remaining accents via unicode normalization
    nfkd = unicodedata.normalize('NFKD', simplified)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def turkish_contains(needle: str, haystack: str) -> bool:
    """
    Returns True if needle is found within haystack, using Turkish-aware
    case-insensitive matching, and falling back to accent-stripped matching.
    """
    if not needle or not haystack:
        return False

    n_fold = turkish_casefold(needle)
    h_fold = turkish_casefold(haystack)

    if n_fold in h_fold:
        return True

    # Fallback to accent-insensitive comparison
    n_ascii = turkish_strip_accents(needle)
    h_ascii = turkish_strip_accents(haystack)
    return n_ascii in h_ascii
