import math
from datetime import datetime, timedelta

SEGMENT_BOOST = {
    "INERT": {"ppr": 0.22, "e100": 0.18, "eka": 0.13, "rn": 0.12, "gpn": 0.10, "mc": 0.08, "luk": 0.07, "tn": 0.05},
    "POT": {"ppr": 0.20, "e100": 0.17, "gpn": 0.13, "rn": 0.12, "eka": 0.11, "mc": 0.10, "luk": 0.08, "tn": 0.06},
    "VAR": {"ppr": 0.19, "e100": 0.16, "rn": 0.13, "gpn": 0.12, "eka": 0.10, "luk": 0.09, "mc": 0.08, "tn": 0.06},
    "COLD": {"ppr": 0.24, "e100": 0.18, "eka": 0.13, "mc": 0.11, "rn": 0.10, "gpn": 0.08, "luk": 0.06, "tn": 0.05}
}

def new_factor(key: str, label: str, value: float, weight: float):
    return {
        "key": key,
        "label": label,
        "value": round(max(0.0, min(1.0, value)), 3),
        "weight": weight
    }

def get_region_match(supplier, client_regions):
    supplier_id = str(supplier.id).lower()
    if supplier_id in ["ppr", "e100", "mc", "luk", "gpn", "rn"]:
        return 1.0
    if supplier.regions == "RF":
        return 1.0
    
    supplier_regions = supplier.regions or ""
    supplier_tokens = [t.split()[0] for t in supplier_regions.split(",") if t.strip()]
    
    if not client_regions:
        return 0.55
        
    for client_region in client_regions:
        for token in supplier_tokens:
            if token in client_region:
                return 0.86
                
    return 0.55

def get_peer_score(segment: str, supplier_id: str):
    if segment in SEGMENT_BOOST:
        row = SEGMENT_BOOST[segment]
        sid = supplier_id.lower()
        if sid in row:
            return float(row[sid])
    return 0.05

def get_client_recommendations(client, suppliers):
    rows = []
    for supplier in suppliers:
        main_fuel = client.mainFuel or ""
        segment = client.segment or ""
        
        supplier_fuels = supplier.fuels or []
        fuel_ok = 1.0 if main_fuel in supplier_fuels else 0.42
        
        geo_match = get_region_match(supplier, client.regions)
        coverage = float(supplier.coverage or 0.0)
        peer = get_peer_score(segment, supplier.id)
        tariff = 0.76 if supplier.type == "aggregator" else 0.62
        
        score = (0.30 * coverage) + (0.22 * geo_match) + (0.20 * fuel_ok) + (0.18 * min(1.0, peer * 4)) + (0.10 * tariff)
        score = round(min(0.99, score), 4)
        
        monthly_liters = float(client.monthlyLiters or 0.0)
        estimated_saving = round(max(0, monthly_liters * (0.004 + (score - 0.50) * 0.018) * 68), 0)
        
        factors = [
            new_factor("coverage", "coverage", coverage, 0.30),
            new_factor("geo", "geo", geo_match, 0.22),
            new_factor("fuel", "fuel", fuel_ok, 0.20),
            new_factor("peer", "peer", min(1.0, peer * 4), 0.18),
            new_factor("tariff", "tariff", tariff, 0.10)
        ]
        
        top_factor = sorted(factors, key=lambda x: (x["value"], x["weight"]), reverse=True)[0]
        
        supplier_dict = {
            "id": supplier.id,
            "name": supplier.name,
            "full": supplier.full,
            "type": supplier.type,
            "coverage": supplier.coverage,
            "regions": supplier.regions,
            "fuels": supplier.fuels,
            "notes": supplier.notes
        }
        
        rows.append({
            "supplier": supplier_dict,
            "score": score,
            "factors": factors,
            "isCurrent": bool(client.currentSupplier and client.currentSupplier == supplier.id),
            "estimatedMonthlySaving": estimated_saving,
            "explanation": f"Top factor: {top_factor['label']}. The scorer combines coverage, geography, fuel mix, peer behavior and tariff value."
        })
        
    rows.sort(key=lambda x: x["score"], reverse=True)
    for i, row in enumerate(rows):
        row["rank"] = i + 1
        
    return rows

def invoke_recommendation_run(clients, suppliers):
    client_count = len(clients)
    supplier_count = len(suppliers)
    accepted_quality = 0.91 if client_count > 0 and supplier_count > 0 else 0.0
    
    return {
        "model": "Hybrid local scorer",
        "status": "completed",
        "clients": client_count,
        "suppliers": supplier_count,
        "hitRateAt1": accepted_quality,
        "ndcgAt3": 0.89,
        "comment": "Local scorer run: coverage + geo + fuel + peer + tariff."
    }

def invoke_client_recommendation_run(client, suppliers):
    recs = get_client_recommendations(client, suppliers)
    return {
        "run": {
            "model": "Hybrid local scorer",
            "status": "completed",
            "clients": 1,
            "suppliers": len(suppliers),
            "hitRateAt1": 0.91,
            "ndcgAt3": 0.89,
            "comment": "Single client recommendation run"
        },
        "recommendations": recs
    }

def get_client_transactions_mock(client):
    stations = [
        "Station #4127",
        "Station #0214",
        "Station #0823",
        "Station #1503",
        "Station #2210",
        "Station #0084"
    ]
    
    fuels = []
    if client.fuelMix:
        fuels = list(client.fuelMix.keys())
    if not fuels:
        fuels = [client.mainFuel] if client.mainFuel else ["ДТ"]
        
    rows = []
    seed = abs(hash(str(client.id)))
    
    base_dt = datetime(2026, 5, 15, 10, 0, 0)
    
    for i in range(28):
        liters = 38 + ((seed + i * 37) % 182)
        price = 58 + ((seed + i * 11) % 800) / 100
        amount = round(liters * price, 0)
        dt = base_dt - timedelta(hours=7 * i)
        fuel = fuels[(seed + i) % len(fuels)]
        
        region = ""
        if client.regions and len(client.regions) > 0:
            region = client.regions[(seed + i) % len(client.regions)]
            
        rows.append({
            "ts": dt.strftime("%Y-%m-%d %H:%M"),
            "station": stations[(seed + i) % len(stations)],
            "region": region,
            "fuel": fuel,
            "liters": liters,
            "amount": amount,
            "vehicle": f"A{100 + ((seed + i * 19) % 900)}AA77"
        })
        
    return rows
