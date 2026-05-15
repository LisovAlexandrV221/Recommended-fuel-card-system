

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)


# 0. ВРЕМЕННОЙ СПЛИТ

def temporal_split(tx: pd.DataFrame, split_date: str | pd.Timestamp
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Делит транзакции по дате на train/test."""
    split_ts = pd.Timestamp(split_date)
    tx = tx.copy()
    tx["Время транзакции"] = pd.to_datetime(tx["Время транзакции"])
    train = tx[tx["Время транзакции"] < split_ts].copy()
    test = tx[tx["Время транзакции"] >= split_ts].copy()
    return train, test


# 1. GROUND TRUTH ИЗ TEST-ПЕРИОДА

def build_test_ground_truth(tx_test_clean: pd.DataFrame) -> pd.DataFrame:
    """
    Для каждого клиента в test определяет «реального победителя» —
    поставщика с максимальным объёмом в test-периоде.

    Возвращает DF: Код клиента, true_supplier, test_volume, test_spend, test_market_spend
    """
    g = tx_test_clean.groupby(["Код клиента", "Поставщик"]).agg(
        volume=("Объем", "sum"),
        spend=("amount", "sum"),
        market_spend=("amount_market", "sum"),
    ).reset_index()

    # winner = поставщик с max volume у клиента
    winners = g.sort_values(
        ["Код клиента", "volume"], ascending=[True, False]
    ).drop_duplicates("Код клиента")

    winners = winners.rename(columns={
        "Поставщик": "true_supplier",
        "volume": "test_volume",
        "spend": "test_spend",
        "market_spend": "test_market_spend",
    })
    return winners.reset_index(drop=True)


# 2. МОДЕЛЬ A — CONTENT-BASED 

class ContentBasedRecommender:
    """
    Использует composite_score из Этапа 2.
    Без обучения — это система на правилах + взвешенная сумма компонентов.
    """

    def __init__(self):
        self.target_df = None

    def fit(self, target_df: pd.DataFrame):
        self.target_df = target_df.copy()
        return self

    def predict_scores(self, client_ids: list) -> pd.DataFrame:
        """Возвращает score для всех (client, supplier) среди client_ids."""
        df = self.target_df[self.target_df["Код клиента"].isin(client_ids)]
        return df[["Код клиента", "Поставщик", "composite_score"]].rename(
            columns={"composite_score": "score"}
        )


# 3. МОДЕЛЬ B — КОЛЛАБОРАТИВНАЯ ФИЛЬТРАЦИЯ (ALS)

class CollaborativeFilteringRecommender:
    """
    ALS (Alternating Least Squares) на матрице implicit feedback.
    Значения матрицы = volume(client, supplier) — масштаб взаимодействия.

    Внимание: при 4 поставщиках MF почти бесполезен (фактор-пространство
    переразмерено). Реализован для честного сравнения.
    """

    def __init__(self, n_factors: int = 8, n_iters: int = 30, reg: float = 0.1):
        self.n_factors = n_factors
        self.n_iters = n_iters
        self.reg = reg
        self.client_factors = None
        self.supplier_factors = None
        self.client_idx = None
        self.supplier_idx = None

    def fit(self, interaction_features: pd.DataFrame):
        """Обучает ALS на данных взаимодействий."""
        df = interaction_features.copy()
        # log-нормировка volume для устойчивости
        df["interaction"] = np.log1p(df["volume"])

        clients = sorted(df["Код клиента"].unique())
        suppliers = sorted(df["Поставщик"].unique())
        self.client_idx = {c: i for i, c in enumerate(clients)}
        self.supplier_idx = {s: i for i, s in enumerate(suppliers)}

        n_c, n_s = len(clients), len(suppliers)
        R = np.zeros((n_c, n_s))
        for _, row in df.iterrows():
            R[self.client_idx[row["Код клиента"]],
              self.supplier_idx[row["Поставщик"]]] = row["interaction"]

        # ALS
        np.random.seed(42)
        U = np.random.randn(n_c, self.n_factors) * 0.1
        V = np.random.randn(n_s, self.n_factors) * 0.1
        I_f = np.eye(self.n_factors) * self.reg

        for _ in range(self.n_iters):
            # Update U: для каждого клиента
            VtV = V.T @ V + I_f
            U = (R @ V) @ np.linalg.inv(VtV)
            # Update V: для каждого поставщика
            UtU = U.T @ U + I_f
            V = (R.T @ U) @ np.linalg.inv(UtU)

        self.client_factors = U
        self.supplier_factors = V
        self.suppliers = suppliers
        return self

    def predict_scores(self, client_ids: list) -> pd.DataFrame:
        rows = []
        for c in client_ids:
            if c not in self.client_idx:
                # Холодный старт: средние факторы
                u = self.client_factors.mean(axis=0)
            else:
                u = self.client_factors[self.client_idx[c]]
            scores = self.supplier_factors @ u  # (n_suppliers,)
            for s, sc in zip(self.suppliers, scores):
                rows.append({"Код клиента": c, "Поставщик": s, "score": float(sc)})
        return pd.DataFrame(rows)


# 4. МОДЕЛЬ C — LEARNING-TO-RANK (LightGBM Ranker)

class LTRRecommender:
    """
    LightGBM с objective=lambdarank.
    Учится ранжировать поставщиков для клиента по composite_score (soft target).

    Если LightGBM недоступен, fallback на градиентный бустинг через scikit-learn
    с pairwise-loss приближённо (не идеально, но работает).
    """

    def __init__(self, n_estimators: int = 200, learning_rate: float = 0.05):
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.model = None
        self.feature_cols = None
        self.use_lgb = True

    def _build_feature_matrix(self,
                              compatibility_features: pd.DataFrame,
                              client_profile: pd.DataFrame,
                              supplier_profile: pd.DataFrame) -> pd.DataFrame:
        """Готовит X для модели — мерджим всё в одну плоскую таблицу."""
        df = compatibility_features.copy()

        cp_cols_num = [
            "total_volume", "total_spend", "tx_count",
            "avg_ticket", "avg_price_ratio", "saving_pct",
            "unique_regions", "region_hhi", "fuel_entropy",
            "main_supplier_share", "price_sensitivity",
            "share_night", "share_weekend", "own_station_share",
            "contract_age_months", "actual_monthly_volume",
        ]
        df = df.merge(
            client_profile[["Код клиента"] + cp_cols_num],
            on="Код клиента", how="left", suffixes=("", "_client")
        )

        sp_cols_num = [
            "total_volume", "unique_clients", "unique_stations",
            "unique_regions", "own_station_share",
            "avg_price_ratio", "std_price_ratio",
            "price_stability", "avg_saving_per_liter",
        ]
        sp = supplier_profile[["Поставщик"] + sp_cols_num].rename(
            columns={c: f"sup_{c}" for c in sp_cols_num}
        )
        df = df.merge(sp, on="Поставщик", how="left")

        feature_cols = [c for c in df.columns
                        if c not in ["Код клиента", "Поставщик",
                                     "composite_score", "hard_filter_pass",
                                     "has_history", "top_factor",
                                     "score_pct", "score_economy",
                                     "score_coverage", "score_fuel",
                                     "score_stability", "expected_savings_pct"]
                        and df[c].dtype in [np.float64, np.int64, np.float32, np.int32]]

        return df, feature_cols

    def fit(self,
            target_df: pd.DataFrame,
            compatibility_features: pd.DataFrame,
            client_profile: pd.DataFrame,
            supplier_profile: pd.DataFrame):
        """Учит ранкер на composite_score как soft-таргете."""
        df, feat_cols = self._build_feature_matrix(
            compatibility_features, client_profile, supplier_profile
        )
        df = df.merge(
            target_df[["Код клиента", "Поставщик", "composite_score"]],
            on=["Код клиента", "Поставщик"], how="left"
        )
        df = df.dropna(subset=["composite_score"])
        df = df.sort_values("Код клиента").reset_index(drop=True)

        X = df[feat_cols].fillna(0)
        y = df["composite_score"]
        groups = df.groupby("Код клиента").size().values

        try:
            import lightgbm as lgb
            self.model = lgb.LGBMRanker(
                objective="lambdarank",
                n_estimators=self.n_estimators,
                learning_rate=self.learning_rate,
                num_leaves=31,
                min_data_in_leaf=10,
                verbose=-1,
            )
            y_int = pd.qcut(y, q=5, labels=False, duplicates="drop").fillna(0).astype(int)
            self.model.fit(X, y_int, group=groups)
            self.use_lgb = True
        except (ImportError, Exception) as e:
            # Fallback на регрессионный GBM
            from sklearn.ensemble import GradientBoostingRegressor
            self.model = GradientBoostingRegressor(
                n_estimators=self.n_estimators,
                learning_rate=self.learning_rate,
                max_depth=4,
                random_state=42,
            )
            self.model.fit(X, y)
            self.use_lgb = False

        self.feature_cols = feat_cols
        return self

    def predict_scores(self,
                       client_ids: list,
                       compatibility_features: pd.DataFrame,
                       client_profile: pd.DataFrame,
                       supplier_profile: pd.DataFrame) -> pd.DataFrame:
        df, _ = self._build_feature_matrix(
            compatibility_features, client_profile, supplier_profile
        )
        df = df[df["Код клиента"].isin(client_ids)].copy()
        X = df[self.feature_cols].fillna(0)
        df["score"] = self.model.predict(X)
        return df[["Код клиента", "Поставщик", "score"]]


# 5. МЕТРИКИ

def hit_rate_at_k(predictions: pd.DataFrame,
                  ground_truth: pd.DataFrame,
                  k: int = 1) -> float:
    """Доля клиентов, у которых истинный поставщик в top-k рекомендаций."""
    df = predictions.sort_values(
        ["Код клиента", "score"], ascending=[True, False]
    )
    df["rank"] = df.groupby("Код клиента").cumcount() + 1
    topk = df[df["rank"] <= k]

    gt = ground_truth.set_index("Код клиента")["true_supplier"]
    hits = []
    for client, group in topk.groupby("Код клиента"):
        if client not in gt.index:
            continue
        true_sup = gt.loc[client]
        hits.append(true_sup in group["Поставщик"].values)
    return np.mean(hits) if hits else 0.0


def ndcg_at_k(predictions: pd.DataFrame,
              ground_truth: pd.DataFrame,
              k: int = 3) -> float:
    """
    NDCG@k с бинарной релевантностью (1 = истинный поставщик, 0 = остальные).
    При k=1 эквивалентно Hit Rate@1.
    """
    df = predictions.sort_values(
        ["Код клиента", "score"], ascending=[True, False]
    )
    df["rank"] = df.groupby("Код клиента").cumcount() + 1
    df = df[df["rank"] <= k]

    gt = ground_truth.set_index("Код клиента")["true_supplier"]
    ndcgs = []
    for client, group in df.groupby("Код клиента"):
        if client not in gt.index:
            continue
        true_sup = gt.loc[client]
        rel = (group["Поставщик"].values == true_sup).astype(int)
        dcg = (rel / np.log2(np.arange(2, len(rel) + 2))).sum()
        idcg = 1.0 / np.log2(2) 
        ndcgs.append(dcg / idcg)
    return np.mean(ndcgs) if ndcgs else 0.0


def estimated_saving_lift(predictions: pd.DataFrame,
                          ground_truth: pd.DataFrame,
                          tx_test_clean: pd.DataFrame,
                          supplier_profile_train: pd.DataFrame
                          ) -> dict:
    """
    Главная бизнес-метрика.

    Логика:
      Для каждого клиента в test берём топ-1 поставщика по предсказанию модели.
      Считаем, сколько клиент потратил БЫ, если бы все его test-транзакции
      прошли через рекомендованного поставщика по avg_price_ratio этого
      поставщика (из train).
      Сравниваем с фактическими тратами клиента в test-периоде.

    Возвращает:
      avg_lift_pct          — средняя экономия в %
      total_lift_rub        — суммарная экономия по всем клиентам в руб
      pct_clients_better    — доля клиентов, для которых рекомендация выгоднее факта
    """
    pred = predictions.sort_values(
        ["Код клиента", "score"], ascending=[True, False]
    )
    pred["rank"] = pred.groupby("Код клиента").cumcount() + 1
    top1 = pred[pred["rank"] == 1][["Код клиента", "Поставщик"]].rename(
        columns={"Поставщик": "rec_supplier"}
    )

    sup_pr = supplier_profile_train.set_index("Поставщик")["avg_price_ratio"]

    # Для каждого клиента в test: фактические траты + рыночная стоимость
    client_test = tx_test_clean.groupby("Код клиента").agg(
        actual_spend=("amount", "sum"),
        market_spend=("amount_market", "sum"),
    ).reset_index()

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
        "n_clients_evaluated": len(df),
    }


# 6. ГЛАВНЫЙ ПАЙПЛАЙН СРАВНЕНИЯ

@dataclass
class ModelResults:
    name: str
    hit_rate_1: float
    ndcg_1: float
    ndcg_3: float
    saving_lift: dict


def evaluate_all_models(tx: pd.DataFrame,
                        clients: pd.DataFrame,
                        split_date: str = "2025-05-01"
                        ) -> tuple[pd.DataFrame, dict]:
    """
    Полный цикл: split → fit на train → predict на test → метрики.

    Возвращает:
      results_df  — таблица с метриками всех моделей
      details     — dict с дополнительной информацией (предикты и пр.)
    """
    from feature_engineering import build_all_features
    from target_builder import build_target

    print(f"\n[SPLIT] Делим по дате {split_date}…")
    tx_train, tx_test = temporal_split(tx, split_date)
    print(f"  train: {len(tx_train)} строк, "
          f"{tx_train['Код клиента'].nunique()} клиентов")
    print(f"  test:  {len(tx_test)} строк, "
          f"{tx_test['Код клиента'].nunique()} клиентов")

    # Train: фичи + таргет
    print("\n[TRAIN] Строим фичи и таргет…")
    train_features = build_all_features(tx_train, clients)
    train_target = build_target(
        compatibility_features=train_features["compatibility_features"],
        supplier_profile=train_features["supplier_profile"],
        interaction_features=train_features["interaction_features"],
        client_profile=train_features["client_profile"],
    )

    # Test: ground truth
    print("\n[TEST] Готовим ground truth…")
    from feature_engineering import clean_transactions
    tx_test_clean = clean_transactions(tx_test)
    gt = build_test_ground_truth(tx_test_clean)
    print(f"  Клиентов в test с известным winner: {len(gt)}")
    eligible_clients = gt["Код клиента"].tolist()

    # Оставляем только клиентов, которые есть в train (для fair comparison)
    train_clients_set = set(train_features["client_profile"]["Код клиента"])
    eligible_clients = [c for c in eligible_clients if c in train_clients_set]
    gt = gt[gt["Код клиента"].isin(eligible_clients)]
    print(f"  Из них есть в train: {len(eligible_clients)}")

    results = []
    details = {}

    # Модель A Content-Based
    print("\n[МОДЕЛЬ A] Content-Based + правила…")
    model_a = ContentBasedRecommender().fit(train_target)
    pred_a = model_a.predict_scores(eligible_clients)
    details["A_predictions"] = pred_a
    results.append(ModelResults(
        name="A. Content-Based + Rules",
        hit_rate_1=hit_rate_at_k(pred_a, gt, k=1),
        ndcg_1=ndcg_at_k(pred_a, gt, k=1),
        ndcg_3=ndcg_at_k(pred_a, gt, k=3),
        saving_lift=estimated_saving_lift(
            pred_a, gt, tx_test_clean, train_features["supplier_profile"]
        ),
    ))

    # Модель B Collaborative Filtering
    print("[МОДЕЛЬ B] Collaborative Filtering (ALS)…")
    model_b = CollaborativeFilteringRecommender(n_factors=4).fit(
        train_features["interaction_features"]
    )
    pred_b = model_b.predict_scores(eligible_clients)
    details["B_predictions"] = pred_b
    results.append(ModelResults(
        name="B. Collaborative Filtering",
        hit_rate_1=hit_rate_at_k(pred_b, gt, k=1),
        ndcg_1=ndcg_at_k(pred_b, gt, k=1),
        ndcg_3=ndcg_at_k(pred_b, gt, k=3),
        saving_lift=estimated_saving_lift(
            pred_b, gt, tx_test_clean, train_features["supplier_profile"]
        ),
    ))

    #Модель C Learning-to-Rank
    print("[МОДЕЛЬ C] Learning-to-Rank (LightGBM)…")
    model_c = LTRRecommender(n_estimators=200).fit(
        train_target,
        train_features["compatibility_features"],
        train_features["client_profile"],
        train_features["supplier_profile"],
    )
    pred_c = model_c.predict_scores(
        eligible_clients,
        train_features["compatibility_features"],
        train_features["client_profile"],
        train_features["supplier_profile"],
    )
    details["C_predictions"] = pred_c
    details["C_uses_lgb"] = model_c.use_lgb
    results.append(ModelResults(
        name="C. Learning-to-Rank",
        hit_rate_1=hit_rate_at_k(pred_c, gt, k=1),
        ndcg_1=ndcg_at_k(pred_c, gt, k=1),
        ndcg_3=ndcg_at_k(pred_c, gt, k=3),
        saving_lift=estimated_saving_lift(
            pred_c, gt, tx_test_clean, train_features["supplier_profile"]
        ),
    ))

    #Сборка результата 
    results_df = pd.DataFrame([
        {
            "Модель": r.name,
            "Hit@1": round(r.hit_rate_1, 3),
            "NDCG@1": round(r.ndcg_1, 3),
            "NDCG@3": round(r.ndcg_3, 3),
            "Avg Saving Lift, %": round(r.saving_lift["avg_lift_pct"] * 100, 2),
            "Total Lift, руб": round(r.saving_lift["total_lift_rub"], 0),
            "% клиентов улучш.": round(r.saving_lift["pct_clients_better"] * 100, 1),
        }
        for r in results
    ])

    details["ground_truth"] = gt
    details["tx_test_clean"] = tx_test_clean
    details["train_features"] = train_features

    return results_df, details


if __name__ == "__main__":
    pass
