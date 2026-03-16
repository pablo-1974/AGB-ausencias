# app.py
from datetime import datetime
from fastapi import FastAPI, Request
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
# Proxy Headers (Render)
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
# APP FASTAPI
# ------------------------------------------------------------
app = FastAPI(title=settings.APP_NAME)
print("APP STARTED")

# ------------------------------------------------------------
# PROXY HEADERS
# ------------------------------------------------------------
if ProxyHeadersMiddleware:
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# ------------------------------------------------------------
# SESSION MIDDLEWARE
# ------------------------------------------------------------
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="ausencias_session",
    max_age=60 * 60 * 8,
    same_site="none",
    https_only=True
)

# ------------------------------------------------------------
# STATIC & TEMPLATES
# ------------------------------------------------------------
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.cache = {}
app.state.templates = templates

# ------------------------------------------------------------
# NO-CACHE MIDDLEWARE
# ------------------------------------------------------------
@app.middleware("http")
async def no_cache_mw(request: Request, call_next):
    response = await call_next(request)
    nocache = {"/", "/login", "/register-first-admin", "/register"}
    if request.url.path in nocache or request.url.path.startswith("/admin"):
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
# LOAD USER MIDDLEWARE → ***AQUÍ VA, DESPUÉS DE TODAS LAS RUTAS***
# ------------------------------------------------------------
from starlette.middleware.base import BaseHTTPMiddleware
from database import AsyncSessionLocal
from models import User

class LoadUserMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        print("LOAD_USER middleware running")
        print("REQUEST HAS COOKIE?:", request.cookies)

        request.state.user = None

        # ⭐⭐ ESTA ES LA CLAVE:
        uid = request.session.get("uid")
        print("UID SEEN:", uid)

        if uid:
            async with AsyncSessionLocal() as db:
                try:
                    user = await db.get(User, uid)
                    print("DB RESULT:", user)
                    request.state.user = user
                except Exception as e:
                    print("DB ERROR:", e)

        return await call_next(request)

# ⭐⭐⭐ IMPORTANTÍSIMO: SE AÑADE AL FINAL ⭐⭐⭐
app.add_middleware(LoadUserMiddleware)

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
# ROOT ROUTE
# ------------------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"])
async def dashboard(request: Request):
    if request.method == "HEAD":
        return JSONResponse({"ok": True})

    if not request.session or not request.session.get("uid"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse("dashboard.html", tpl(request))

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
async def not_found(request: Request, exc):
    if request.method == "HEAD":
        return JSONResponse({"ok": True})

    uid = request.session.get("uid")
    if not uid:
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        "dashboard.html", tpl(request, message="Página no encontrada"), status_code=404
    )

@app.exception_handler(500)
async def internal_error(request: Request, exc):
    if request.method == "HEAD":
        return JSONResponse({"ok": True})

    uid = request.session.get("uid")
    if not uid:
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        "dashboard.html", tpl(request, message="Error interno"), status_code=500
    )
