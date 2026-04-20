# auth.py
from __future__ import annotations
from typing import Optional

import logging

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from starlette.responses import RedirectResponse

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from passlib.hash import bcrypt

from datetime import date, datetime

from database import get_session
from config import settings
from models import User, Role

from context import ctx as _ctx

from services.actions_log import log_action
from utils import ActionType

COOKIE_NAME = "ausencias_session"

logger = logging.getLogger("uvicorn.error")
router = APIRouter()


# ---------------------------
# Helpers
# ---------------------------
async def _get_user_by_email(session: AsyncSession, email: str) -> Optional[User]:
    return (
        await session.execute(
            select(User).where(User.email == email.lower().strip())
        )
    ).scalar_one_or_none()


async def _count_users(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(User))).scalar_one()


async def _count_admins(session: AsyncSession) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == Role.admin)
        )
    ).scalar_one()


def _hash_password(raw: str) -> str:
    if raw is None:
        raise ValueError("Contraseña vacía")
    try:
        if len(raw.encode("utf-8")) > 72:
            raise ValueError("La contraseña no puede superar 72 bytes (bcrypt)")
        return bcrypt.hash(raw)
    except Exception as e:
        raise ValueError(f"No se pudo generar el hash: {e}")


def _verify_password(raw: str, pw_hash: str) -> bool:
    try:
        return bcrypt.verify(raw, pw_hash)
    except Exception:
        return False


def _templates(request: Request):
    tpl = getattr(request.app.state, "templates", None)
    if tpl is None:
        from fastapi.templating import Jinja2Templates

        tpl = Jinja2Templates(directory="templates")
        request.app.state.templates = tpl
    return tpl


# ---------------------------
# Auth dependencies
# ---------------------------
async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
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

@router.get("/login")
async def login_page(request: Request):
    return _templates(request).TemplateResponse(
        "login.html",
        _ctx(
            request,
            user=None,
            hide_chrome=True,
            show_nav=False,
            title=settings.APP_NAME,
        ),
    )


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
            logger.info("Login fallido para %s", email_norm)
            return _templates(request).TemplateResponse(
                "login.html",
                _ctx(
                    request,
                    user=None,
                    hide_chrome=True,
                    show_nav=False,
                    title=settings.APP_NAME,
                    error="Correo o contraseña no válidos",
                    email=email_norm,
                ),
                status_code=400,
            )

        request.session["uid"] = u.id
        print("SESSION AFTER LOGIN:", request.session)
        
        logger.info("Login OK para %s (uid=%s)", email_norm, u.id)
        
        # ✅ REGISTRO DE ACCIÓN: LOGIN CORRECTO
        await log_action(
            session,
            user=u,
            action=ActionType.LOGIN,
            entity="user",
            entity_id=u.id,
            detail="Inicio de sesión correcto",
        )

        await session.commit()
        
        return RedirectResponse("/", status_code=303)

    except Exception:
        logger.exception("Error inesperado en login para %s", email_norm)
        return _templates(request).TemplateResponse(
            "login.html",
            _ctx(
                request,
                user=None,
                hide_chrome=True,
                show_nav=False,
                title=settings.APP_NAME,
                error="Correo o contraseña no válidos",
                email=email_norm,
            ),
            status_code=500,
        )


@router.get("/logout")
async def logout(request: Request):
    if request.session:
        request.session.clear()

    response = RedirectResponse("/login", status_code=303)

    is_secure = request.url.scheme == "https"
    response.delete_cookie(
        key=COOKIE_NAME, path="/", samesite="lax", secure=is_secure, domain=None
    )
    return response


# ---------------------------
# Registro (primer admin)
# ---------------------------

@router.get("/register-first-admin")
async def register_first_admin_page(request: Request, session: AsyncSession = Depends(get_session)):
    total = await _count_users(session)
    if total > 0:
        return RedirectResponse("/register", status_code=303)

    return _templates(request).TemplateResponse(
        "register_first.html",
        _ctx(
            request,
            user=None,
            hide_chrome=True,
            show_nav=False,
            title="Crear administrador",
        ),
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

        if settings.ADMIN_EMAIL_DOMAIN and not email_norm.endswith(
            "@" + settings.ADMIN_EMAIL_DOMAIN
        ):
            raise HTTPException(400, "Email no autorizado para admin inicial.")

        try:
            pw_hash = _hash_password(password)
        except ValueError as ve:
            raise HTTPException(400, str(ve))

        u = User(
            name=name.strip(),
            email=email_norm,
            password_hash=pw_hash,
            role=Role.admin,
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
        raise HTTPException(400, "Email ya existente")

    except SQLAlchemyError:
        await session.rollback()
        raise HTTPException(500, "Error de base de datos")

    except Exception as e:
        await session.rollback()
        logger.exception("Error creando admin")
        raise


# ---------------------------
# Registro normal
# ---------------------------

@router.get("/register")
async def register_page(request: Request):
    return _templates(request).TemplateResponse(
        "register.html",
        _ctx(
            request,
            user=None,
            hide_chrome=True,
            show_nav=False,
            title="Registro",
        ),
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

    except Exception:
        await session.rollback()
        logger.exception("Error creando usuario")
        raise


# ---------------------------
# Cambio de contraseña
# ---------------------------

@router.get("/me/password")
async def me_password_page(
    request: Request,
    user: User = Depends(current_user),
):
    return _templates(request).TemplateResponse(
        "password_change.html",
        _ctx(request, user=user, title="Cambiar contraseña")
    )


@router.post("/me/password")
async def me_password_post(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    # Validar contraseña actual
    if not _verify_password(current_password, user.password_hash):
        raise HTTPException(400, "La contraseña actual no es válida")

    # Guardar nueva contraseña
    user.password_hash = _hash_password(new_password)
    await session.commit()

    # Cerrar sesión
    if request.session:
        request.session.clear()

    # Redirigir a login (sin renderizar plantilla)
    response = RedirectResponse("/login", status_code=303)
    is_secure = request.url.scheme == "https"
    response.delete_cookie(
        key=COOKIE_NAME, path="/", samesite="lax", secure=is_secure
    )
    return response


# ---------------------------
# Admin: gestión de usuarios
# ---------------------------

@router.get("/admin/users")
async def admin_users_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    users = (
        await session.execute(
            select(User).order_by(User.role.desc(), User.name.asc())
        )
    ).scalars().all()

    return _templates(request).TemplateResponse(
        "users_list.html",
        _ctx(request, admin, title="Gestión de usuarios", users=users),
    )


@router.get("/admin/users/new")
async def admin_users_new_page(
    request: Request,
    admin: User = Depends(admin_required),
):
    return _templates(request).TemplateResponse(
        "users_new.html",
        _ctx(request, admin, title="Nuevo usuario"),
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

    pw_hash = _hash_password(password)
    r = Role.admin if role == "admin" else Role.user

    u = User(
        name=name.strip(),
        email=email_norm,
        password_hash=pw_hash,
        role=r,
        active=True,
    )
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

    u.password_hash = _hash_password(new_password)
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
            raise HTTPException(400, "Debe quedar al menos un administrador")

    await session.delete(u)
    await session.commit()

    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/toggle-active")
async def admin_toggle_active(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")

    u.active = not u.active
    await session.commit()

    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/toggle-role")
async def admin_toggle_role(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")

    if u.role == Role.admin:
        admins = await session.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == Role.admin)
        )
        if admins.scalar_one() <= 1:
            raise HTTPException(400, "Debe quedar al menos un administrador")

    u.role = Role.user if u.role == Role.admin else Role.admin
    await session.commit()

    return RedirectResponse("/admin/users", status_code=303)
