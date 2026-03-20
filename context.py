# context.py
from datetime import date, datetime
from config import settings

def ctx(request, user, **extra):
    now = datetime.now()

    base = {
        "request": request,
        "user": user,

        # Título por defecto
        "title": extra.get("title", settings.APP_NAME),

        # Datos de institución / interfaz
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,

        # Fechas globales para BASE.HTML 🔥
        "year": now.year,                # año actual
        "today": now.date(),             # fecha actual (YYYY-MM-DD)
        "now": now.strftime("%H:%M"),    # hora actual HH:MM (lo que antes usaba el header)
        "now_dt": now,                   # datetime completo (por si se usa en plantillas)
    }

    base.update(extra or {})
    return base
