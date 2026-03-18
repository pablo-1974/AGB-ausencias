# utils/sort.py
import unicodedata

def normalize_name(name: str) -> str:
    """
    Normaliza un nombre quitando tildes y diacríticos para ordenación alfabética:
    Á -> A, é -> e, ü -> u, etc.
    Mantiene Ñ como expresión propia, no la convierte en N.
    Devuelve siempre minúsculas.
    """
    if not name:
        return ""
    
    # Normalizar NFD permite separar letras y tildes
    nf = unicodedata.normalize("NFD", name)
    # Eliminamos solo los diacríticos, pero no tocamos la Ñ (que no es combining)
    cleaned = "".join(ch for ch in nf if not unicodedata.combining(ch))
    return cleaned.lower()
