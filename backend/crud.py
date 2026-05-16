from sqlalchemy.orm import Session
from backend import models, schemas
from datetime import datetime
import uuid

def get_clients(db: Session, search: str = None, priority: bool = False):
    query = db.query(models.Client)
    if search:
        search = search.lower()
        query = query.filter(
            models.Client.name.ilike(f"%{search}%") |
            models.Client.id.ilike(f"%{search}%") |
            models.Client.inn.ilike(f"%{search}%")
        )
    if priority:
        query = query.filter(models.Client.priority >= 0.60)
    
    return query.order_by(models.Client.priority.desc()).all()

def get_client(db: Session, client_id: str):
    return db.query(models.Client).filter(models.Client.id == client_id).first()

def get_suppliers(db: Session):
    return db.query(models.Supplier).all()

def get_decisions(db: Session):
    return db.query(models.Decision).order_by(models.Decision.id.desc()).all()

def create_decision(db: Session, decision: schemas.DecisionCreate):
    db_decision = models.Decision(
        **decision.model_dump(),
        ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    db.add(db_decision)
    db.commit()
    db.refresh(db_decision)
    return db_decision

def get_model_runs(db: Session):
    return db.query(models.ModelRun).order_by(models.ModelRun.id.desc()).all()

def create_model_run(db: Session, run: schemas.ModelRunBase):
    db_run = models.ModelRun(
        **run.model_dump(),
        ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    db.add(db_run)
    db.commit()
    db.refresh(db_run)
    return db_run

def get_data_sources(db: Session):
    return db.query(models.DataSource).all()

def get_import_state(db: Session):
    return db.query(models.ImportState).order_by(models.ImportState.id.desc()).first()

def get_users(db: Session):
    return db.query(models.User).all()

def create_user(db: Session, user: schemas.UserCreate):
    db_user = models.User(
        **user.model_dump(),
        id=f"u{uuid.uuid4().hex[:8]}",
        last="-"
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def toggle_user_status(db: Session, user_id: str):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user:
        user.status = "blocked" if user.status == "active" else "active"
        db.commit()
        db.refresh(user)
    return user

def get_client_transactions(db: Session, client_id: str):
    return db.query(models.Transaction).filter(models.Transaction.client == client_id).all()
