from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models.branch import Branch
from schemas.branch import BranchResponse
from security.auth import get_current_user
from models.user import User

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
