from fastapi import FastAPI, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from sqlalchemy.orm import Session
import datetime as dt

# --- Importación de módulos locales ---
from config import settings
from database import Base, engine, get_db
from auth import router as auth_router, get_current_user, require_admin

# Servicios
from services.imports import import_teachers_file, import_schedule_file
from services.schedule import get_schedule_for_teacher
from services.absences import (
    create_absence,
    get_absences_for_date,
    delete_absence,
    categorize_absence,
    get_uncategorized_absences,
)
from services.leaves import (
    create_leave, create_substitution,
    close_leave, get_open_leaves,
)
from services.pdf_monthly import generate_monthly_pdf
from services.pdf_daily import generate_daily_pdf


# =====================================================
# INICIALIZAR APP
# =====================================================
app = FastAPI()

# Archivos estáticos
app.mount("/static", StaticFiles(directory="static"), name="static")

# Motor de plantillas
templates = Jinja2Templates(directory="templates")

# Middleware de sesión
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    same_site="lax",
    https_only=False  # pon True en producción si usas HTTPS
)

# Crear tablas si no existen
Base.metadata.create_all(bind=engine)

# Router de autenticación
app.include_router(auth_router)


# =====================================================
# DASHBOARD
# =====================================================
@app.get("/")
def dashboard(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "user": user}
    )


# =====================================================
# IMPORTAR PROFESORES
# =====================================================
@app.get("/admin/import/teachers")
def import_teachers_page(request: Request, user=Depends(require_admin)):
    msg = request.session.pop("flash", None)  # recuperar mensaje si existe
    return templates.TemplateResponse(
        "teachers_import.html",
        {"request": request, "user": user, "message": msg},
    )

@app.post("/admin/import/teachers")
async def import_teachers(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(require_admin)
):
    result = await import_teachers_file(file, db)
    request.session["flash"] = (
        f"Profesores importados: {result['inserted']} nuevos, "
        f"{result['skipped']} omitidos por email duplicado."
    )
    return RedirectResponse("/admin/import/teachers", status_code=303)


# =====================================================
# IMPORTAR HORARIO (GUARDIAS + CLASES, SIN REUNIONES)
# =====================================================
@app.get("/admin/import/schedule")
def import_schedule_page(request: Request, user=Depends(require_admin)):
    return templates.TemplateResponse(
        "schedule_import.html",
        {"request": request, "user": user},
    )

@app.post("/admin/import/schedule")
async def import_schedule(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(require_admin)
):
    await import_schedule_file(file, db)
    return RedirectResponse("/admin/import/schedule", status_code=303)


# =====================================================
# VER HORARIO DE UN PROFESOR
# =====================================================
from typing import Optional
from fastapi import Query

@app.get("/schedule")
def view_schedule(
    request: Request,
    teacher_id: Optional[str] = Query(None),   # 1) lo recibimos como str o None
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
):
    # 2) Normalizamos: si está vacío o no es dígito, lo tratamos como None
    tid = int(teacher_id) if teacher_id and teacher_id.isdigit() else None

    # 3) Solo buscamos horario si hay un id válido
    schedule = get_schedule_for_teacher(tid, db) if tid is not None else None

    return templates.TemplateResponse(
        "schedule_view.html",
        {"request": request, "user": user, "schedule": schedule},
    )


# =====================================================
# AUSENCIAS
# =====================================================
@app.get("/absences/new")
def absences_new(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse("absences_new.html", {"request": request, "user": user})

@app.post("/absences/new")
def absences_create(
    request: Request,
    teacher_id: int = Form(...),
    date: str = Form(...),
    hours: str = Form(...),
    explanation: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    create_absence(teacher_id, date, hours, explanation, user.id, db)
    return RedirectResponse("/absences/new", status_code=303)


@app.get("/absences/manage")
def absences_manage(
    request: Request,
    date: str = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    if not date:
        date = dt.date.today().isoformat()
    absences = get_absences_for_date(date, db)
    return templates.TemplateResponse(
        "absences_manage.html",
        {"request": request, "absences": absences, "date": date},
    )


@app.post("/absences/{id}/update")
def absences_update(
    id: int,
    category: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    categorize_absence(id, category, db)
    return RedirectResponse("/absences/manage", status_code=303)


@app.post("/absences/{id}/delete")
def absences_delete(
    id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    delete_absence(id, db)
    return RedirectResponse("/absences/manage", status_code=303)


# =====================================================
# BAJAS Y SUSTITUCIONES
# =====================================================
@app.get("/leaves/new")
def leaves_new(request: Request, db: Session = Depends(get_db), user=Depends(require_admin)):
    open_leaves = get_open_leaves(db)
    return templates.TemplateResponse(
        "leaves_new.html",
        {"request": request, "user": user, "open_leaves": open_leaves},
    )


@app.post("/leaves/new")
def leaves_create(
    request: Request,
    teacher_id: int = Form(...),
    start_date: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(require_admin)
):
    create_leave(teacher_id, start_date, db)
    return RedirectResponse("/leaves/new", status_code=303)


@app.get("/substitutions/new")
def subs_new(request: Request, db: Session = Depends(get_db), user=Depends(require_admin)):
    open_leaves = get_open_leaves(db)
    return templates.TemplateResponse(
        "substitutions_new.html",
        {"request": request, "user": user, "open_leaves": open_leaves},
    )


@app.post("/substitutions/new")
def subs_create(
    request: Request,
    leave_id: int = Form(...),
    start_date: str = Form(...),
    substitute_name: str = Form(...),
    substitute_email: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(require_admin)
):
    create_substitution(leave_id, start_date, substitute_name, substitute_email, db)
    return RedirectResponse("/substitutions/new", status_code=303)


@app.get("/leaves/close")
def leaves_close_page(request: Request, db: Session = Depends(get_db), user=Depends(require_admin)):
    open_leaves = get_open_leaves(db)
    return templates.TemplateResponse(
        "leaves_close.html",
        {"request": request, "user": user, "open_leaves": open_leaves},
    )


@app.post("/leaves/close")
def leaves_close_action(
    request: Request,
    leave_id: int = Form(...),
    end_date: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(require_admin)
):
    close_leave(leave_id, end_date, db)
    return RedirectResponse("/leaves/close", status_code=303)


# =====================================================
# REPORTES MENSUAL Y DIARIO
# =====================================================
@app.get("/reports/monthly")
def reports_monthly_page(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    uncategorized = get_uncategorized_absences(db)
    return templates.TemplateResponse(
        "reports_monthly.html",
        {"request": request, "user": user, "uncategorized": uncategorized},
    )


@app.get("/reports/monthly/pdf")
def reports_monthly_pdf(
    start: str,
    end: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    pdf_bytes = generate_monthly_pdf(start, end, db)
    return StreamingResponse(pdf_bytes, media_type="application/pdf")


@app.get("/reports/daily")
def reports_daily_page(
    request: Request,
    date: str = None,
    user=Depends(get_current_user)
):
    if not date:
        date = dt.date.today().isoformat()
    return templates.TemplateResponse(
        "reports_daily.html",
        {"request": request, "user": user, "date": date},
    )


@app.get("/reports/daily/pdf")
def reports_daily_pdf(
    date: str,
    observations: str = "",
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    pdf_bytes = generate_daily_pdf(date, observations, db)

    return StreamingResponse(pdf_bytes, media_type="application/pdf")


