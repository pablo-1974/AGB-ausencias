# project/app.py
from datetime import datetime
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.proxy_headers import ProxyHeadersMiddleware
from starlette.responses import RedirectResponse, JSONResponse

from .config import settings
from .auth import router as auth_router, setup_session

# -------------------------------------------------
# App base
# -------------------------------------------------
app = FastAPI(title=settings.APP_NAME)

# Proxy headers (Render / reverse proxy)
# - Asegura que request.url.scheme sea https cuando Render manda X-Forwarded-Proto
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# Archivos estáticos (logo, estilos…)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates Jinja2
templates = Jinja2Templates(directory="templates")
app.state.templates = templates

# Sesiones (cookies seguras)
setup_session(app)  # añade SessionMiddleware con SECRET_KEY (ver auth.setup_session)

# -------------------------------------------------
# Contexto común para plantillas
# -------------------------------------------------
APP_NAME = "Ausencias de profesores"
INSTITUTION_NAME = "IES Antonio García Bellido"
LOGO_PATH = "/static/logo.png"

def _tpl(request: Request, **extra):
    base_ctx = {
        "request": request,
        "title": APP_NAME,
        "app_name": APP_NAME,
        "institution_name": INSTITUTION_NAME,
        "logo_path": LOGO_PATH,
        "now": datetime.now(),
    }
    base_ctx.update(extra or {})
    return base_ctx

# -------------------------------------------------
# Routers
# -------------------------------------------------
# Autenticación
app.include_router(auth_router)

# Cuando tengas listos los demás routers, descomenta:
try:
    from .services import pdf_daily, pdf_monthly  # noqa
    # from .reports import router as reports_router
    # app.include_router(reports_router, prefix="/reports", tags=["reports"])
except Exception:
    pass

try:
    # from .schedule import router as schedule_router
    # app.include_router(schedule_router, prefix="/schedule", tags=["schedule"])
    pass
except Exception:
    pass

try:
    # from .absences import router as absences_router
    # app.include_router(absences_router, prefix="/absences", tags=["absences"])
    pass
except Exception:
    pass

try:
    # from .leaves import router as leaves_router
    # app.include_router(leaves_router, prefix="/leaves", tags=["leaves"])
    pass
except Exception:
    pass

# -------------------------------------------------
# Rutas base
# -------------------------------------------------
@app.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", _tpl(request))

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "ts": datetime.utcnow().isoformat()})

# -------------------------------------------------
# Manejo de errores (opcional, más amable)
# -------------------------------------------------
@app.exception_handler(404)
async def not_found(request: Request, exc):
    # Si quieres una plantilla 404.html, cámbialo
    return templates.TemplateResponse(
        "dashboard.html",
        _tpl(request, message="Página no encontrada"),
        status_code=404
    )

@app.exception_handler(500)
async def server_error(request: Request, exc):
    return templates.TemplateResponse(
        "dashboard.html",
        _tpl(request, message="Error interno. Intenta de nuevo."),
        status_code=500
    )
