from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


RANDOM_STATE = 42
NEG_INF_SCORE = -1e9

DATA_DIR = Path(".")
CLIENTS_PATH = DATA_DIR / "df_clients.xlsx"
TX_PATH = DATA_DIR / "df_tx.xlsx"


def load_clients(path: Path = CLIENTS_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Не найден файл {path}. Положите df_clients.xlsx рядом с ноутбуком "
            "или измените CLIENTS_PATH."
        )
    clients = pd.read_excel(path)
    clients["Код клиента"] = clients["Код клиента"].astype(str)
    clients["Дата начала договора"] = pd.to_datetime(
        clients["Дата начала договора"], errors="coerce"
    )
    clients["Дата окончания договора"] = pd.to_datetime(
        clients["Дата окончания договора"], errors="coerce"
    )
    return clients


def load_transactions(path: Path = TX_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Не найден файл {path}. Положите df_tx.xlsx рядом с ноутбуком "
            "или измените TX_PATH."
        )
    tx = pd.read_excel(path)
    tx["Код клиента"] = tx["Код клиента"].astype(str)
    tx["Время транзакции"] = pd.to_datetime(tx["Время транзакции"], errors="coerce")
    return tx


def clean_transactions(tx: pd.DataFrame) -> pd.DataFrame:
    df = tx.copy()
    df["Время транзакции"] = pd.to_datetime(df["Время транзакции"], errors="coerce")
    df = df.dropna(
        subset=[
            "Время транзакции",
            "Код клиента",
            "Объем",
            "Цена клиента",
            "Цена стелы",
        ]
    )
    df = df[
        (df["Объем"] > 0)
        & (df["Цена клиента"] > 0)
        & (df["Цена стелы"] > 0)
    ].copy()

    if "Поставщик" not in df.columns:
        df["Поставщик"] = df["Тип карты"].astype(str).str.split(".").str[0]
    if "Канал" not in df.columns:
        df["Канал"] = df["Тип карты"].astype(str).str.split(".").str[-1]
    if "Тарифный_план" not in df.columns:
        df["Тарифный_план"] = (
            df["Наименование категории тарифа"]
            .astype(str)
            .str.extract(r"-(\d+)")[0]
            .fillna("0")
        )
    if "Номенклатура" not in df.columns:
        df["Номенклатура"] = df["Наименование номенклатуры"].astype(str)

    df["Тип АЗС"] = (
        df["Тип АЗС"].astype(str).str.lower().str.contains("собствен").astype(int)
    )
    df["год"] = df["Время транзакции"].dt.year
    df["месяц"] = df["Время транзакции"].dt.month
    df["неделя"] = df["Время транзакции"].dt.isocalendar().week.astype(int)
    df["час"] = df["Время транзакции"].dt.hour
    df["день_недели"] = df["Время транзакции"].dt.dayofweek
    df["выходной"] = (df["день_недели"] >= 5).astype(int)
    df["ночь"] = df["час"].isin([0, 1, 2, 3, 4, 5, 23]).astype(int)
    df["amount"] = df["Объем"] * df["Цена клиента"]
    df["amount_market"] = df["Объем"] * df["Цена стелы"]
    df["saving"] = df["amount_market"] - df["amount"]
    df["price_ratio"] = df["Цена клиента"] / df["Цена стелы"]
    return df


def clean_clients(clients: pd.DataFrame) -> pd.DataFrame:
    df = clients.copy()
    df["Код клиента"] = df["Код клиента"].astype(str)
    df["Дата начала договора"] = pd.to_datetime(
        df["Дата начала договора"], errors="coerce"
    )
    df["Дата окончания договора"] = pd.to_datetime(
        df["Дата окончания договора"], errors="coerce"
    )
    df["договор_бессрочный"] = df["Дата окончания договора"].isna().astype(int)
    df["заявленный_объем_указан"] = (
        df["Заявленный объем в месяц, л"].fillna(0) > 0
    ).astype(int)
    return df


def _row_entropy(p: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return -np.where(p > 0, p * np.log(p), 0).sum(axis=1)


def _price_sensitivity(group: pd.DataFrame) -> float:
    if len(group) < 5:
        return 0.0
    x = group["Цена клиента"].values
    y = group["Объем"].values
    if x.std() == 0 or y.std() == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def build_client_profile(tx: pd.DataFrame, clients: pd.DataFrame) -> pd.DataFrame:
    g = tx.groupby("Код клиента")
    profile = g.agg(
        tx_count=("Объем", "size"),
        total_volume=("Объем", "sum"),
        avg_volume_per_tx=("Объем", "mean"),
        median_volume=("Объем", "median"),
        std_volume=("Объем", "std"),
        first_tx=("Время транзакции", "min"),
        last_tx=("Время транзакции", "max"),
    )
    profile["std_volume"] = profile["std_volume"].fillna(0)
    profile["activity_days"] = (
        profile["last_tx"] - profile["first_tx"]
    ).dt.days.clip(lower=1)
    profile["tx_per_day"] = profile["tx_count"] / profile["activity_days"]

    fin = g.agg(
        total_spend=("amount", "sum"),
        total_market_spend=("amount_market", "sum"),
        total_saving=("saving", "sum"),
        avg_ticket=("amount", "mean"),
        avg_price_ratio=("price_ratio", "mean"),
    )
    fin["saving_pct"] = (fin["total_saving"] / fin["total_market_spend"]).fillna(0)
    profile = profile.join(fin)

    fuel_mix = (
        tx.groupby(["Код клиента", "Номенклатура"])["Объем"]
        .sum()
        .unstack(fill_value=0)
    )
    fuel_mix = fuel_mix.div(
        fuel_mix.sum(axis=1).replace(0, np.nan), axis=0
    ).fillna(0)
    fuel_mix.columns = [f"share_{c}" for c in fuel_mix.columns]
    profile = profile.join(fuel_mix)
    profile["fuel_entropy"] = _row_entropy(fuel_mix.values)
    profile["fuel_diversity"] = g["Номенклатура"].nunique()

    geo = g.agg(
        unique_regions=("Регион АЗС", "nunique"),
        unique_stations=("Номер АЗС", "nunique"),
        own_station_share=("Тип АЗС", "mean"),
    )
    profile = profile.join(geo)
    region_share = (
        tx.groupby(["Код клиента", "Регион АЗС"])["Объем"]
        .sum()
        .groupby(level=0)
        .apply(lambda s: ((s / s.sum()) ** 2).sum())
    )
    profile["region_hhi"] = region_share
    top_region = (
        tx.groupby(["Код клиента", "Регион АЗС"])["Объем"]
        .sum()
        .reset_index()
        .sort_values(["Код клиента", "Объем"], ascending=[True, False])
        .drop_duplicates("Код клиента")
        .set_index("Код клиента")
    )
    profile["top_region"] = top_region["Регион АЗС"]
    profile["top_region_share"] = (top_region["Объем"] / profile["total_volume"]).fillna(
        0
    )

    profile = profile.join(
        g.agg(share_night=("ночь", "mean"), share_weekend=("выходной", "mean"))
    )
    profile["unique_suppliers"] = g["Поставщик"].nunique()
    profile["unique_channels"] = g["Канал"].nunique()
    sup_share = (
        tx.groupby(["Код клиента", "Поставщик"])["Объем"]
        .sum()
        .groupby(level=0)
        .apply(lambda s: s.max() / s.sum())
    )
    profile["main_supplier_share"] = sup_share
    profile["price_sensitivity"] = g.apply(_price_sensitivity)

    c = clients.set_index("Код клиента")
    cols = [
        "Статус договора",
        "Тип клиента",
        "Офис обслуживания",
        "Заявленный объем в месяц, л",
        "договор_бессрочный",
        "заявленный_объем_указан",
        "Дата начала договора",
        "Вид деятельности клиента",
    ]
    cols = [col for col in cols if col in c.columns]
    profile = profile.join(c[cols], how="left")
    snapshot = tx["Время транзакции"].max()
    profile["contract_age_months"] = (
        (snapshot - profile["Дата начала договора"]).dt.days / 30
    ).round(1)
    months_active = (profile["activity_days"] / 30).clip(lower=1)
    profile["actual_monthly_volume"] = profile["total_volume"] / months_active
    declared = profile["Заявленный объем в месяц, л"].replace(0, np.nan)
    profile["plan_fulfilment"] = (profile["actual_monthly_volume"] / declared).fillna(-1)
    profile["volume_segment"] = pd.qcut(
        profile["total_volume"],
        q=4,
        labels=["S", "M", "L", "XL"],
        duplicates="drop",
    )
    return profile.reset_index()


def build_supplier_profile(tx: pd.DataFrame) -> pd.DataFrame:
    g = tx.groupby("Поставщик")
    profile = g.agg(
        tx_count=("Объем", "size"),
        total_volume=("Объем", "sum"),
        unique_clients=("Код клиента", "nunique"),
        unique_stations=("Номер АЗС", "nunique"),
        unique_regions=("Регион АЗС", "nunique"),
        unique_fuel_groups=("Номенклатура", "nunique"),
        own_station_share=("Тип АЗС", "mean"),
        avg_price_ratio=("price_ratio", "mean"),
        median_price_ratio=("price_ratio", "median"),
        std_price_ratio=("price_ratio", "std"),
        unique_channels=("Канал", "nunique"),
        unique_tariff_plans=("Тарифный_план", "nunique"),
    )
    profile["std_price_ratio"] = profile["std_price_ratio"].fillna(0)
    profile["price_stability"] = 1 / (profile["std_price_ratio"] + 1e-3)
    profile["avg_saving_per_liter"] = tx.groupby("Поставщик").apply(
        lambda d: d["saving"].sum() / d["Объем"].sum()
    )
    fuel_mix = (
        tx.groupby(["Поставщик", "Номенклатура"])["Объем"]
        .sum()
        .unstack(fill_value=0)
    )
    fuel_mix = fuel_mix.div(
        fuel_mix.sum(axis=1).replace(0, np.nan), axis=0
    ).fillna(0)
    fuel_mix.columns = [f"sup_share_{c}" for c in fuel_mix.columns]
    return profile.join(fuel_mix).reset_index()


def build_interaction_features(tx: pd.DataFrame) -> pd.DataFrame:
    inter = (
        tx.groupby(["Код клиента", "Поставщик"])
        .agg(
            tx_count=("Объем", "size"),
            volume=("Объем", "sum"),
            spend=("amount", "sum"),
            saving=("saving", "sum"),
            avg_price_ratio=("price_ratio", "mean"),
            std_price_ratio=("price_ratio", "std"),
            first_tx=("Время транзакции", "min"),
            last_tx=("Время транзакции", "max"),
            regions_used=("Регион АЗС", "nunique"),
            stations_used=("Номер АЗС", "nunique"),
            fuel_groups_used=("Номенклатура", "nunique"),
        )
        .reset_index()
    )
    inter["std_price_ratio"] = inter["std_price_ratio"].fillna(0)
    inter["saving_per_liter"] = inter["saving"] / inter["volume"]
    inter["lifetime_days"] = (inter["last_tx"] - inter["first_tx"]).dt.days
    inter["share_of_client_volume"] = inter["volume"] / inter.groupby("Код клиента")[
        "volume"
    ].transform("sum")
    inter["share_of_supplier_volume"] = inter["volume"] / inter.groupby("Поставщик")[
        "volume"
    ].transform("sum")
    inter["days_since_last_tx"] = (
        tx["Время транзакции"].max() - inter["last_tx"]
    ).dt.days
    return inter


def build_coverage_matrix(tx: pd.DataFrame) -> pd.DataFrame:
    cov = (
        tx.groupby(["Поставщик", "Регион АЗС"])
        .agg(stations=("Номер АЗС", "nunique"), volume=("Объем", "sum"))
        .reset_index()
    )
    cov["volume_share_in_supplier"] = cov["volume"] / cov.groupby("Поставщик")[
        "volume"
    ].transform("sum")
    return cov


def build_fuel_mix_matrix(tx: pd.DataFrame, by: str) -> pd.DataFrame:
    m = tx.groupby([by, "Номенклатура"])["Объем"].sum().unstack(fill_value=0)
    return m.div(m.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)


def build_region_share_client(tx: pd.DataFrame) -> pd.DataFrame:
    m = tx.groupby(["Код клиента", "Регион АЗС"])["Объем"].sum().unstack(fill_value=0)
    return m.div(m.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)


def build_activity_clusters(
    clients: pd.DataFrame, n_clusters: int = 12
) -> pd.DataFrame:
    try:
        from sklearn.cluster import KMeans
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        return pd.DataFrame(
            {"Код клиента": clients["Код клиента"], "activity_cluster": -1}
        )

    text = clients["Вид деятельности клиента"].fillna("не указан").astype(str)
    X = TfidfVectorizer(
        max_features=500, ngram_range=(1, 2), min_df=3
    ).fit_transform(text)
    labels = KMeans(
        n_clusters=n_clusters, random_state=RANDOM_STATE, n_init=10
    ).fit_predict(X)
    return pd.DataFrame(
        {"Код клиента": clients["Код клиента"].values, "activity_cluster": labels}
    )


def build_compatibility_features(
    client_profile: pd.DataFrame,
    supplier_profile: pd.DataFrame,
    fuel_mix_client: pd.DataFrame,
    fuel_mix_supplier: pd.DataFrame,
    coverage_matrix: pd.DataFrame,
    region_share_client: pd.DataFrame,
    interaction_features: pd.DataFrame,
    k_neighbors: int = 20,
) -> pd.DataFrame:
    client_profile = client_profile.copy()
    client_profile["Код клиента"] = client_profile["Код клиента"].astype(str)

    clients = client_profile["Код клиента"].unique()
    suppliers = supplier_profile["Поставщик"].unique()
    pairs = pd.MultiIndex.from_product(
        [clients, suppliers], names=["Код клиента", "Поставщик"]
    ).to_frame(index=False)
    sup_regions = (
        coverage_matrix.groupby("Поставщик")["Регион АЗС"].apply(set).to_dict()
    )

    def _coverage(client_id: str, supplier: str) -> float:
        if client_id not in region_share_client.index:
            return 0.0
        cs = region_share_client.loc[client_id]
        return float(cs[cs.index.isin(sup_regions.get(supplier, set()))].sum())

    pairs["region_coverage_pct"] = [
        _coverage(c, s)
        for c, s in zip(pairs["Код клиента"], pairs["Поставщик"])
    ]

    cm = fuel_mix_client.copy()
    cm.columns = [str(c) for c in cm.columns]
    sm = fuel_mix_supplier.copy()
    sm.columns = [str(c) for c in sm.columns]
    common = sorted(set(cm.columns) & set(sm.columns))
    cm = cm.reindex(columns=common, fill_value=0)
    sm = sm.reindex(columns=common, fill_value=0)
    cm_arr = cm.reindex(pairs["Код клиента"]).fillna(0).to_numpy()
    sm_arr = sm.reindex(pairs["Поставщик"]).fillna(0).to_numpy()
    dot = (cm_arr * sm_arr).sum(axis=1)
    norm_c = np.linalg.norm(cm_arr, axis=1)
    norm_s = np.linalg.norm(sm_arr, axis=1)
    pairs["fuel_match_cosine"] = np.where(
        (norm_c * norm_s) > 0, dot / (norm_c * norm_s + 1e-9), 0.0
    )
    need_mask = cm_arr >= 0.05
    avail_mask = sm_arr >= 0.01
    pairs["fuel_match_overlap"] = (need_mask & avail_mask).sum(axis=1) / np.maximum(
        need_mask.sum(axis=1), 1
    )

    sup_pr = supplier_profile.set_index("Поставщик")["avg_price_ratio"]
    cp = client_profile.set_index("Код клиента")
    pairs["actual_monthly_volume"] = pairs["Код клиента"].map(cp["actual_monthly_volume"])
    avg_market_price = cp["total_market_spend"] / cp["total_volume"]
    pairs["avg_market_price_per_liter"] = pairs["Код клиента"].map(avg_market_price)

    ih = interaction_features.copy()
    ih["Код клиента"] = ih["Код клиента"].astype(str)
    pair_hist = ih[
        ["Код клиента", "Поставщик", "avg_price_ratio"]
    ].rename(columns={"avg_price_ratio": "pair_avg_price_ratio_train"})
    pairs = pairs.merge(pair_hist, on=["Код клиента", "Поставщик"], how="left")

    seg_map = {"S": 0.0, "M": 1.0, "L": 2.0, "XL": 3.0}

    def _seg_float(v: object) -> float:
        if pd.isna(v):
            return 1.0
        return float(seg_map.get(str(v), 1.0))

    coord_idx = cp.index.astype(str)
    seg_arr = np.array(
        [_seg_float(cp.loc[c, "volume_segment"]) for c in coord_idx], dtype=float
    )
    rh_arr = cp.loc[coord_idx, "region_hhi"].astype(float).values
    fe_arr = cp.loc[coord_idx, "fuel_entropy"].astype(float).values
    X_raw = np.column_stack([seg_arr, rh_arr, fe_arr])
    mu = X_raw.mean(axis=0)
    sigma = X_raw.std(axis=0) + 1e-9
    Z = (X_raw - mu) / sigma
    coord = pd.DataFrame(
        {"z0": Z[:, 0], "z1": Z[:, 1], "z2": Z[:, 2]},
        index=coord_idx.astype(str),
    )

    pr_out = pairs["pair_avg_price_ratio_train"].to_numpy(dtype=float).copy()
    cid_vals = pairs["Код клиента"].astype(str).values
    sup_vals = pairs["Поставщик"].values

    for supplier in suppliers:
        idx_sup = np.where(sup_vals == supplier)[0]
        idx_need = idx_sup[np.isnan(pr_out[idx_sup])]
        if len(idx_need) == 0:
            continue
        hist = ih.loc[
            ih["Поставщик"] == supplier, ["Код клиента", "avg_price_ratio"]
        ]
        fallback_price = float(sup_pr.get(supplier, np.nan))
        if len(hist) == 0:
            pr_out[idx_need] = fallback_price
            continue
        hist = hist.groupby("Код клиента", as_index=False)["avg_price_ratio"].mean()
        hist["Код клиента"] = hist["Код клиента"].astype(str)
        hmap = dict(zip(hist["Код клиента"], hist["avg_price_ratio"]))
        valid_nc = [c for c in hmap if c in coord.index]
        if len(valid_nc) == 0:
            pr_out[idx_need] = fallback_price
            continue
        neigh_coord = coord.loc[valid_nc].to_numpy(dtype=float)
        neigh_price = np.array([hmap[c] for c in valid_nc], dtype=float)

        for idx in idx_need:
            cid = cid_vals[idx]
            if cid not in coord.index:
                pr_out[idx] = fallback_price
                continue
            query = coord.loc[cid].to_numpy(dtype=float).reshape(1, -1)
            dist = np.linalg.norm(neigh_coord - query, axis=1)
            k_take = min(k_neighbors, len(dist))
            take_idx = np.argpartition(dist, k_take - 1)[:k_take]
            pr_out[idx] = float(neigh_price[take_idx].mean())

    pairs["personalized_price_ratio"] = pr_out
    pairs["personalized_price_ratio"] = pairs["personalized_price_ratio"].fillna(
        pairs["Поставщик"].map(sup_pr)
    )
    pairs["estimated_monthly_saving"] = (
        pairs["actual_monthly_volume"]
        * pairs["avg_market_price_per_liter"]
        * (1 - pairs["personalized_price_ratio"]).clip(lower=0)
    )
    pairs = pairs.drop(columns=["pair_avg_price_ratio_train"])
    return pairs


def build_all_features(tx: pd.DataFrame, clients: pd.DataFrame) -> dict:
    tx_clean = clean_transactions(tx)
    clients_clean = clean_clients(clients)
    client_profile = build_client_profile(tx_clean, clients_clean)
    client_profile = client_profile.merge(
        build_activity_clusters(clients_clean), on="Код клиента", how="left"
    )
    supplier_profile = build_supplier_profile(tx_clean)
    interaction = build_interaction_features(tx_clean)
    coverage = build_coverage_matrix(tx_clean)
    fuel_mix_client = build_fuel_mix_matrix(tx_clean, "Код клиента")
    fuel_mix_supplier = build_fuel_mix_matrix(tx_clean, "Поставщик")
    region_share_client = build_region_share_client(tx_clean)
    compatibility = build_compatibility_features(
        client_profile,
        supplier_profile,
        fuel_mix_client,
        fuel_mix_supplier,
        coverage,
        region_share_client,
        interaction,
        k_neighbors=20,
    )
    return {
        "tx": tx_clean,
        "clients": clients_clean,
        "client_profile": client_profile,
        "supplier_profile": supplier_profile,
        "interaction_features": interaction,
        "coverage_matrix": coverage,
        "fuel_mix_client": fuel_mix_client,
        "fuel_mix_supplier": fuel_mix_supplier,
        "region_share_client": region_share_client,
        "compatibility_features": compatibility,
    }


@dataclass
class ScoreConfig:
    min_region_coverage: float = 0.30
    min_fuel_overlap: float = 0.50


def _normalize_stability(supplier_profile: pd.DataFrame) -> pd.Series:
    stab = supplier_profile.set_index("Поставщик")["price_stability"]
    log_stab = np.log1p(stab)
    return (
        (log_stab - log_stab.min()) / (log_stab.max() - log_stab.min() + 1e-9)
    ).rename("stability_score")


def build_target(
    compatibility_features: pd.DataFrame,
    supplier_profile: pd.DataFrame,
    interaction_features: pd.DataFrame,
    client_profile: pd.DataFrame,
    cfg: ScoreConfig | None = None,
) -> pd.DataFrame:
    if cfg is None:
        cfg = ScoreConfig()

    df = compatibility_features.copy()
    history_pairs = set(
        zip(
            interaction_features["Код клиента"].astype(str),
            interaction_features["Поставщик"],
        )
    )
    df["has_history"] = [
        (str(c), s) in history_pairs
        for c, s in zip(df["Код клиента"], df["Поставщик"])
    ]
    df["hard_filter_pass"] = (
        (df["region_coverage_pct"] >= cfg.min_region_coverage)
        & (df["fuel_match_overlap"] >= cfg.min_fuel_overlap)
    )
    df["personalized_savings_per_liter"] = (
        (1 - df["personalized_price_ratio"]).clip(lower=0)
        * df["avg_market_price_per_liter"]
    )

    eligible = df["hard_filter_pass"]
    smin = df.loc[eligible, "personalized_savings_per_liter"].min()
    smax = df.loc[eligible, "personalized_savings_per_liter"].max()
    if not eligible.any():
        df["composite_score_raw"] = NEG_INF_SCORE
    elif float(smax - smin) < 1e-12:
        df["composite_score_raw"] = np.where(eligible, 1.0, NEG_INF_SCORE)
    else:
        norm = (df["personalized_savings_per_liter"] - smin) / (smax - smin + 1e-12)
        df["composite_score_raw"] = np.where(eligible, norm, NEG_INF_SCORE)

    df["composite_score"] = df["composite_score_raw"]
    df["score_economy"] = df["composite_score"].replace(NEG_INF_SCORE, np.nan)
    df["score_coverage"] = df["region_coverage_pct"].clip(0, 1)
    df["score_fuel"] = df["fuel_match_overlap"].clip(0, 1)
    df["score_stability"] = df["Поставщик"].map(
        _normalize_stability(supplier_profile)
    ).fillna(0)
    df["top_factor"] = np.where(
        df["hard_filter_pass"], "экономия", "не_проходит_фильтры"
    )
    df["score_pct"] = (
        df["composite_score"].replace(NEG_INF_SCORE, np.nan) * 100
    ).round(1)
    df["expected_savings_pct"] = ((1 - df["personalized_price_ratio"]) * 100).round(2)
    return df.drop(columns=["composite_score_raw"])


def temporal_split(
    tx: pd.DataFrame, split_date: str | pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame]:
    split_ts = pd.Timestamp(split_date)
    df = tx.copy()
    df["Время транзакции"] = pd.to_datetime(df["Время транзакции"])
    return (
        df[df["Время транзакции"] < split_ts].copy(),
        df[df["Время транзакции"] >= split_ts].copy(),
    )


def build_test_ground_truth(tx_test_clean: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        tx_test_clean.groupby(["Код клиента", "Поставщик"])
        .agg(
            volume=("Объем", "sum"),
            spend=("amount", "sum"),
            market_spend=("amount_market", "sum"),
        )
        .reset_index()
    )
    winners = grouped.sort_values(
        ["Код клиента", "volume"], ascending=[True, False]
    ).drop_duplicates("Код клиента")
    return winners.rename(
        columns={
            "Поставщик": "true_supplier",
            "volume": "test_volume",
            "spend": "test_spend",
            "market_spend": "test_market_spend",
        }
    ).reset_index(drop=True)


def _build_interaction_matrix(
    interaction_features: pd.DataFrame,
    value_col: str = "volume",
    transform: str = "log1p",
) -> tuple[np.ndarray, list[str], list[str], dict[str, int], dict[str, int]]:
    df = interaction_features.copy()
    df["Код клиента"] = df["Код клиента"].astype(str)
    clients = sorted(df["Код клиента"].unique().tolist())
    suppliers = sorted(df["Поставщик"].unique().tolist())
    client_idx = {c: i for i, c in enumerate(clients)}
    supplier_idx = {s: i for i, s in enumerate(suppliers)}

    values = df[value_col].astype(float).to_numpy()
    if transform == "log1p":
        values = np.log1p(values)
    elif transform == "binary":
        values = (values > 0).astype(float)

    matrix = np.zeros((len(clients), len(suppliers)), dtype=float)
    for (cid, supplier), value in zip(df[["Код клиента", "Поставщик"]].itertuples(index=False), values):
        matrix[client_idx[cid], supplier_idx[supplier]] = value
    return matrix, clients, suppliers, client_idx, supplier_idx


class NeighborhoodCFRecommender:
    def __init__(self, n_neighbors: int = 30):
        self.n_neighbors = n_neighbors

    def fit(self, interaction_features: pd.DataFrame) -> "NeighborhoodCFRecommender":
        matrix, clients, suppliers, client_idx, supplier_idx = _build_interaction_matrix(
            interaction_features, value_col="volume", transform="log1p"
        )
        row_norm = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
        normalized = matrix / row_norm
        similarity = normalized @ normalized.T
        np.fill_diagonal(similarity, 0.0)
        similarity = np.maximum(similarity, 0.0)

        self.matrix = matrix
        self.clients = clients
        self.suppliers = suppliers
        self.client_idx = client_idx
        self.supplier_idx = supplier_idx
        self.similarity = similarity
        self.popularity = matrix.mean(axis=0)
        return self

    def predict_scores(self, client_ids: list[str]) -> pd.DataFrame:
        rows: list[dict] = []
        for cid in client_ids:
            if cid in self.client_idx:
                uidx = self.client_idx[cid]
                sim = self.similarity[uidx].copy()
                if self.n_neighbors < len(sim):
                    keep = np.argpartition(sim, -self.n_neighbors)[-self.n_neighbors:]
                    mask = np.zeros_like(sim, dtype=bool)
                    mask[keep] = True
                    sim = np.where(mask, sim, 0.0)
                denom = sim.sum()
                scores = (sim @ self.matrix) / (denom + 1e-9) if denom > 0 else self.popularity
                scores = scores + 0.15 * self.matrix[uidx]
            else:
                scores = self.popularity
            for supplier, score in zip(self.suppliers, scores):
                rows.append(
                    {"Код клиента": cid, "Поставщик": supplier, "score": float(score)}
                )
        return pd.DataFrame(rows)


class ALSRecommender:
    def __init__(
        self,
        n_factors: int = 12,
        n_iters: int = 20,
        reg: float = 0.1,
        alpha: float = 20.0,
    ):
        self.n_factors = n_factors
        self.n_iters = n_iters
        self.reg = reg
        self.alpha = alpha

    def fit(self, interaction_features: pd.DataFrame) -> "ALSRecommender":
        matrix, clients, suppliers, client_idx, supplier_idx = _build_interaction_matrix(
            interaction_features, value_col="volume", transform="log1p"
        )
        pref = (matrix > 0).astype(float)
        conf = 1.0 + self.alpha * matrix
        n_users, n_items = matrix.shape
        rng = np.random.default_rng(RANDOM_STATE)
        user_factors = rng.normal(0, 0.1, size=(n_users, self.n_factors))
        item_factors = rng.normal(0, 0.1, size=(n_items, self.n_factors))
        eye = np.eye(self.n_factors)

        for _ in range(self.n_iters):
            yty = item_factors.T @ item_factors
            for uidx in range(n_users):
                cu = np.diag(conf[uidx])
                pu = pref[uidx]
                a = yty + item_factors.T @ (cu - np.eye(n_items)) @ item_factors + self.reg * eye
                b = item_factors.T @ (cu @ pu)
                user_factors[uidx] = np.linalg.solve(a, b)

            xtx = user_factors.T @ user_factors
            for iidx in range(n_items):
                ci = np.diag(conf[:, iidx])
                pi = pref[:, iidx]
                a = xtx + user_factors.T @ (ci - np.eye(n_users)) @ user_factors + self.reg * eye
                b = user_factors.T @ (ci @ pi)
                item_factors[iidx] = np.linalg.solve(a, b)

        self.clients = clients
        self.suppliers = suppliers
        self.client_idx = client_idx
        self.supplier_idx = supplier_idx
        self.user_factors = user_factors
        self.item_factors = item_factors
        self.popularity = matrix.mean(axis=0)
        return self

    def predict_scores(self, client_ids: list[str]) -> pd.DataFrame:
        rows: list[dict] = []
        mean_user = self.user_factors.mean(axis=0)
        for cid in client_ids:
            user_vec = (
                self.user_factors[self.client_idx[cid]]
                if cid in self.client_idx
                else mean_user
            )
            scores = self.item_factors @ user_vec
            if cid not in self.client_idx:
                scores = scores + 0.1 * self.popularity
            for supplier, score in zip(self.suppliers, scores):
                rows.append(
                    {"Код клиента": cid, "Поставщик": supplier, "score": float(score)}
                )
        return pd.DataFrame(rows)


class AutoEncoderCFRecommender:
    def __init__(
        self,
        hidden_dim: int = 8,
        max_iter: int = 500,
        alpha: float = 1e-4,
    ):
        self.hidden_dim = hidden_dim
        self.max_iter = max_iter
        self.alpha = alpha

    def fit(self, interaction_features: pd.DataFrame) -> "AutoEncoderCFRecommender":
        matrix, clients, suppliers, client_idx, supplier_idx = _build_interaction_matrix(
            interaction_features, value_col="volume", transform="log1p"
        )
        scale = matrix.max() + 1e-9
        X = matrix / scale
        self.scale = scale
        self.clients = clients
        self.suppliers = suppliers
        self.client_idx = client_idx
        self.supplier_idx = supplier_idx
        self.popularity = X.mean(axis=0)

        try:
            from sklearn.neural_network import MLPRegressor

            hidden_mid = max(2, min(self.hidden_dim, max(2, X.shape[1] - 1)))
            self.model = MLPRegressor(
                hidden_layer_sizes=(self.hidden_dim, hidden_mid, self.hidden_dim),
                activation="relu",
                solver="adam",
                alpha=self.alpha,
                learning_rate_init=0.01,
                max_iter=self.max_iter,
                random_state=RANDOM_STATE,
            )
            self.model.fit(X, X)
            self.use_mlp = True
            self.reconstructed_ = self.model.predict(X)
        except Exception:
            k = max(1, min(self.hidden_dim, X.shape[1] - 1 if X.shape[1] > 1 else 1))
            u, s, vt = np.linalg.svd(X, full_matrices=False)
            u_k = u[:, :k]
            s_k = s[:k]
            vt_k = vt[:k, :]
            self.reconstructed_ = (u_k * s_k) @ vt_k
            self.use_mlp = False

        return self

    def predict_scores(self, client_ids: list[str]) -> pd.DataFrame:
        rows: list[dict] = []
        mean_reconstruction = self.reconstructed_.mean(axis=0)
        for cid in client_ids:
            if cid in self.client_idx:
                scores = self.reconstructed_[self.client_idx[cid]]
            else:
                scores = mean_reconstruction + 0.2 * self.popularity
            for supplier, score in zip(self.suppliers, scores):
                rows.append(
                    {"Код клиента": cid, "Поставщик": supplier, "score": float(score)}
                )
        return pd.DataFrame(rows)


class LightGCNRecommender:
    def __init__(
        self,
        embedding_dim: int = 16,
        n_layers: int = 2,
        n_epochs: int = 200,
        learning_rate: float = 0.03,
        reg: float = 1e-3,
    ):
        self.embedding_dim = embedding_dim
        self.n_layers = n_layers
        self.n_epochs = n_epochs
        self.learning_rate = learning_rate
        self.reg = reg

    def _propagate(
        self, user_emb: np.ndarray, item_emb: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        user_layers = [user_emb]
        item_layers = [item_emb]
        cur_user = user_emb
        cur_item = item_emb

        for _ in range(self.n_layers):
            next_user = self.norm_adj @ cur_item
            next_item = self.norm_adj.T @ cur_user
            user_layers.append(next_user)
            item_layers.append(next_item)
            cur_user = next_user
            cur_item = next_item

        final_user = np.mean(user_layers, axis=0)
        final_item = np.mean(item_layers, axis=0)
        return final_user, final_item

    def fit(self, interaction_features: pd.DataFrame) -> "LightGCNRecommender":
        matrix, clients, suppliers, client_idx, supplier_idx = _build_interaction_matrix(
            interaction_features, value_col="volume", transform="binary"
        )
        self.clients = clients
        self.suppliers = suppliers
        self.client_idx = client_idx
        self.supplier_idx = supplier_idx
        self.binary_matrix = matrix
        self.popularity = matrix.mean(axis=0)

        user_degree = np.sqrt(matrix.sum(axis=1, keepdims=True) + 1e-9)
        item_degree = np.sqrt(matrix.sum(axis=0, keepdims=True) + 1e-9)
        self.norm_adj = matrix / (user_degree * item_degree)

        rng = np.random.default_rng(RANDOM_STATE)
        self.user_emb = rng.normal(
            0, 0.1, size=(len(clients), self.embedding_dim)
        )
        self.item_emb = rng.normal(
            0, 0.1, size=(len(suppliers), self.embedding_dim)
        )

        all_items = np.arange(len(suppliers))
        observed_items = {
            uidx: np.where(matrix[uidx] > 0)[0] for uidx in range(len(clients))
        }

        for _ in range(self.n_epochs):
            final_user, final_item = self._propagate(self.user_emb, self.item_emb)
            for uidx, positives in observed_items.items():
                if len(positives) == 0 or len(positives) == len(suppliers):
                    continue
                pos = int(rng.choice(positives))
                negatives = all_items[~np.isin(all_items, positives)]
                neg = int(rng.choice(negatives))

                u_vec = final_user[uidx]
                pos_vec = final_item[pos]
                neg_vec = final_item[neg]
                x_uij = float(u_vec @ pos_vec - u_vec @ neg_vec)
                grad_scale = 1.0 / (1.0 + np.exp(x_uij))

                grad_user = grad_scale * (neg_vec - pos_vec) + self.reg * self.user_emb[uidx]
                grad_pos = grad_scale * (-u_vec) + self.reg * self.item_emb[pos]
                grad_neg = grad_scale * (u_vec) + self.reg * self.item_emb[neg]

                self.user_emb[uidx] -= self.learning_rate * grad_user
                self.item_emb[pos] -= self.learning_rate * grad_pos
                self.item_emb[neg] -= self.learning_rate * grad_neg

        self.final_user_emb, self.final_item_emb = self._propagate(
            self.user_emb, self.item_emb
        )
        return self

    def predict_scores(self, client_ids: list[str]) -> pd.DataFrame:
        rows: list[dict] = []
        mean_user = self.final_user_emb.mean(axis=0)
        for cid in client_ids:
            if cid in self.client_idx:
                user_vec = self.final_user_emb[self.client_idx[cid]]
                scores = self.final_item_emb @ user_vec
            else:
                user_vec = mean_user
                scores = self.final_item_emb @ user_vec + 0.1 * self.popularity
            for supplier, score in zip(self.suppliers, scores):
                rows.append(
                    {"Код клиента": cid, "Поставщик": supplier, "score": float(score)}
                )
        return pd.DataFrame(rows)


class MostPopRecommender:
    def __init__(self):
        self.popularity = None
        self.suppliers = None

    def fit(self, interaction_features: pd.DataFrame) -> "MostPopRecommender":
        pop = interaction_features.groupby("Поставщик")["volume"].sum()
        self.suppliers = pop.index.tolist()
        self.popularity = pop.values / (pop.sum() + 1e-9)
        return self

    def predict_scores(self, client_ids: list[str]) -> pd.DataFrame:
        rows: list[dict] = []
        for cid in client_ids:
            for supplier, score in zip(self.suppliers, self.popularity):
                rows.append({"Код клиента": cid, "Поставщик": supplier, "score": float(score)})
        return pd.DataFrame(rows)


class TopPersonalRecommender:
    def __init__(self):
        self.user_history = {}
        self.suppliers = None
        self.popularity = None

    def fit(self, interaction_features: pd.DataFrame) -> "TopPersonalRecommender":
        pop = interaction_features.groupby("Поставщик")["volume"].sum()
        self.suppliers = pop.index.tolist()
        self.popularity = pop.values / (pop.sum() + 1e-9)
        
        g = interaction_features.groupby(["Код клиента", "Поставщик"])["volume"].sum()
        for cid, group in g.groupby(level=0):
            self.user_history[str(cid)] = group.loc[cid].to_dict()
        return self

    def predict_scores(self, client_ids: list[str]) -> pd.DataFrame:
        rows: list[dict] = []
        for cid in client_ids:
            hist = self.user_history.get(cid, {})
            for supplier, pop_score in zip(self.suppliers, self.popularity):
                score = hist.get(supplier, 0.0)
                if score == 0.0:
                    score = pop_score * 1e-6
                rows.append({"Код клиента": cid, "Поставщик": supplier, "score": float(score)})
        return pd.DataFrame(rows)


class EASERecommender:
    def __init__(self, reg: float = 250.0):
        self.reg = reg
    
    def fit(self, interaction_features: pd.DataFrame) -> "EASERecommender":
        matrix, clients, suppliers, client_idx, supplier_idx = _build_interaction_matrix(
            interaction_features, value_col="volume", transform="log1p"
        )
        G = matrix.T @ matrix
        diag_indices = np.diag_indices(G.shape[0])
        G[diag_indices] += self.reg
        
        P = np.linalg.inv(G)
        B = P / (-np.diag(P)[:, None])
        B[diag_indices] = 0.0
        
        self.B = B
        self.matrix = matrix
        self.clients = clients
        self.suppliers = suppliers
        self.client_idx = client_idx
        self.supplier_idx = supplier_idx
        self.popularity = matrix.mean(axis=0)
        return self
        
    def predict_scores(self, client_ids: list[str]) -> pd.DataFrame:
        rows: list[dict] = []
        for cid in client_ids:
            if cid in self.client_idx:
                uidx = self.client_idx[cid]
                scores = self.matrix[uidx] @ self.B
            else:
                scores = self.popularity
            for supplier, score in zip(self.suppliers, scores):
                rows.append({"Код клиента": cid, "Поставщик": supplier, "score": float(score)})
        return pd.DataFrame(rows)


def apply_hard_filter_to_predictions(
    predictions: pd.DataFrame, target_df: pd.DataFrame
) -> pd.DataFrame:
    mask_df = target_df[["Код клиента", "Поставщик", "hard_filter_pass", "composite_score"]].copy()
    merged = predictions.merge(
        mask_df, on=["Код клиента", "Поставщик"], how="left"
    )
    merged["score"] = np.where(
        merged["hard_filter_pass"].fillna(False),
        merged["score"] + 1e-6 * merged["composite_score"].fillna(0),
        NEG_INF_SCORE,
    )
    return merged[["Код клиента", "Поставщик", "score"]]


def hit_rate_at_k(
    predictions: pd.DataFrame, ground_truth: pd.DataFrame, k: int = 1
) -> float:
    df = predictions.sort_values(["Код клиента", "score"], ascending=[True, False]).copy()
    df["rank"] = df.groupby("Код клиента").cumcount() + 1
    topk = df[df["rank"] <= k]
    gt = ground_truth.set_index("Код клиента")["true_supplier"]
    hits = [
        gt.loc[cid] in group["Поставщик"].values
        for cid, group in topk.groupby("Код клиента")
        if cid in gt.index
    ]
    return float(np.mean(hits)) if hits else 0.0


def ndcg_at_k(
    predictions: pd.DataFrame, ground_truth: pd.DataFrame, k: int = 3
) -> float:
    df = predictions.sort_values(["Код клиента", "score"], ascending=[True, False]).copy()
    df["rank"] = df.groupby("Код клиента").cumcount() + 1
    df = df[df["rank"] <= k]
    gt = ground_truth.set_index("Код клиента")["true_supplier"]
    ndcgs: list[float] = []
    for cid, group in df.groupby("Код клиента"):
        if cid not in gt.index:
            continue
        rel = (group["Поставщик"].values == gt.loc[cid]).astype(int)
        dcg = (rel / np.log2(np.arange(2, len(rel) + 2))).sum()
        ndcgs.append(float(dcg))
    return float(np.mean(ndcgs)) if ndcgs else 0.0


def mrr(predictions: pd.DataFrame, ground_truth: pd.DataFrame) -> float:
    df = predictions.sort_values(["Код клиента", "score"], ascending=[True, False]).copy()
    df["rank"] = df.groupby("Код клиента").cumcount() + 1
    gt = ground_truth.set_index("Код клиента")["true_supplier"]
    rr: list[float] = []
    for cid, group in df.groupby("Код клиента"):
        if cid not in gt.index:
            continue
        true_supplier = gt.loc[cid]
        hit_ranks = group.loc[group["Поставщик"] == true_supplier, "rank"]
        rr.append(1.0 / float(hit_ranks.iloc[0]) if len(hit_ranks) > 0 else 0.0)
    return float(np.mean(rr)) if rr else 0.0


def estimated_saving_lift(
    predictions: pd.DataFrame,
    tx_test_clean: pd.DataFrame,
    supplier_profile_train: pd.DataFrame,
) -> dict:
    pred = predictions.sort_values(["Код клиента", "score"], ascending=[True, False]).copy()
    pred["rank"] = pred.groupby("Код клиента").cumcount() + 1
    top1 = pred[pred["rank"] == 1][["Код клиента", "Поставщик"]].rename(
        columns={"Поставщик": "rec_supplier"}
    )
    sup_pr = supplier_profile_train.set_index("Поставщик")["avg_price_ratio"]
    client_test = (
        tx_test_clean.groupby("Код клиента")
        .agg(actual_spend=("amount", "sum"), market_spend=("amount_market", "sum"))
        .reset_index()
    )
    df = client_test.merge(top1, on="Код клиента", how="inner")
    df["rec_price_ratio"] = df["rec_supplier"].map(sup_pr)
    df["hypothetical_spend"] = df["market_spend"] * df["rec_price_ratio"]
    df["lift_rub"] = df["actual_spend"] - df["hypothetical_spend"]
    df["lift_pct"] = df["lift_rub"] / df["actual_spend"]
    return {
        "avg_lift_pct": float(df["lift_pct"].mean()),
        "median_lift_pct": float(df["lift_pct"].median()),
        "total_lift_rub": float(df["lift_rub"].sum()),
        "pct_clients_better": float((df["lift_rub"] > 0).mean()),
        "n_clients_evaluated": int(len(df)),
    }


def evaluate_collaborative_models(
    tx: pd.DataFrame,
    clients: pd.DataFrame,
    split_date: str = "2025-05-15",
    apply_hard_filter: bool = True,
) -> tuple[pd.DataFrame, dict]:
    tx_train, tx_test = temporal_split(tx, split_date)
    features = build_all_features(tx_train, clients)
    target_df = build_target(
        features["compatibility_features"],
        features["supplier_profile"],
        features["interaction_features"],
        features["client_profile"],
    )
    tx_test_clean = clean_transactions(tx_test)
    ground_truth = build_test_ground_truth(tx_test_clean)
    ground_truth = ground_truth[
        ground_truth["Код клиента"].isin(features["client_profile"]["Код клиента"])
    ].copy()
    eligible_clients = ground_truth["Код клиента"].astype(str).tolist()

    models = {
        "MostPop": MostPopRecommender().fit(features["interaction_features"]),
        "TopPersonal": TopPersonalRecommender().fit(features["interaction_features"]),
        "EASE": EASERecommender(reg=250.0).fit(features["interaction_features"]),
        "CF": NeighborhoodCFRecommender(n_neighbors=30).fit(
            features["interaction_features"]
        ),
        "ALS": ALSRecommender(n_factors=12, n_iters=20, reg=0.08, alpha=20.0).fit(
            features["interaction_features"]
        ),
        "AutoEncoder CF": AutoEncoderCFRecommender(
            hidden_dim=8, max_iter=500, alpha=1e-4
        ).fit(features["interaction_features"]),
        "LightGCN": LightGCNRecommender(
            embedding_dim=16, n_layers=2, n_epochs=200, learning_rate=0.03, reg=1e-3
        ).fit(features["interaction_features"]),
    }

    predictions: dict[str, pd.DataFrame] = {}
    for name, model in models.items():
        pred = model.predict_scores(eligible_clients)
        if apply_hard_filter:
            pred = apply_hard_filter_to_predictions(pred, target_df)
        predictions[name] = pred

    rows: list[dict] = []
    for name, pred in predictions.items():
        lift = estimated_saving_lift(pred, tx_test_clean, features["supplier_profile"])
        rows.append(
            {
                "Модель": name,
                "Hit@1": round(hit_rate_at_k(pred, ground_truth, k=1), 3),
                "NDCG@3": round(ndcg_at_k(pred, ground_truth, k=3), 3),
                "MRR": round(mrr(pred, ground_truth), 3),
                "Saving Lift, %": round(lift["avg_lift_pct"] * 100, 2),
                "Total Lift, руб": round(lift["total_lift_rub"], 0),
                "% клиентов с улучшением": round(lift["pct_clients_better"] * 100, 1),
            }
        )

    results = pd.DataFrame(rows).sort_values(
        ["Hit@1", "NDCG@3", "MRR", "Saving Lift, %"],
        ascending=False,
    ).reset_index(drop=True)

    details = {
        "split_date": split_date,
        "features": features,
        "target_df": target_df,
        "ground_truth": ground_truth,
        "tx_test_clean": tx_test_clean,
        "predictions": predictions,
        "apply_hard_filter": apply_hard_filter,
    }
    return results, details
