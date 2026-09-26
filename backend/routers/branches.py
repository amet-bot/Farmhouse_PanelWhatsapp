from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List

from database import get_db
from models.branch import Branch
from schemas.branch import BranchResponse, BranchCreate, BranchUpdate
from security.auth import require_role

router = APIRouter(prefix="/branches", tags=["Sucursales"])

@router.get("/", response_model=List[BranchResponse])
def get_branches(
    db: Session = Depends(get_db)
):
    """
    Retorna la lista oficial de sucursales activas de Farmhouse desde SQL Server.
    Endpoint público accesible sin token para alimentar selectores y navegación.
    """
    branches = db.query(Branch).filter(Branch.active == True).order_by(Branch.id).all()
    return branches


@router.get("/admin", response_model=List[BranchResponse], dependencies=[Depends(require_role(["admin"]))])
def get_branches_admin(db: Session = Depends(get_db)):
    """
    Todas las sucursales, activas e inactivas — para el panel de Administración. Separado del
    endpoint público de arriba para no meterle autenticación a algo que hoy alimenta selectores
    sin login (Menú Digital, etc.).
    """
    return db.query(Branch).order_by(Branch.id).all()


@router.post("/", response_model=BranchResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role(["admin"]))])
def create_branch(
    branch_in: BranchCreate,
    db: Session = Depends(get_db),
):
    existing = db.query(Branch).filter(
        (func.lower(Branch.name) == branch_in.name.strip().lower()) | (func.lower(Branch.code) == branch_in.code.strip().lower())
    ).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe una sucursal con ese nombre o código.")

    branch = Branch(
        name=branch_in.name.strip(),
        code=branch_in.code.strip().upper(),
        color=branch_in.color,
        active=branch_in.active if branch_in.active is not None else True,
        address=branch_in.address,
        latitude=branch_in.latitude,
        longitude=branch_in.longitude,
        accepts_delivery=branch_in.accepts_delivery,
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


@router.put("/{branch_id}", response_model=BranchResponse, dependencies=[Depends(require_role(["admin"]))])
def update_branch(
    branch_id: int,
    branch_in: BranchUpdate,
    db: Session = Depends(get_db),
):
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sucursal no encontrada.")

    update_data = branch_in.model_dump(exclude_unset=True)
    if "name" in update_data:
        dup = db.query(Branch).filter(func.lower(Branch.name) == update_data["name"].strip().lower(), Branch.id != branch_id).first()
        if dup:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe otra sucursal con ese nombre.")
        update_data["name"] = update_data["name"].strip()
    if "code" in update_data:
        dup = db.query(Branch).filter(func.lower(Branch.code) == update_data["code"].strip().lower(), Branch.id != branch_id).first()
        if dup:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe otra sucursal con ese código.")
        update_data["code"] = update_data["code"].strip().upper()

    for field, val in update_data.items():
        setattr(branch, field, val)

    db.commit()
    db.refresh(branch)
    return branch


@router.post("/{branch_id}/toggle-active", response_model=BranchResponse, dependencies=[Depends(require_role(["admin"]))])
def toggle_branch_active(
    branch_id: int,
    db: Session = Depends(get_db),
):
    """
    Desactivar y no borrar: una sucursal tiene usuarios, dispositivos, pedidos e inventario
    encadenados. Desactivarla la saca de los selectores públicos sin romper el historial.
    """
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sucursal no encontrada.")
    branch.active = not branch.active
    db.commit()
    db.refresh(branch)
    return branch
