# utils.py
from typing import List

# ---------------------------
# Bitmask de horas (1ª..6ª)
# ---------------------------

def hours_list_to_mask(hours: List[int], all_selected: bool = False) -> int:
    """
    hours → lista con números 1..6
    all_selected=True → activa todas las horas (1..6)
    """
    if all_selected:
        return (1 << 6) - 1  # 0b111111 = 63
    m = 0
    for h in hours:
        if 1 <= h <= 6:
            m |= (1 << (h - 1))
    return m


def mask_to_hour_list(mask: int) -> List[int]:
    """Devuelve lista de horas activas en máscara."""
    out = []
    for h in range(1, 7):  # 1..6
        if mask & (1 << (h - 1)):
            out.append(h)
    return out


# ---------------------------
# Mapeos horario
# ---------------------------

DAY_NAMES = {
    0: "Lunes",
    1: "Martes",
    2: "Miércoles",
    3: "Jueves",
    4: "Viernes",
    5: "Sábado",
    6: "Domingo",
}

HOUR_LABELS = {
    0: "1ª",
    1: "2ª",
    2: "3ª",
    3: "RECREO",
    4: "4ª",
    5: "5ª",
    6: "6ª",
}

def day_name(idx: int) -> str:
    return DAY_NAMES.get(idx, "?")


def hour_label(idx: int) -> str:
    return HOUR_LABELS.get(idx, "?")


# ---------------------------
# Validaciones Excel
# ---------------------------

def require_columns(df, required: List[str]):
    """Lanza ValueError si faltan columnas."""
    cols = [c.strip().lower() for c in df.columns]
    missing = [c for c in required if c.lower() not in cols]
    if missing:
        raise ValueError(f"Faltan columnas obligatorias en Excel: {missing}")

# -----------------------------
# Ordenación alfabética
# -----------------------------
import unicodedata

def normalize_name(name: str) -> str:
    """
    Normaliza un nombre quitando tildes y diacríticos para ordenación alfabética:
    Á -> A, é -> e, ü -> u, etc.
    Mantiene Ñ como expresión propia.
    Devuelve siempre minúsculas.
    """
    if not name:
        return ""

    nf = unicodedata.normalize("NFD", name)
    cleaned = "".join(ch for ch in nf if not unicodedata.combining(ch))
    return cleaned.lower()
