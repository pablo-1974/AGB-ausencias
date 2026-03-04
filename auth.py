from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from typing import Optional

from database import get_db
from models import User, Role
from utils import hash_password, verify_password

router = APIRouter()
templates = Jinja2Templates(directory="templates")

SESSION_KEY = "user_id"


# -------------------------
# Helpers
# -------------------------

def _get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none()

def _get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.get(User, user_id)

def _first_user_role(db: Session) -> Role:
    total = db.execute(select(func.count(User.id))).scalar() or 0
    return Role.admin if total == 0 else Role.user


# -------------------------
# Dependencies
# -------------------------

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    Devuelve el usuario autenticado leyendo la cookie de sesión.
    Si no hay sesión válida, lanza 401 (lo redirigiremos en app.py con un handler).
    """
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        raise HTTPException(status_code=401, detail="No autenticado")
    user = _get_user_by_id(db, int(user_id))
    if not user or not user.is_active:
        # Sesión inválida o usuario desactivado
        request.session.pop(SESSION_KEY, None)
        raise HTTPException(status_code=401, detail="No autenticado")
    return user

def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.admin:
        raise HTTPException(status_code=403, detail="Requiere rol administrador")
    return user


# -------------------------
# Auth: Login / Logout / Register
# -------------------------

@router.get("/login")
def login_page(request: Request):
    # Página con degradado morado y logo (plantilla `login.html`)
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _get_user_by_email(db, email)
    if not user or not verify_password(password, user.password_hash):
        # Volvemos a la página de login con un mensaje
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Credenciales no válidas",
                "email": email,
            },
            status_code=400,
        )
    # Guardar sesión
    request.session[SESSION_KEY] = user.id
    return RedirectResponse("/", status_code=303)

@router.get("/register")
def register_page(request: Request, db: Session = Depends(get_db)):
    """
    Página de registro:
    - Solo permite registro si la base está vacía (primer admin)
    - Si no está vacía, redirige a /login
    """
    total = db.execute(select(func.count(User.id))).scalar() or 0
    if total > 0:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "register_mode": True})

@router.post("/register")
def register_post(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Crea usuario:
    - Si es el primero → admin
    - Si no (por seguridad) denegamos registro público (solo admin puede crear más desde /admin/users)
    """
    total = db.execute(select(func.count(User.id))).scalar() or 0
    if total > 0:
        # No se permite registro público cuando ya hay usuarios
        return RedirectResponse("/login", status_code=303)

    if _get_user_by_email(db, email):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "register_mode": True, "error": "Ya existe un usuario con ese email."},
            status_code=400,
        )

    user = User(
        name=name.strip(),
        email=email.lower().strip(),
        password_hash=hash_password(password),
        role=_first_user_role(db),
        is_active=True,
    )
    db.add(user)
    db.commit()

    # Autologin tras crear el primer usuario
    request.session[SESSION_KEY] = user.id
    return RedirectResponse("/", status_code=303)

@router.get("/logout")
def logout(request: Request):
    request.session.pop(SESSION_KEY, None)
    return RedirectResponse("/login", status_code=303)


# -------------------------
# Cuenta: cambiar contraseña propia
# -------------------------

@router.get("/account/password")
def account_password_page(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("account_password.html", {"request": request, "user": user})

@router.post("/account/password")
def account_password_post(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(current_password, user.password_hash):
        return templates.TemplateResponse(
            "account_password.html",
            {"request": request, "user": user, "error": "La contraseña actual no es correcta."},
            status_code=400,
        )
    if len(new_password) < 8:
        return templates.TemplateResponse(
            "account_password.html",
            {"request": request, "user": user, "error": "La nueva contraseña debe tener al menos 8 caracteres."},
            status_code=400,
        )
    if new_password != new_password_confirm:
        return templates.TemplateResponse(
            "account_password.html",
            {"request": request, "user": user, "error": "Las contraseñas no coinciden."},
            status_code=400,
        )

    user.password_hash = hash_password(new_password)
    db.add(user)
    db.commit()
    return templates.TemplateResponse(
        "account_password.html",
        {"request": request, "user": user, "success": "Contraseña actualizada correctamente."},
    )


# -------------------------
# Admin: gestionar usuarios
# -------------------------

@router.get("/admin/users")
def admin_users_page(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    users = db.execute(select(User).order_by(User.id)).scalars().all()
    return templates.TemplateResponse("admin_users.html", {"request": request, "user": admin, "users": users})

@router.post("/admin/users")
def admin_users_create(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if _get_user_by_email(db, email):
        return templates.TemplateResponse(
            "admin_users.html",
            {
                "request": request,
                "user": admin,
                "users": db.execute(select(User).order_by(User.id)).scalars().all(),
                "error": "Ya existe un usuario con ese email.",
            },
            status_code=400,
        )
    new_user = User(
        name=name.strip(),
        email=email.lower().strip(),
        password_hash=hash_password(password),
        role=Role.admin if role == "admin" else Role.user,
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)

@router.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    target = _get_user_by_id(db, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener al menos 8 caracteres")

    target.password_hash = hash_password(new_password)
    db.add(target)
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)

@router.post("/admin/users/{user_id}/delete")
def admin_delete_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if admin.id == user_id:
        # Evitar que un admin se borre a sí mismo
        raise HTTPException(status_code=400, detail="No puedes borrarte a ti mismo.")
    target = _get_user_by_id(db, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    db.delete(target)
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)