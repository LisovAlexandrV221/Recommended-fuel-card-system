from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, JSON, DateTime
from sqlalchemy.orm import relationship
from backend.database import Base
from datetime import datetime

class User(Base):
    __tablename__ = "users"
    
    id = Column(String, primary_key=True, index=True)
    name = Column(String, index=True)
    login = Column(String, unique=True, index=True)
    role = Column(String)
    status = Column(String)
    last = Column(String)

class Client(Base):
    __tablename__ = "clients"
    
    id = Column(String, primary_key=True, index=True)
    name = Column(String, index=True)
    inn = Column(String)
    industry = Column(String)
    segment = Column(String)
    fleet = Column(Integer)
    mainFuel = Column(String)
    regions = Column(JSON)
    monthlyLiters = Column(Float)
    monthlySpend = Column(Float)
    currentSupplier = Column(String)
    contractEnd = Column(String, nullable=True)
    manager = Column(String)
    office = Column(String)
    status = Column(String)
    clientType = Column(String)
    signals = Column(JSON)
    priority = Column(Float)
    history = Column(JSON)
    fuelMix = Column(JSON)
    geoSplit = Column(JSON)

class Supplier(Base):
    __tablename__ = "suppliers"
    
    id = Column(String, primary_key=True, index=True)
    name = Column(String, index=True)
    full = Column(String)
    type = Column(String)
    coverage = Column(Float)
    regions = Column(String)
    fuels = Column(JSON)
    notes = Column(String)

class Transaction(Base):
    __tablename__ = "transactions"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    client = Column(String, ForeignKey("clients.id"), index=True)
    ts = Column(String)
    station = Column(String)
    region = Column(String)
    fuel = Column(String)
    liters = Column(Float)
    amount = Column(Float)
    vehicle = Column(String)

class Decision(Base):
    __tablename__ = "decisions"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ts = Column(String)
    client = Column(String, ForeignKey("clients.id"), index=True)
    manager = Column(String)
    rec = Column(String)
    action = Column(String)
    picked = Column(String, nullable=True)
    note = Column(String, nullable=True)

class ModelRun(Base):
    __tablename__ = "model_runs"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ts = Column(String)
    model = Column(String)
    status = Column(String)
    clients = Column(Integer)
    suppliers = Column(Integer)
    hitRateAt1 = Column(Float)
    ndcgAt3 = Column(Float)
    comment = Column(String)

class DataSource(Base):
    __tablename__ = "data_sources"
    
    id = Column(String, primary_key=True, index=True)
    name = Column(String)
    last = Column(String)
    status = Column(String)
    rows = Column(Integer)
    delta = Column(String)

class ImportState(Base):
    __tablename__ = "import_state"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ts = Column(String)
    clients = Column(Integer)
    transactions = Column(Integer)
    transactionSamples = Column(Integer)
    suppliers = Column(Integer)
    clientsFile = Column(String)
    transactionsFile = Column(String)
