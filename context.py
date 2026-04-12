# context.py
from datetime import datetime
from config import settings

def ctx(request, user, **extra):
    """
    Contexto global para TODAS las plantillas.
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

        # ✅ URL para HTML (NO ruta de sistema)
        "logo_url": request.url_for("static", path="logo.png"),

        # Variables necesarias para el header
        "year": now_dt.year,
        "today": now_dt.date(),
        "now_dt": now_dt,
    }

    base.update(extra or {})
    return base
