# auth.py
from __future__ import annotations
from typing import Optional

import logging

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from starlette.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from passlib.hash import bcrypt

from database import get_session
from config import settings
from models import User, Role

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

# ---------------------------
# Sesión (cookie)
# ---------------------------
def setup_session(app):
    # Ajusta flags según tu política
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="ausencias_session",
        max_age=60 * 60 * 8,    # 8 horas
        same_site="lax",
        https_only=True,
    )

# ---------------------------
# Helpers
# ---------------------------
async def _get_user_by_email(session: AsyncSession, email: str) -> Optional[User]:
    return (await session.execute(select(User).where(User.email == email.lower().strip()))).scalar_one_or_none()

async def _count_users(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(User))).scalar_one()

async def _count_admins(session: AsyncSession) -> int:
    return (await session.execute(
        select(func.count()).select_from(User).where(User.role == Role.admin)
    )).scalar_one()

def _hash_password(raw: str) -> str:
    """
    Bcrypt sólo usa los **primeros 72 bytes**. Para evitar errores de backend
    o contraseñas multibyte largas, validamos y lanzamos un 400 controlado.
    """
    if raw is None:
        raise ValueError("Contraseña vacía")
    try:
        # Validar tamaño efectivo en bytes (UTF-8)
        if len(raw.encode("utf-8")) > 72:
            raise ValueError("La contraseña no puede superar 72 bytes (bcrypt)")
        return bcrypt.hash(raw)
    except Exception as e:
        # Evita que un fallo de backend deje el alta en silencio
        raise ValueError(f"No se pudo generar el hash de la contraseña: {e}")

def _verify_password(raw: str, pw_hash: str) -> bool:
    try:
        return bcrypt.verify(raw, pw_hash)
    except Exception:
        return False

def _templates(request: Request):
    # Usa los templates montados en app.state; fallback en caso de que aún no esté
    tpl = getattr(request.app.state, "templates", None)
    if tpl is None:
        from fastapi.templating import Jinja2Templates
        tpl = Jinja2Templates(directory="templates")
        request.app.state.templates = tpl
    return tpl

# ---------------------------
# Auth dependencies
# ---------------------------
async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    uid = request.session.get("uid") if request.session else None
    if not uid:
        raise HTTPException(status_code=401)
    user = await session.get(User, uid)
    if not user:
        raise HTTPException(status_code=401)
    if not user.active:
        request.session.clear()
        raise HTTPException(status_code=401)
    return user

async def admin_required(user: User = Depends(current_user)) -> User:
    if user.role != Role.admin:
        raise HTTPException(status_code=403, detail="Se requieren permisos de administrador")
    return user

# ---------------------------
# Login / Logout
# ---------------------------

# PROVISIONAL #
@router.get("/__whoami")
async def __whoami(request: Request):
    return {
        "has_session": bool(request.session),
        "uid": request.session.get("uid") if request.session else None,
        "path": str(request.url.path),
    }
###############

@router.get("/login")
async def login_page(request: Request):
    return _templates(request).TemplateResponse(
        "login.html",
        {
            "request": request,
            "title": settings.APP_NAME,
            "app_name": settings.APP_NAME,
            "institution_name": settings.INSTITUTION_NAME,
            "logo_path": settings.LOGO_PATH,
        },
    )

# arriba del archivo ya añadimos: import logging; logger = logging.getLogger("uvicorn.error")

@router.post("/login")
async def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    email_norm = (email or "").strip().lower()
    try:
        u = await _get_user_by_email(session, email_norm)
        if not u or not _verify_password(password or "", u.password_hash):
            # En lugar de 401, re-renderizamos login con mensaje de error
            logger.info("Login fallido para %s", email_norm)
            return _templates(request).TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "title": settings.APP_NAME,
                    "app_name": settings.APP_NAME,
                    "institution_name": settings.INSTITUTION_NAME,
                    "logo_path": settings.LOGO_PATH,
                    "error": "Correo o contraseña no válidos",
                    "email": email_norm,  # para repoblar el campo
                },
                status_code=400,
            )

        # OK: establecemos la sesión y redirigimos al dashboard
        request.session["uid"] = u.id
        logger.info("Login OK para %s (uid=%s)", email_norm, u.id)
        return RedirectResponse(url="/", status_code=303)

    except Exception:
        # Si algo raro pasa (DB, etc.), lo veremos en Events
        logger.exception("Error inesperado en /login para %s", email_norm)
        return _templates(request).TemplateResponse(
            "login.html",
            {
                "request": request,
                "title": settings.APP_NAME,
                "app_name": settings.APP_NAME,
                "institution_name": settings.INSTITUTION_NAME,
                "logo_path": settings.LOGO_PATH,
                "error": "Error interno al iniciar sesión. Inténtalo de nuevo.",
                "email": email_norm,
            },
            status_code=500,
        )

# ---------------------------
# Registro (primer admin)
# ---------------------------
@router.get("/register-first-admin")
async def register_first_admin_page(request: Request, session: AsyncSession = Depends(get_session)):
    total = await _count_users(session)
    if total > 0:
        return RedirectResponse(url="/register", status_code=303)
    return _templates(request).TemplateResponse(
        "register_first.html",
        {
            "request": request,
            "title": "Crear administrador",
            "logo_path": settings.LOGO_PATH,
        },
    )

@router.post("/register-first-admin")
async def register_first_admin_post(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    try:
        total = await _count_users(session)
        if total > 0:
            raise HTTPException(400, "Ya existe al menos un usuario.")

        email_norm = email.lower().strip()
        if settings.ADMIN_EMAIL_DOMAIN and not email_norm.endswith("@" + settings.ADMIN_EMAIL_DOMAIN):
            raise HTTPException(400, "Email no autorizado para admin inicial.")

        # Hash seguro (controla errores y tamaño)
        try:
            pw_hash = _hash_password(password)
        except ValueError as ve:
            logger.exception("Hash error creando primer admin (%s)", email_norm)
            raise HTTPException(400, str(ve))

        u = User(
            name=name.strip(),
            email=email_norm,
            password_hash=pw_hash,
            role=Role.admin,
            active=True,
        )

        session.add(u)
        # Garantiza PK antes de commit; si falla, saltará al except
        await session.flush()
        await session.commit()

        # Si por expiración/driver no quedó el id en u, reférescalo
        if not getattr(u, "id", None):
            await session.refresh(u)

        request.session["uid"] = u.id
        return RedirectResponse("/", status_code=303)

    except IntegrityError:
        await session.rollback()
        logger.exception("IntegrityError creando primer admin (%s)", email)
        raise HTTPException(400, "El email ya existe o no es válido")

    except SQLAlchemyError:
        await session.rollback()
        logger.exception("SQLAlchemyError creando primer admin")
        raise HTTPException(500, "Error de base de datos")

    except HTTPException:
        # Ya gestionado arriba; no vuelques doble log
        await session.rollback()
        raise

    except Exception:
        await session.rollback()
        logger.exception("Error general creando primer admin")
        raise

# ---------------------------
# Registro normal
# ---------------------------
@router.get("/register")
async def register_page(request: Request):
    return _templates(request).TemplateResponse(
        "register.html",
        {"request": request, "logo_path": settings.LOGO_PATH},
    )

@router.post("/register")
async def register_post(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    try:
        total = await _count_users(session)
        role = Role.admin if total == 0 else Role.user

        email_norm = email.lower().strip()
        exists = await _get_user_by_email(session, email_norm)
        if exists:
            raise HTTPException(400, "Ya existe un usuario con ese email")

        try:
            pw_hash = _hash_password(password)
        except ValueError as ve:
            logger.exception("Hash error creando usuario (%s)", email_norm)
            raise HTTPException(400, str(ve))

        u = User(
            name=name.strip(),
            email=email_norm,
            password_hash=pw_hash,
            role=role,
            active=True,
        )
        session.add(u)
        await session.flush()
        await session.commit()

        if not getattr(u, "id", None):
            await session.refresh(u)

        request.session["uid"] = u.id
        return RedirectResponse("/", status_code=303)

    except IntegrityError:
        await session.rollback()
        logger.exception("IntegrityError creando usuario (%s)", email)
        raise HTTPException(400, "El email ya existe o no es válido")

    except SQLAlchemyError:
        await session.rollback()
        logger.exception("SQLAlchemyError creando usuario")
        raise HTTPException(500, "Error de base de datos")

    except HTTPException:
        await session.rollback()
        raise

    except Exception:
        await session.rollback()
        logger.exception("Error general creando usuario")
        raise

# ---------------------------
# Cambio de contraseña (propio)
# ---------------------------
@router.get("/me/password")
async def me_password_page(request: Request, user: User = Depends(current_user)):
    return _templates(request).TemplateResponse(
        "password_change.html",
        {"request": request, "user": user, "logo_path": settings.LOGO_PATH},
    )

@router.post("/me/password")
async def me_password_post(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    if not _verify_password(current_password, user.password_hash):
        raise HTTPException(400, "La contraseña actual no es válida")
    try:
        user.password_hash = _hash_password(new_password)
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    await session.commit()
    return RedirectResponse("/", status_code=303)

# ---------------------------
# Admin: gestión de usuarios
# ---------------------------
@router.get("/admin/users")
async def admin_users_list(request: Request, session: AsyncSession = Depends(get_session), admin: User = Depends(admin_required)):
    res = await session.execute(select(User).order_by(User.role.desc(), User.name.asc()))
    users = res.scalars().all()
    return _templates(request).TemplateResponse(
        "users_list.html",
        {"request": request, "users": users, "logo_path": settings.LOGO_PATH},
    )

@router.get("/admin/users/new")
async def admin_users_new_page(request: Request, admin: User = Depends(admin_required)):
    return _templates(request).TemplateResponse(
        "users_new.html",
        {"request": request, "logo_path": settings.LOGO_PATH},
    )

@router.post("/admin/users/new")
async def admin_users_new(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    email_norm = email.lower().strip()
    if await _get_user_by_email(session, email_norm):
        raise HTTPException(400, "Email ya registrado")
    try:
        pw_hash = _hash_password(password)
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    r = Role.admin if role == "admin" else Role.user
    u = User(name=name.strip(), email=email_norm, password_hash=pw_hash, role=r, active=True)
    session.add(u)
    await session.commit()
    return RedirectResponse("/admin/users", status_code=303)

@router.post("/admin/users/{user_id}/reset-password")
async def admin_users_reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    try:
        u.password_hash = _hash_password(new_password)
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    await session.commit()
    return RedirectResponse("/admin/users", status_code=303)

@router.post("/admin/users/{user_id}/delete")
async def admin_users_delete(
    request: Request,
    user_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    if u.id == admin.id:
        raise HTTPException(400, "No puedes borrarte a ti mismo")
    if u.role == Role.admin:
        admins = await _count_admins(session)
        if admins <= 1:
            raise HTTPException(400, "Debe quedar al menos un administrador activo")
    await session.delete(u)
    await session.commit()
    return RedirectResponse("/admin/users", status_code=303)

# --- DEBUG: eliminar cuando acabemos ---
@router.get("/__debug-first-admin")
async def __debug_first_admin(session: AsyncSession = Depends(get_session)):
    try:
        total = await _count_users(session)
        admins = await _count_admins(session)
        return {
            "total_users": int(total or 0),
            "total_admins": int(admins or 0),
            "db_url_prefix": (settings.DATABASE_URL[:40] + "...") if hasattr(settings, "DATABASE_URL") else None,
        }
    except Exception as e:
        return {"error": str(e)}


