from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from typing import List, Optional
import os

from backend import models, schemas, crud
from backend.database import engine, get_db
from backend.core import model as rec_model

# Create DB tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Recommended Fuel Card System API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health_check():
    from datetime import datetime
    return {
        "status": "ok",
        "service": "recommended-fuel-card-system",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

@app.get("/api/meta")
def get_meta(db: Session = Depends(get_db)):
    clients_count = db.query(models.Client).count()
    suppliers_count = db.query(models.Supplier).count()
    decisions_count = db.query(models.Decision).count()
    sources_count = db.query(models.DataSource).count()
    users_count = db.query(models.User).count()
    import_state = crud.get_import_state(db)
    runs = crud.get_model_runs(db)
    
    return {
        "clients": clients_count,
        "suppliers": suppliers_count,
        "decisions": decisions_count,
        "sources": sources_count,
        "users": users_count,
        "importState": import_state,
        "lastRun": runs[0] if runs else None
    }

@app.get("/api/suppliers", response_model=List[schemas.Supplier])
def get_suppliers(db: Session = Depends(get_db)):
    return crud.get_suppliers(db)

@app.get("/api/clients", response_model=List[schemas.Client])
def get_clients(search: Optional[str] = None, priority: Optional[str] = None, db: Session = Depends(get_db)):
    is_priority = priority == "true"
    return crud.get_clients(db, search=search, priority=is_priority)

@app.get("/api/clients/{client_id}", response_model=schemas.Client)
def get_client(client_id: str, db: Session = Depends(get_db)):
    client = crud.get_client(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client

@app.get("/api/clients/{client_id}/recommendations")
def get_client_recommendations(client_id: str, db: Session = Depends(get_db)):
    client = crud.get_client(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    suppliers = crud.get_suppliers(db)
    return rec_model.get_client_recommendations(client, suppliers)

@app.post("/api/clients/{client_id}/recommendations/run")
def run_client_recommendations(client_id: str, db: Session = Depends(get_db)):
    client = crud.get_client(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    suppliers = crud.get_suppliers(db)
    result = rec_model.invoke_client_recommendation_run(client, suppliers)
    
    run_schema = schemas.ModelRunBase(**result["run"])
    saved_run = crud.create_model_run(db, run_schema)
    
    return {
        "run": saved_run,
        "recommendations": result["recommendations"]
    }

@app.get("/api/clients/{client_id}/transactions")
def get_client_transactions(client_id: str, db: Session = Depends(get_db)):
    client = crud.get_client(db, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    txs = crud.get_client_transactions(db, client_id)
    if txs:
        return txs
    
    # Return mock transactions if none exist
    return rec_model.get_client_transactions_mock(client)

@app.get("/api/decisions", response_model=List[schemas.Decision])
def get_decisions(db: Session = Depends(get_db)):
    return crud.get_decisions(db)

@app.post("/api/decisions", response_model=schemas.Decision, status_code=201)
def create_decision(decision: schemas.DecisionCreate, db: Session = Depends(get_db)):
    return crud.create_decision(db, decision)

@app.get("/api/model-runs", response_model=List[schemas.ModelRun])
def get_model_runs(db: Session = Depends(get_db)):
    return crud.get_model_runs(db)

@app.get("/api/model-catalog")
def get_model_catalog():
    return [
        {"id": "hyb", "name": "Гибридная", "segment": "все клиенты", "hr1": 0.991, "ndcg3": 0.989, "map3": 0.989, "status": "production"},
        {"id": "als", "name": "ALS", "segment": "инертные клиенты", "hr1": 0.999, "ndcg3": 0.997, "map3": 0.999, "status": "production"},
        {"id": "ease", "name": "EASE", "segment": "потенциальные клиенты", "hr1": 0.933, "ndcg3": 0.926, "map3": 0.915, "status": "production"},
        {"id": "cb", "name": "Content-Based", "segment": "для сравнения", "hr1": 0.799, "ndcg3": 0.877, "map3": 0.845, "status": "baseline"},
        {"id": "pop", "name": "MostPop", "segment": "baseline", "hr1": 0.854, "ndcg3": 0.879, "map3": 0.831, "status": "baseline"}
    ]

@app.post("/api/recalculate", status_code=201)
def recalculate_model(db: Session = Depends(get_db)):
    clients = crud.get_clients(db)
    suppliers = crud.get_suppliers(db)
    run_result = rec_model.invoke_recommendation_run(clients, suppliers)
    
    run_schema = schemas.ModelRunBase(**run_result)
    saved_run = crud.create_model_run(db, run_schema)
    return saved_run

@app.get("/api/import-state")
def get_import_state(db: Session = Depends(get_db)):
    return crud.get_import_state(db)

@app.get("/api/data-sources", response_model=List[schemas.DataSource])
def get_data_sources(db: Session = Depends(get_db)):
    return crud.get_data_sources(db)

@app.post("/api/data-sources/{source_id}/reload")
def reload_data_source(source_id: str, db: Session = Depends(get_db)):
    # Mock reload
    return crud.get_data_sources(db)

@app.get("/api/users", response_model=List[schemas.User])
def get_users(db: Session = Depends(get_db)):
    return crud.get_users(db)

@app.post("/api/users", response_model=schemas.User, status_code=201)
def create_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    return crud.create_user(db, user)

@app.post("/api/users/{user_id}/toggle")
def toggle_user(user_id: str, db: Session = Depends(get_db)):
    user = crud.toggle_user_status(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# Import router
from backend.routers import import_excel
app.include_router(import_excel.router)

# Mount frontend
frontend_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
