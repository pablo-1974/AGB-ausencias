# app.py
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse, JSONResponse

from config import settings
from auth import router as auth_router, setup_session

# router de importación de profesores
from imports_teachers import router as teachers_import_router
# router de importación de clases y guardias
from imports_schedule import router as schedule_import_router
# router de horarios
from schedule_router import router as schedule_router
# router de listados de profesorado (pantalla + PDFs)
from teachers_router import router as teachers_router  # NEW
# router de bajas
from leaves_router import router as leaves_router
# router de ausencias
from absences_router import router as absences_router
# app.py (imports)
from reports_router import router as reports_router
# router de calendario
from config_calendar_router import router as calendar_router

# ------------------------------------------------------------
# Middleware de proxy (fallback tolerante)
#   Intento 1: import “antiguo” de Starlette (compat si bajas versión)
#   Intento 2: import recomendado actual de Uvicorn
#   Si ninguno existe, seguimos sin middleware (no rompe el arranque)
# ------------------------------------------------------------
ProxyHeadersMiddleware = None
try:
    from starlette.middleware.proxy_headers import ProxyHeadersMiddleware  # type: ignore
except Exception:
    try:
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware  # type: ignore
    except Exception:
        ProxyHeadersMiddleware = None

# ------------------------------------------------------------
# APP FASTAPI (configurada ya para Render + Neon)
# ------------------------------------------------------------
app = FastAPI(title=settings.APP_NAME)

# Asegurar https detrás de Render (X-Forwarded-Proto)
if ProxyHeadersMiddleware:
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# ------------------------------------------------------------
# STATIC y TEMPLATES
# ------------------------------------------------------------
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")
app.state.templates = templates

# ------------------------------------------------------------
# SESIONES (cookies)
# ------------------------------------------------------------
setup_session(app)

# ------------------------------------------------------------
# MIDDLEWARE: Cargar SIEMPRE el usuario en request.state.user
# ------------------------------------------------------------
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_session
from models import User

@app.middleware("http")
async def load_user(request: Request, call_next):
    """
    Carga el usuario autenticado en request.state.user para que esté
    disponible en TODAS las plantillas, sin depender de cada ruta.
    """
    request.state.user = None
    uid = request.session.get("uid") if request.session else None

    if uid:
        session: AsyncSession = await get_session()
        request.state.user = await session.get(User, uid)

    response = await call_next(request)
    return response


# ------------------------------------------------------------
# MIDDLEWARE: No cache en páginas sensibles
# ------------------------------------------------------------
@app.middleware("http")
async def no_cache_mw(request: Request, call_next):
    response = await call_next(request)
    nocache_paths = {"/", "/login", "/register-first-admin", "/register"}
    if request.url.path in nocache_paths or request.url.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# ------------------------------------------------------------
# INCLUIR RUTAS
# ------------------------------------------------------------
app.include_router(auth_router)
app.include_router(teachers_import_router)    # importar profesores
app.include_router(schedule_import_router)    # importar guardias y clases
app.include_router(schedule_router)           # ver/editar/imprimir horario
app.include_router(teachers_router)           # listados de profesorado
app.include_router(leaves_router)
app.include_router(absences_router)
app.include_router(reports_router)
app.include_router(calendar_router)

# DEBUG: mostrar errores de arranque
import sys
def debug_import_error(module_name: str):
    try:
        __import__(module_name)
    except Exception as e:
        print(f"ERROR AL IMPORTAR {module_name}:", e, file=sys.stderr)

debug_import_error("services.pdf_monthly")
debug_import_error("reports_router")
debug_import_error("leaves_router")
debug_import_error("teachers_router")

# ------------------------------------------------------------
# Contexto común
# ------------------------------------------------------------
APP_NAME = settings.APP_NAME
INSTITUTION_NAME = settings.INSTITUTION_NAME
LOGO_PATH = settings.LOGO_PATH

def tpl(request: Request, **extra):
    ctx = {
        "request": request,
        "title": APP_NAME,
        "app_name": APP_NAME,
        "institution_name": INSTITUTION_NAME,
        "logo_path": LOGO_PATH,
        "now_dt": datetime.now(),
    }
    ctx.update(extra or {})
    return ctx

# ------------------------------------------------------------
# RUTA PRINCIPAL
# ------------------------------------------------------------
@app.get("/")
async def dashboard(request: Request):
    # Si no hay login → enviar a login
    if not request.session.get("uid"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse("dashboard.html", tpl(request))

# ------------------------------------------------------------
# HEALTHCHECK (Render lo usa si quieres)
# ------------------------------------------------------------
@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "timestamp": datetime.utcnow().isoformat()})

# ------------------------------------------------------------
# ERRORES
#   Nota: si no hay sesión, en vez de "maquillar" el error como dashboard,
#   redirigimos a /login para no confundir (menú sólo tras login).
# ------------------------------------------------------------
@app.exception_handler(404)
async def not_found(request: Request, exc):
    if not request.session.get("uid"):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        "dashboard.html",
        tpl(request, message="Página no encontrada"),
        status_code=404
    )

@app.exception_handler(500)
async def internal_error(request: Request, exc):
    if not request.session.get("uid"):
        # Evita mostrar dashboard si no hay login
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        "dashboard.html",
        tpl(request, message="Error interno. Intenta más tarde."),
        status_code=500
    )

# app.py (al final, después de app.include_router(...))
print("=== ROUTES ===")
for r in app.routes:
    try:
        print(r.methods, r.path)
    except Exception:
        print(r)
print("==============")


