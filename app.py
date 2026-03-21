# app.py
from datetime import datetime
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

import os
print("DEBUG FILES:", os.listdir("."))

from config import settings
print("SECRET_KEY IN RUNTIME:", settings.SECRET_KEY)

from auth import router as auth_router

# ------------------------------------------------------------
# Proxy Headers
# ------------------------------------------------------------
ProxyHeadersMiddleware = None
try:
    from starlette.middleware.proxy_headers import ProxyHeadersMiddleware
except:
    try:
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    except:
        ProxyHeadersMiddleware = None

# ------------------------------------------------------------
# APP
# ------------------------------------------------------------
app = FastAPI(title=settings.APP_NAME)
print("APP STARTED")

# ------------------------------------------------------------
# PROXY HEADERS FIRST
# ------------------------------------------------------------
if ProxyHeadersMiddleware:
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# ------------------------------------------------------------
# SESSION MIDDLEWARE (antes que nada más)
# ------------------------------------------------------------
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="ausencias_session",
    max_age=60 * 60 * 8,
    same_site="none",
    https_only=True,
)

# ------------------------------------------------------------
# STATIC FILES
# ------------------------------------------------------------
app.mount("/static", StaticFiles(directory="static"), name="static")

# ------------------------------------------------------------
# TEMPLATES
# ------------------------------------------------------------
templates = Jinja2Templates(directory="templates")
templates.env.cache = {}
app.state.templates = templates

# ------------------------------------------------------------
# LOAD USER DEPENDENCY (en vez de middleware)
# ------------------------------------------------------------
from database import AsyncSessionLocal
from models import User

async def load_user_dep(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    async with AsyncSessionLocal() as db:
        return await db.get(User, uid)

# ------------------------------------------------------------
# NO-CACHE MIDDLEWARE
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
# ROUTERS
# ------------------------------------------------------------
from imports_teachers import router as teachers_import_router
from imports_schedule import router as schedule_import_router
from schedule_router import router as schedule_router
from teachers_router import router as teachers_router
from leaves_router import router as leaves_router
from absences_router import router as absences_router
from reports_router import router as reports_router
from config_calendar_router import router as calendar_router
from admin_router import router as admin_router
from stats_router import router as stats_router

app.include_router(auth_router)
app.include_router(teachers_import_router)
app.include_router(schedule_import_router)
app.include_router(schedule_router)
app.include_router(teachers_router)
app.include_router(leaves_router)
app.include_router(absences_router)
app.include_router(reports_router)
app.include_router(calendar_router)
app.include_router(admin_router)
app.include_router(stats_router)

# ------------------------------------------------------------
# TEMPLATE CONTEXT
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
# ROOT ROUTE (usa la dependencia de usuario)
# ------------------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"])
async def dashboard(request: Request, user: User = Depends(load_user_dep)):

    if request.method == "HEAD":
        return JSONResponse({"ok": True})

    if not user:
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        "dashboard.html",
        tpl(request, user=user)
    )

# ------------------------------------------------------------
# HEALTH
# ------------------------------------------------------------
@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "timestamp": datetime.utcnow().isoformat()})

# ------------------------------------------------------------
# ERROR HANDLERS
# ------------------------------------------------------------
@app.exception_handler(404)
async def not_found(request: Request, exc, user: User = Depends(load_user_dep)):

    if not user or not isinstance(user, User):
        # NO renderizar base.html sin user válido
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        "dashboard.html",
        tpl(request, message="Página no encontrada", user=user),
        status_code=404
    )


@app.exception_handler(500)
async def internal_error(request: Request, exc, user: User = Depends(load_user_dep)):

    if not user or not isinstance(user, User):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        "dashboard.html",
        tpl(request, message="Error interno", user=user),
        status_code=500
    )
