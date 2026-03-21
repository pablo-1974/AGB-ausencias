# context.py
from datetime import datetime, date
from config import settings

def ctx(request, user, **extra):
    """
    Contexto global para TODAS las plantillas.
    Se asegura de incluir:
      - now_dt → datetime actual (fecha y hora), necesario para base.html
      - today  → fecha (opcional)
    """
    now_dt = datetime.now()

    base = {
        "request": request,
        "user": user,

        # Título por defecto
        "title": extra.get("title", settings.APP_NAME),

        # Información institucional
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,

        # Variables que necesita el header 🔥
        "year": now_dt.year,
        "today": now_dt.date(),
        "now_dt": now_dt,   # ← ESTA ES LA CLAVE IMPORTANTE
    }

    base.update(extra or {})
    return base
