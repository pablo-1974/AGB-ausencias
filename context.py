from datetime import date, datetime
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
        "now": date.today(),
        "now_dt": datetime.now(),
    }
    base.update(extra or {})
    return base
