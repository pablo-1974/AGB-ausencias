# app.py
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.proxy_headers import ProxyHeadersMiddleware
from starlette.responses import RedirectResponse, JSONResponse

from config import settings
from auth import router as auth_router, setup_session


# ------------------------------------------------------------
# APP FASTAPI (configurada ya para Render + Neon)
# ------------------------------------------------------------
app = FastAPI(title=settings.APP_NAME)

# Asegurar https detrás de Render (X-Forwarded-Proto)
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
# INCLUIR RUTAS (solo auth de momento)
# El resto las iremos activando cuando generemos sus routers
# ------------------------------------------------------------
app.include_router(auth_router)


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
        "now": datetime.now(),
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
# ------------------------------------------------------------
@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse(
        "dashboard.html",
        tpl(request, message="Página no encontrada"),
        status_code=404
    )

@app.exception_handler(500)
async def internal_error(request: Request, exc):
    return templates.TemplateResponse(
        "dashboard.html",
        tpl(request, message="Error interno. Intenta más tarde."),
        status_code=500
    )
