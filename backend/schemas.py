from pydantic import BaseModel
from typing import List, Dict, Any, Optional

class UserBase(BaseModel):
    name: str
    login: str
    role: Optional[str] = "manager"
    status: Optional[str] = "active"

class UserCreate(UserBase):
    pass

class User(UserBase):
    id: str
    last: str

    class Config:
        from_attributes = True

class DecisionCreate(BaseModel):
    client: str
    manager: str
    rec: str
    action: str
    picked: Optional[str] = None
    note: Optional[str] = ""

class Decision(DecisionCreate):
    id: int
    ts: str

    class Config:
        from_attributes = True

class ClientBase(BaseModel):
    id: str
    name: str
    inn: Optional[str] = None
    industry: Optional[str] = None
    segment: Optional[str] = None
    fleet: Optional[int] = None
    mainFuel: Optional[str] = None
    regions: Optional[List[str]] = None
    monthlyLiters: Optional[float] = None
    monthlySpend: Optional[float] = None
    currentSupplier: Optional[str] = None
    contractEnd: Optional[str] = None
    manager: Optional[str] = None
    office: Optional[str] = None
    status: Optional[str] = None
    clientType: Optional[str] = None
    signals: Optional[List[str]] = None
    priority: Optional[float] = None
    history: Optional[List[Dict[str, Any]]] = None
    fuelMix: Optional[Dict[str, float]] = None
    geoSplit: Optional[Dict[str, float]] = None

class Client(ClientBase):
    class Config:
        from_attributes = True

class SupplierBase(BaseModel):
    id: str
    name: str
    full: Optional[str] = None
    type: Optional[str] = None
    coverage: Optional[float] = None
    regions: Optional[str] = None
    fuels: Optional[List[str]] = None
    notes: Optional[str] = None

class Supplier(SupplierBase):
    class Config:
        from_attributes = True

class ModelRunBase(BaseModel):
    model: str
    status: str
    clients: int
    suppliers: int
    hitRateAt1: float
    ndcgAt3: float
    comment: str

class ModelRun(ModelRunBase):
    id: int
    ts: str

    class Config:
        from_attributes = True

class DataSourceBase(BaseModel):
    id: str
    name: str
    last: str
    status: str
    rows: int
    delta: str

class DataSource(DataSourceBase):
    class Config:
        from_attributes = True

class ImportStateBase(BaseModel):
    ts: str
    clients: int
    transactions: int
    transactionSamples: int
    suppliers: int
    clientsFile: str
    transactionsFile: str

class ImportState(ImportStateBase):
    id: int

    class Config:
        from_attributes = True
