from datetime import date
from config import settings

def ctx(request, user, **extra):
    base = {
        "request": request,
        "user": user,
        "title": extra.get("title", settings.APP_NAME),
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
        "year": date.today().year,
    }
    base.update(extra or {})
    return base
