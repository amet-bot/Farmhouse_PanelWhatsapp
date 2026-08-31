import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from sqlalchemy import func

from database import get_db
from models.user import User
from models.branch import Branch
from models.conversation import Conversation
from models.device import Device
from models.message import Message
from schemas.user import UserResponse, UserCreate, UserUpdate
from security.auth import get_current_user, require_role, get_password_hash

logger = logging.getLogger("farmhouse.users")

router = APIRouter(prefix="/users", tags=["Usuarios"])

@router.get("/", response_model=List[UserResponse])
def get_users(
    branch_id: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(User)
    
    if current_user.role == "agent" and current_user.branch_id:
        query = query.filter(User.branch_id == current_user.branch_id, User.active == True)
    else:
        if branch_id:
            query = query.filter(User.branch_id == branch_id)
        
    return query.order_by(User.id.desc()).offset(skip).limit(limit).all()

@router.post("/", response_model=UserResponse, dependencies=[Depends(require_role(["admin"]))])
def create_user(user_in: UserCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    username_clean = user_in.username.strip().lower()
    
    # 1. Validar que el nombre de usuario no esté registrado
    existing = db.query(User).filter(func.lower(User.username) == username_clean).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El nombre de usuario o código ya está registrado en el sistema."
        )

    # 2. Validar que el email no esté registrado (columna UNIQUE en MySQL)
    email_clean = user_in.email.strip().lower() if user_in.email else None
    if email_clean:
        existing_email = db.query(User).filter(func.lower(User.email) == email_clean).first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El correo electrónico ya está registrado en el sistema."
            )

    # 3. Validar sucursal según rol
    branch_id_val = None
    if user_in.role == "agent":
        if not user_in.branch_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Para un agente es obligatorio asignar una sucursal existente."
            )
        branch = db.query(Branch).filter(Branch.id == user_in.branch_id, Branch.active == True).first()
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="La sucursal seleccionada no existe o se encuentra inactiva."
            )
        branch_id_val = branch.id
    elif user_in.branch_id:
        branch = db.query(Branch).filter(Branch.id == user_in.branch_id, Branch.active == True).first()
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="La sucursal seleccionada no existe."
            )
        branch_id_val = branch.id

    # 4. Generar hash bcrypt real y seguro
    hashed_pwd = get_password_hash(user_in.password.strip())

    user = User(
        username=username_clean,
        name=user_in.name.strip(),
        email=email_clean,
        password_hash=hashed_pwd,
        role=user_in.role,
        branch_id=branch_id_val,
        avatar_url=user_in.avatar_url,
        active=user_in.active if user_in.active is not None else True
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info(f"Nuevo usuario creado por Admin ({current_user.username}): '{user.name}' (@{user.username}, Rol: '{user.role}', Sucursal ID: {user.branch_id}, Activo: {user.active})")
    return user

@router.put("/{user_id}", response_model=UserResponse, dependencies=[Depends(require_role(["admin"]))])
def update_user(
    user_id: int,
    user_in: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en la base de datos.")

    update_data = user_in.model_dump(exclude_unset=True)

    # Validar username único si se modifica
    if "username" in update_data and update_data["username"]:
        new_username = update_data["username"].strip().lower()
        if new_username != user.username.lower():
            dup = db.query(User).filter(func.lower(User.username) == new_username, User.id != user_id).first()
            if dup:
                raise HTTPException(status_code=400, detail="El nuevo nombre de usuario ya está en uso.")
            user.username = new_username
        del update_data["username"]

    # Validar email único si se modifica (columna UNIQUE en MySQL)
    if "email" in update_data:
        if update_data["email"]:
            new_email = update_data["email"].strip().lower()
            if new_email != (user.email or "").lower():
                dup_email = db.query(User).filter(func.lower(User.email) == new_email, User.id != user_id).first()
                if dup_email:
                    raise HTTPException(status_code=400, detail="El correo electrónico ya está en uso por otro usuario.")
            user.email = new_email
        else:
            user.email = None
        del update_data["email"]

    # Hashear nueva contraseña si se proporciona
    if "password" in update_data:
        if update_data["password"]:
            user.password_hash = get_password_hash(update_data["password"].strip())
            logger.info(f"Contraseña actualizada para usuario ID {user.id} (@{user.username}).")
        del update_data["password"]

    # Validar sucursal si se actualiza
    if "branch_id" in update_data and update_data["branch_id"]:
        b = db.query(Branch).filter(Branch.id == update_data["branch_id"]).first()
        if not b:
            raise HTTPException(status_code=404, detail="La sucursal especificada no existe.")

    for field, val in update_data.items():
        setattr(user, field, val)

    db.commit()
    db.refresh(user)
    logger.info(f"Usuario actualizado por Admin ({current_user.username}): ID {user.id} '{user.name}' (@{user.username}, Rol: '{user.role}', Activo: {user.active})")
    return user

@router.post("/{user_id}/toggle-active", response_model=UserResponse, dependencies=[Depends(require_role(["admin"]))])
def toggle_user_active(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        
    if current_user.id == user_id and user.active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes desactivar tu propia cuenta mientras estás conectado."
        )

    if user.role == "admin" and user.active:
        active_admins = db.query(User).filter(
            User.role == "admin",
            User.active == True,
            User.id != user_id
        ).count()
        if active_admins < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Debe existir al menos un administrador activo en el sistema."
            )

    user.active = not user.active
    db.commit()
    db.refresh(user)
    return user

@router.delete("/{user_id}", dependencies=[Depends(require_role(["admin"]))])
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    # 1. Seguridad: No permitir que el usuario elimine su propia cuenta
    if current_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes eliminar tu propia cuenta mientras estás conectado."
        )

    # 2. Seguridad: Impedir eliminar el único administrador activo
    if user.role == "admin":
        active_admins = db.query(User).filter(
            User.role == "admin",
            User.active == True,
            User.id != user_id
        ).count()
        if active_admins < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Debe existir al menos un administrador activo en el sistema."
            )

    # 3. Validar si tiene conversaciones activas/abiertas
    active_convs = db.query(Conversation).filter(
        Conversation.assigned_user_id == user_id,
        Conversation.status.in_(["open", "new", "pending"])
    ).count()
    if active_convs > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este usuario tiene conversaciones activas. Transfiere o reasigna las conversaciones antes de eliminarlo."
        )

    # 4. Desvincular de forma segura registros históricos para evitar errores de Foreign Key
    db.query(Device).filter(Device.assigned_user_id == user_id).update({"assigned_user_id": None}, synchronize_session=False)
    db.query(Conversation).filter(Conversation.assigned_user_id == user_id).update({"assigned_user_id": None}, synchronize_session=False)
    db.query(Message).filter(Message.sender_id == user_id).update({"sender_id": None}, synchronize_session=False)

    # 5. Eliminación física en SQL Server
    db.delete(user)
    db.commit()

    return {
        "status": "deleted",
        "id": user_id,
        "message": "Usuario eliminado correctamente."
    }