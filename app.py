# app.py
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

########## PROVISIONAL ##############################
import os
print("DEBUG FILES:", os.listdir("."))
####################################################

from config import settings
from auth import router as auth_router


# ------------------------------------------------------------
# Middleware de proxy (fallback tolerante)
# ------------------------------------------------------------
ProxyHeadersMiddleware = None
try:
    from starlette.middleware.proxy_headers import ProxyHeadersMiddleware  
except Exception:
    try:
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware  
    except Exception:
        ProxyHeadersMiddleware = None

# ------------------------------------------------------------
# APP FASTAPI
# ------------------------------------------------------------
app = FastAPI(title=settings.APP_NAME)

print("APP STARTED")


# ------------------------------------------------------------
# PROXY HEADERS (Render)
# ------------------------------------------------------------
if ProxyHeadersMiddleware:
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")


# ------------------------------------------------------------
# SESIÓN — DEBE IR AQUÍ, ANTES DE CUALQUIER OTRO MIDDLEWARE
# ------------------------------------------------------------
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="ausencias_session",
    max_age=60 * 60 * 8,
    same_site="lax",      # ← esencial para que Chrome envie la cookie tras login POST + redirect
    https_only=False,        # ← mantiene seguridad
)


# ------------------------------------------------------------
# NUEVO MIDDLEWARE CORRECTO (FUNCIONAL, NO DE CLASE)
# ------------------------------------------------------------
from database import AsyncSessionLocal
from models import User

@app.middleware("http")
async def load_user(request: Request, call_next):
    print("LOAD_USER middleware running")
    print("REQUEST HAS COOKIE?:", request.cookies)   # 🟢 LÍNEA NUEVA

    request.state.user = None

    if "session" in request.scope:
        uid = request.session.get("uid")
        print("UID SEEN:", uid)   # ya lo tienes
        if uid:
            async with AsyncSessionLocal() as db:
                user = await db.get(User, uid)
                print("DB USER:", user)
                request.state.user = user

    return await call_next(request)


# ------------------------------------------------------------
# STATIC y TEMPLATES
# ------------------------------------------------------------
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")
templates.env.cache = {}
app.state.templates = templates


# ------------------------------------------------------------
# MIDDLEWARE: No cache
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
# INCLUIR RUTAS (IMPORTAR Y AÑADIR)
# ------------------------------------------------------------
# router de importación de profesores
from imports_teachers import router as teachers_import_router
# router de importación de clases y guardias
from imports_schedule import router as schedule_import_router
# router de horarios
from schedule_router import router as schedule_router
# router de listados de profesorado (pantalla + PDFs)
from teachers_router import router as teachers_router  
# router de bajas
from leaves_router import router as leaves_router
# router de ausencias
from absences_router import router as absences_router
# router de informes
from reports_router import router as reports_router
# router de calendario
from config_calendar_router import router as calendar_router

app.include_router(auth_router)
app.include_router(teachers_import_router)
app.include_router(schedule_import_router)
app.include_router(schedule_router)
app.include_router(teachers_router)
app.include_router(leaves_router)
app.include_router(absences_router)
app.include_router(reports_router)
app.include_router(calendar_router)

# ------------------------------------------------------------
# DEBUG import
# ------------------------------------------------------------
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
    now = datetime.now()
    ctx = {
        "request": request,
        "title": APP_NAME,
        "app_name": APP_NAME,
        "institution_name": INSTITUTION_NAME,
        "logo_path": LOGO_PATH,
        "now_dt": now,
        "now": now,
    }
    ctx.update(extra or {})
    return ctx

# ------------------------------------------------------------
# RUTA PRINCIPAL
# ------------------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"])
async def dashboard(request: Request):
    # 🔥 IGNORAR HEAD REQUESTS
    if request.method == "HEAD":
        # No usar sesión, no renderizar plantillas
        return JSONResponse({"ok": True})

    # 🔥 GET normal: aquí sí usamos sesión
    if not request.session or not request.session.get("uid"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse("dashboard.html", tpl(request))

# ------------------------------------------------------------
# HEALTHCHECK
# ------------------------------------------------------------
@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "timestamp": datetime.utcnow().isoformat()})

# ------------------------------------------------------------
# ERROR HANDLERS
# ------------------------------------------------------------
@app.exception_handler(404)
async def not_found(request: Request, exc):
    # IGNORAR HEAD (Chrome lo hace siempre)
    if request.method == "HEAD":
        return JSONResponse({"ok": True})

    session = request.scope.get("session")
    uid = session.get("uid") if session else None

    if not uid:
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(...)

@app.exception_handler(500)
async def internal_error(request: Request, exc):
    # 🔥 1) IGNORAR peticiones HEAD
    # Chrome, Avast y algunas extensiones hacen HEAD a muchas rutas.
    # HEAD no envía cookies → no hay sesión → no debemos renderizar plantillas.
    if request.method == "HEAD":
        return JSONResponse({"ok": True})

    # 🔥 2) Obtener UID desde sesión (solo GET)
    session = request.scope.get("session")
    uid = session.get("uid") if session else None

    # 🔥 3) Si no hay uid → volver a login
    if not uid:
        return RedirectResponse("/login", status_code=303)

    # 🔥 4) Renderizar plantilla normal de error
    return templates.TemplateResponse(
        "dashboard.html",
        tpl(request, message="Error interno. Intenta más tarde."),
        status_code=500
    )

# ------------------------------------------------------------
# PRINT ROUTES
# ------------------------------------------------------------
print("=== ROUTES ===")
for r in app.routes:
    try:
        print(r.methods, r.path)
    except Exception:
        print(r)
print("==============")
