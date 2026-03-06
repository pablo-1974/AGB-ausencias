# auth.py
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from starlette.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from passlib.hash import bcrypt

from database import get_session
from config import settings
from models import User, Role

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
    return (await session.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()

async def _count_users(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(User))).scalar_one()

async def _count_admins(session: AsyncSession) -> int:
    return (await session.execute(
        select(func.count()).select_from(User).where(User.role == Role.admin)
    )).scalar_one()

def _hash_password(raw: str) -> str:
    return bcrypt.hash(raw)

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

@router.post("/login")
async def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    u = await _get_user_by_email(session, email)
    if not u or not _verify_password(password, u.password_hash):
        # Renderizar el login con error también es posible
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    request.session["uid"] = u.id
    return RedirectResponse(url="/", status_code=303)

@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

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
    total = await _count_users(session)
    if total > 0:
        raise HTTPException(400, "Ya existe al menos un usuario.")
    if settings.ADMIN_EMAIL_DOMAIN and not email.lower().endswith("@" + settings.ADMIN_EMAIL_DOMAIN):
        raise HTTPException(400, "Email no autorizado para admin inicial.")
    u = User(name=name.strip(), email=email.lower().strip(), password_hash=_hash_password(password), role=Role.admin)
    session.add(u)
    await session.commit()
    request.session["uid"] = u.id
    return RedirectResponse("/", status_code=303)

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
    total = await _count_users(session)
    role = Role.admin if total == 0 else Role.user

    exists = await _get_user_by_email(session, email)
    if exists:
        raise HTTPException(400, "Ya existe un usuario con ese email")
    u = User(name=name.strip(), email=email.lower().strip(), password_hash=_hash_password(password), role=role)
    session.add(u)
    await session.commit()
    request.session["uid"] = u.id
    return RedirectResponse("/", status_code=303)

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
    user.password_hash = _hash_password(new_password)
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
    if await _get_user_by_email(session, email):
        raise HTTPException(400, "Email ya registrado")
    r = Role.admin if role == "admin" else Role.user
    u = User(name=name.strip(), email=email.lower().strip(), password_hash=_hash_password(password), role=r)
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
            raise HTTPException(400, "Debe quedar al menos un administrador activo")
    await session.delete(u)
    await session.commit()
    return RedirectResponse("/admin/users", status_code=303)
