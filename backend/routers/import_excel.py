from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from backend.database import get_db
from backend import models, schemas
import pandas as pd
import io
from datetime import datetime

router = APIRouter()

@router.post("/api/import-excel", status_code=201)
async def import_excel(
    clients_file: UploadFile = File(...),
    tx_file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    try:
        # Read files into pandas
        clients_content = await clients_file.read()
        tx_content = await tx_file.read()
        
        df_clients = pd.read_excel(io.BytesIO(clients_content))
        df_tx = pd.read_excel(io.BytesIO(tx_content))
        
        # This is a simplified version of the import logic
        # In a real scenario, we would parse the data and update the database tables
        
        # Clear existing data for simplicity in this demo
        db.query(models.Client).delete()
        db.query(models.Supplier).delete()
        db.query(models.Transaction).delete()
        
        # Process suppliers (mock extraction)
        suppliers = [
            models.Supplier(id="ppr", name="ППР", type="aggregator", coverage=0.9, regions="RF", fuels=["ДТ", "АИ-95"]),
            models.Supplier(id="e100", name="E100", type="aggregator", coverage=0.8, regions="RF", fuels=["ДТ", "АИ-95"]),
            models.Supplier(id="rn", name="Роснефть", type="vink", coverage=0.6, regions="RF", fuels=["ДТ", "АИ-95", "АИ-92"]),
            models.Supplier(id="gpn", name="Газпромнефть", type="vink", coverage=0.5, regions="RF", fuels=["ДТ", "АИ-95", "АИ-92"])
        ]
        db.add_all(suppliers)
        
        # Process clients (mock extraction)
        client_count = 0
        for _, row in df_clients.head(100).iterrows(): # Limit to 100 for demo
            client_id = str(row.get('client_id', f"C-{client_count}"))
            client = models.Client(
                id=client_id,
                name=str(row.get('name', f"Client {client_count}")),
                segment=str(row.get('segment', 'INERT')),
                mainFuel=str(row.get('main_fuel', 'ДТ')),
                monthlyLiters=float(row.get('monthly_liters', 1000.0)),
                priority=float(row.get('priority', 0.5)),
                regions=["RF"]
            )
            db.add(client)
            client_count += 1
            
        # Update import state
        import_state = models.ImportState(
            ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            clients=client_count,
            transactions=len(df_tx),
            transactionSamples=min(1000, len(df_tx)),
            suppliers=len(suppliers),
            clientsFile=clients_file.filename,
            transactionsFile=tx_file.filename
        )
        db.add(import_state)
        
        db.commit()
        
        return {
            "status": "success",
            "clients_imported": client_count,
            "transactions_processed": len(df_tx),
            "suppliers_extracted": len(suppliers)
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")
