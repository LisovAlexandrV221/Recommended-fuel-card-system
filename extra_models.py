"""
Использование:
    from extra_models import (
        CatBoostLikeRanker, PairwiseRanker, StackingEnsemble,
        evaluate_extra_models,
    )
    results = evaluate_extra_models(tx, clients, split_date='2025-05-15')
"""

from __future__ import annotations

import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ОБЩАЯ УТИЛИТА — построение признаков (общая для D и E)

def build_feature_matrix(compatibility_features: pd.DataFrame,
                         client_profile: pd.DataFrame,
                         supplier_profile: pd.DataFrame
                         ) -> tuple[pd.DataFrame, list, list]:
    """
    Возвращает (df, numeric_cols, categorical_cols).
    Объединяет признаки клиента, поставщика и пары в плоскую таблицу.
    """
    df = compatibility_features.copy()

    # Числовые признаки клиента
    cp_num = ["total_volume", "total_spend", "tx_count",
              "avg_ticket", "avg_price_ratio", "saving_pct",
              "unique_regions", "region_hhi", "fuel_entropy",
              "main_supplier_share", "price_sensitivity",
              "share_night", "share_weekend", "own_station_share",
              "contract_age_months", "actual_monthly_volume"]
    cp_num = [c for c in cp_num if c in client_profile.columns]

    # Категориальные признаки клиента
    cp_cat = ["Тип клиента", "Офис обслуживания",
              "Статус договора", "volume_segment", "top_region"]
    cp_cat = [c for c in cp_cat if c in client_profile.columns]

    df = df.merge(
        client_profile[["Код клиента"] + cp_num + cp_cat],
        on="Код клиента", how="left"
    )

    # Числовые признаки поставщика
    sp_num = ["total_volume", "unique_clients", "unique_stations",
              "unique_regions", "own_station_share",
              "avg_price_ratio", "std_price_ratio",
              "price_stability", "avg_saving_per_liter"]
    sp_num = [c for c in sp_num if c in supplier_profile.columns]

    sp = supplier_profile[["Поставщик"] + sp_num].rename(
        columns={c: f"sup_{c}" for c in sp_num}
    )
    df = df.merge(sp, on="Поставщик", how="left")

    # Поставщик сам по себе — категориальная фича
    cat_cols = cp_cat + ["Поставщик"]
    num_cols = [c for c in df.columns
                if c not in ["Код клиента"] + cat_cols
                and df[c].dtype in [np.float64, np.int64,
                                    np.float32, np.int32, "Float64", "Int64"]]

    return df, num_cols, cat_cols


# МОДЕЛЬ D — CatBoost-like Ranker

class CatBoostLikeRanker:
    """
    Если доступен catboost — использует CatBoostRanker (objective=YetiRank).
    Иначе — HistGradientBoostingRegressor с обработкой категорий через
    OrdinalEncoder + явный hint категориальных фичей.
    """

    def __init__(self, n_estimators: int = 300, learning_rate: float = 0.05,
                 max_depth: int = 6, cf_models: dict = None):
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.cf_models = cf_models
        self.model = None
        self.encoders = {}
        self.feature_cols = None
        self.cat_cols = None
        self.use_catboost = False

    def _encode_categoricals(self, df: pd.DataFrame, fit: bool = True) -> pd.DataFrame:
        df = df.copy()
        for c in self.cat_cols:
            if c not in df.columns:
                continue
            df[c] = df[c].astype(str).fillna("__missing__")
            if fit:
                cats = df[c].unique().tolist()
                self.encoders[c] = {v: i for i, v in enumerate(cats)}
            mapping = self.encoders.get(c, {})
            df[c] = df[c].map(mapping).fillna(-1).astype(int)
        return df

    def fit(self, target_df: pd.DataFrame,
            compatibility_features: pd.DataFrame,
            client_profile: pd.DataFrame,
            supplier_profile: pd.DataFrame):

        df, num_cols, cat_cols = build_feature_matrix(
            compatibility_features, client_profile, supplier_profile
        )

        if self.cf_models:
            client_ids = df["Код клиента"].unique().tolist()
            for name, model in self.cf_models.items():
                preds = model.predict_scores(client_ids)
                preds = preds.rename(columns={"score": f"cf_score_{name}"})
                df = df.merge(preds, on=["Код клиента", "Поставщик"], how="left")
                num_cols.append(f"cf_score_{name}")
                df[f"cf_score_{name}"] = df[f"cf_score_{name}"].fillna(0)

        self.cat_cols = cat_cols
        df = df.merge(
            target_df[["Код клиента", "Поставщик", "composite_score"]],
            on=["Код клиента", "Поставщик"], how="left"
        ).dropna(subset=["composite_score"])
        df = df.sort_values("Код клиента").reset_index(drop=True)

        df_enc = self._encode_categoricals(df, fit=True)
        self.feature_cols = num_cols + cat_cols
        X = df_enc[self.feature_cols].fillna(0)
        y = df_enc["composite_score"]
        groups = df_enc.groupby("Код клиента").size().values

        try:
            from catboost import CatBoostRanker, Pool
            cat_indices = [self.feature_cols.index(c) for c in cat_cols
                           if c in self.feature_cols]
            group_id = df_enc["Код клиента"].astype("category").cat.codes.values
            order = np.argsort(group_id)
            X_sorted = X.iloc[order]
            y_sorted = y.iloc[order]
            gid_sorted = group_id[order]

            pool = Pool(X_sorted, y_sorted, group_id=gid_sorted,
                        cat_features=cat_indices)
            self.model = CatBoostRanker(
                iterations=self.n_estimators,
                learning_rate=self.learning_rate,
                depth=self.max_depth,
                loss_function="YetiRank",
                verbose=0,
            )
            self.model.fit(pool)
            self.use_catboost = True
        except (ImportError, Exception):
            from sklearn.ensemble import HistGradientBoostingRegressor
            cat_mask = [c in cat_cols for c in self.feature_cols]
            self.model = HistGradientBoostingRegressor(
                max_iter=self.n_estimators,
                learning_rate=self.learning_rate,
                max_depth=self.max_depth,
                categorical_features=cat_mask,
                random_state=42,
            )
            self.model.fit(X, y)
            self.use_catboost = False

        return self

    def predict_scores(self, client_ids: list,
                       compatibility_features: pd.DataFrame,
                       client_profile: pd.DataFrame,
                       supplier_profile: pd.DataFrame) -> pd.DataFrame:
        df, _, _ = build_feature_matrix(
            compatibility_features, client_profile, supplier_profile
        )
        df = df[df["Код клиента"].isin(client_ids)].copy()

        if self.cf_models:
            for name, model in self.cf_models.items():
                preds = model.predict_scores(client_ids)
                preds = preds.rename(columns={"score": f"cf_score_{name}"})
                df = df.merge(preds, on=["Код клиента", "Поставщик"], how="left")
                df[f"cf_score_{name}"] = df[f"cf_score_{name}"].fillna(0)

        df_enc = self._encode_categoricals(df, fit=False)
        X = df_enc[self.feature_cols].fillna(0)
        df["score"] = self.model.predict(X)
        return df[["Код клиента", "Поставщик", "score"]]


# МОДЕЛЬ E — Pairwise Ranker (XGBoost-like)

class PairwiseRanker:
    """
    Pairwise LTR. Для каждой группы (клиента) генерирует пары (i, j), где
    composite_score[i] > composite_score[j], и обучает регрессор предсказывать
    разницу признаков → положительный сигнал для ранжирования.

    Если доступен xgboost — использует XGBRanker(rank:pairwise).
    Иначе — самописная pairwise схема через GradientBoosting на разностях
    признаков (RankNet-подобный подход).
    """

    def __init__(self, n_estimators: int = 200, learning_rate: float = 0.05,
                 max_pairs_per_group: int = 6):
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.max_pairs = max_pairs_per_group
        self.model = None
        self.feature_cols = None
        self.encoders = {}
        self.cat_cols = None
        self.use_xgb = False

    def _encode(self, df: pd.DataFrame, fit: bool = True) -> pd.DataFrame:
        df = df.copy()
        for c in self.cat_cols:
            if c not in df.columns:
                continue
            df[c] = df[c].astype(str).fillna("__missing__")
            if fit:
                cats = df[c].unique().tolist()
                self.encoders[c] = {v: i for i, v in enumerate(cats)}
            df[c] = df[c].map(self.encoders.get(c, {})).fillna(-1).astype(int)
        return df

    def _make_pairs(self, df_enc: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """
        Для каждого клиента делаем все пары (i, j), где score[i] != score[j].
        Возвращает (X_diff, y_label), где:
          X_diff = features[i] - features[j]
          y_label = +1 если score[i] > score[j], -1 иначе
        """
        X_diffs, y_signs = [], []
        for client, g in df_enc.groupby("Код клиента"):
            g = g.reset_index(drop=True)
            n = len(g)
            if n < 2:
                continue
            # Все возможные пары
            scores = g["composite_score"].values
            X = g[self.feature_cols].fillna(0).values
            pairs = []
            for i in range(n):
                for j in range(n):
                    if i == j:
                        continue
                    if scores[i] > scores[j] + 1e-6:
                        pairs.append((i, j))
            # Ограничиваем для скорости
            if len(pairs) > self.max_pairs:
                idx = np.random.choice(len(pairs), self.max_pairs, replace=False)
                pairs = [pairs[k] for k in idx]
            for i, j in pairs:
                # Подаём как (X[i] - X[j], +1) и (X[j] - X[i], -1) для симметрии
                X_diffs.append(X[i] - X[j])
                y_signs.append(1.0)
                X_diffs.append(X[j] - X[i])
                y_signs.append(-1.0)
        return np.array(X_diffs), np.array(y_signs)

    def fit(self, target_df: pd.DataFrame,
            compatibility_features: pd.DataFrame,
            client_profile: pd.DataFrame,
            supplier_profile: pd.DataFrame):

        df, num_cols, cat_cols = build_feature_matrix(
            compatibility_features, client_profile, supplier_profile
        )
        self.cat_cols = cat_cols
        self.feature_cols = num_cols + cat_cols

        df = df.merge(
            target_df[["Код клиента", "Поставщик", "composite_score"]],
            on=["Код клиента", "Поставщик"], how="left"
        ).dropna(subset=["composite_score"])
        df_enc = self._encode(df, fit=True)

        try:
            import xgboost as xgb
            df_sorted = df_enc.sort_values("Код клиента").reset_index(drop=True)
            X = df_sorted[self.feature_cols].fillna(0)
            y = df_sorted["composite_score"]
            # XGBoost ранкеру нужно сгруппированное расположение
            groups = df_sorted.groupby("Код клиента").size().values
            self.model = xgb.XGBRanker(
                objective="rank:pairwise",
                n_estimators=self.n_estimators,
                learning_rate=self.learning_rate,
                max_depth=6,
                verbosity=0,
            )
            self.model.fit(X, y, group=groups)
            self.use_xgb = True
        except (ImportError, Exception):
            # Самописная pairwise схема
            from sklearn.ensemble import GradientBoostingRegressor
            X_diff, y_sign = self._make_pairs(df_enc)
            self.model = GradientBoostingRegressor(
                n_estimators=self.n_estimators,
                learning_rate=self.learning_rate,
                max_depth=4,
                random_state=42,
            )
            self.model.fit(X_diff, y_sign)
            self.use_xgb = False

        return self

    def predict_scores(self, client_ids: list,
                       compatibility_features: pd.DataFrame,
                       client_profile: pd.DataFrame,
                       supplier_profile: pd.DataFrame) -> pd.DataFrame:
        df, _, _ = build_feature_matrix(
            compatibility_features, client_profile, supplier_profile
        )
        df = df[df["Код клиента"].isin(client_ids)].copy()
        df_enc = self._encode(df, fit=False)
        X = df_enc[self.feature_cols].fillna(0)

        if self.use_xgb:
            df["score"] = self.model.predict(X)
        else:
            df["score"] = self.model.predict(X.values)

        return df[["Код клиента", "Поставщик", "score"]]


# МОДЕЛЬ F — Stacking Ensemble

class StackingEnsemble:
    """
    Мета-модель: линейная комбинация предсказаний из base-моделей.

    Стэкинг через out-of-fold предсказания базовых моделей на train
    и обучение Ridge на этих OOF-предсказаниях.

    На предсказании просто берём предсказания base-моделей на test
    и пропускаем через мета-модель.
    """

    def __init__(self, base_models: dict, meta_alpha: float = 1.0):
        """
        base_models: dict[str, model] — обученные базовые модели.
                     Каждая должна иметь predict_scores().
        """
        self.base_models = base_models
        self.meta_alpha = meta_alpha
        self.meta_model = None

    def fit(self, target_df: pd.DataFrame,
            compatibility_features: pd.DataFrame,
            client_profile: pd.DataFrame,
            supplier_profile: pd.DataFrame):
        """
        Обучает мета-модель на полном train (без CV для простоты).
        В production стоит делать out-of-fold через KFold по клиентам.
        """
        # Собираем признаки = предсказания base-моделей на train
        client_ids = client_profile["Код клиента"].unique().tolist()

        base_preds = {}
        for name, model in self.base_models.items():
            if name == "A":  # Content-Based — берёт только target_df
                p = model.predict_scores(client_ids)
            else:
                p = model.predict_scores(
                    client_ids, compatibility_features,
                    client_profile, supplier_profile
                )
            p = p.rename(columns={"score": f"score_{name}"})
            base_preds[name] = p

        # Объединяем
        meta_df = base_preds[list(base_preds.keys())[0]]
        for name in list(base_preds.keys())[1:]:
            meta_df = meta_df.merge(
                base_preds[name], on=["Код клиента", "Поставщик"]
            )

        # Целевая — composite_score
        meta_df = meta_df.merge(
            target_df[["Код клиента", "Поставщик", "composite_score"]],
            on=["Код клиента", "Поставщик"]
        )

        feat_cols = [f"score_{n}" for n in self.base_models.keys()]
        X = meta_df[feat_cols].fillna(0)
        y = meta_df["composite_score"]

        from sklearn.linear_model import Ridge
        self.meta_model = Ridge(alpha=self.meta_alpha)
        self.meta_model.fit(X, y)

        # Сохраняем веса для отчёта
        self.weights_ = dict(zip(feat_cols, self.meta_model.coef_))
        self.intercept_ = float(self.meta_model.intercept_)

        return self

    def predict_scores(self, client_ids: list,
                       compatibility_features: pd.DataFrame,
                       client_profile: pd.DataFrame,
                       supplier_profile: pd.DataFrame) -> pd.DataFrame:
        base_preds = {}
        for name, model in self.base_models.items():
            if name == "A":
                p = model.predict_scores(client_ids)
            else:
                p = model.predict_scores(
                    client_ids, compatibility_features,
                    client_profile, supplier_profile
                )
            p = p.rename(columns={"score": f"score_{name}"})
            base_preds[name] = p

        meta_df = base_preds[list(base_preds.keys())[0]]
        for name in list(base_preds.keys())[1:]:
            meta_df = meta_df.merge(
                base_preds[name], on=["Код клиента", "Поставщик"]
            )

        feat_cols = [f"score_{n}" for n in self.base_models.keys()]
        X = meta_df[feat_cols].fillna(0)
        meta_df["score"] = self.meta_model.predict(X)

        return meta_df[["Код клиента", "Поставщик", "score"]]


# Объединяющая функция: оценка всех 6 моделей на одной test-выборке

def evaluate_extra_models(tx: pd.DataFrame, clients: pd.DataFrame,
                          split_date: str = "2025-05-15") -> tuple[pd.DataFrame, dict]:
    """
    Оценивает 6 моделей вместе:
      A. Content-Based (baseline)
      B. Collaborative Filtering (ALS)
      C. Learning-to-Rank (GBR)
      D. CatBoost-like Ranker
      E. Pairwise Ranker
      F. Stacking Ensemble (A + D + E)
    """
    from feature_engineering import build_all_features, clean_transactions
    from target_builder import build_target
    from models_and_validation import (
        temporal_split,
        build_test_ground_truth,
        ContentBasedRecommender,
        CollaborativeFilteringRecommender,
        LTRRecommender,
        hit_rate_at_k,
        ndcg_at_k,
        estimated_saving_lift,
        ModelResults,
    )

    print(f"\n[SPLIT] split_date={split_date}")
    tx_train, tx_test = temporal_split(tx, split_date)
    print(f"  train: {len(tx_train)} строк, test: {len(tx_test)} строк")

    print("\n[TRAIN] фичи и таргет…")
    tf = build_all_features(tx_train, clients)
    tt = build_target(
        compatibility_features=tf["compatibility_features"],
        supplier_profile=tf["supplier_profile"],
        interaction_features=tf["interaction_features"],
        client_profile=tf["client_profile"],
    )

    print("[TEST] ground truth…")
    tx_test_clean = clean_transactions(tx_test)
    gt = build_test_ground_truth(tx_test_clean)
    train_clients = set(tf["client_profile"]["Код клиента"])
    gt = gt[gt["Код клиента"].isin(train_clients)]
    eligible = gt["Код клиента"].tolist()
    print(f"  Клиентов для оценки: {len(eligible)}")

    from collaborative_experiments import (
        MostPopRecommender, 
        ALSRecommender, EASERecommender
    )

    print("\n[МОДЕЛИ] обучаем базовые CF-модели для гибрида…")
    cf_models = {
        "MostPop": MostPopRecommender().fit(tf["interaction_features"]),
        "ALS": ALSRecommender(n_factors=12).fit(tf["interaction_features"]),
        "EASE": EASERecommender(reg=250.0).fit(tf["interaction_features"]),
    }

    print("\n[МОДЕЛИ] обучаем основные…")
    print("  A. Content-Based")
    model_a = ContentBasedRecommender().fit(tt)

    print("  B. Collaborative Filtering")
    model_b = CollaborativeFilteringRecommender(n_factors=4).fit(
        tf["interaction_features"]
    )

    print("  C. LTR (GBR)")
    model_c = LTRRecommender(n_estimators=200).fit(
        tt, tf["compatibility_features"],
        tf["client_profile"], tf["supplier_profile"],
    )

    print("  D. CatBoost-like (Hybrid cascade)")
    model_d = CatBoostLikeRanker(n_estimators=300, learning_rate=0.05, cf_models=cf_models).fit(
        tt, tf["compatibility_features"],
        tf["client_profile"], tf["supplier_profile"],
    )

    print("  E. Pairwise Ranker")
    model_e = PairwiseRanker(n_estimators=200).fit(
        tt, tf["compatibility_features"],
        tf["client_profile"], tf["supplier_profile"],
    )

    print("  F. Stacking Ensemble (A + D + E)")
    model_f = StackingEnsemble(
        base_models={"A": model_a, "D": model_d, "E": model_e}
    ).fit(
        tt, tf["compatibility_features"],
        tf["client_profile"], tf["supplier_profile"],
    )

    #Предсказания и метрики
    def _eval(name: str, predict_fn) -> ModelResults:
        pred = predict_fn(eligible)
        return ModelResults(
            name=name,
            hit_rate_1=hit_rate_at_k(pred, gt, k=1),
            ndcg_1=ndcg_at_k(pred, gt, k=1),
            ndcg_3=ndcg_at_k(pred, gt, k=3),
            saving_lift=estimated_saving_lift(
                pred, gt, tx_test_clean, tf["supplier_profile"]
            ),
        )

    results = []
    details = {}

    print("\n[EVAL] предсказываем и считаем метрики…")

    results.append(_eval("A. Content-Based", lambda ids: model_a.predict_scores(ids)))
    details["A"] = model_a

    results.append(_eval("B. Collaborative Filtering",
                         lambda ids: model_b.predict_scores(ids)))
    details["B"] = model_b

    cs = lambda m: lambda ids: m.predict_scores(
        ids, tf["compatibility_features"],
        tf["client_profile"], tf["supplier_profile"]
    )
    results.append(_eval("C. LTR (GBR)", cs(model_c)))
    details["C"] = model_c

    results.append(_eval("D. CatBoost-like", cs(model_d)))
    details["D"] = model_d
    details["D_uses_catboost"] = model_d.use_catboost

    results.append(_eval("E. Pairwise Ranker", cs(model_e)))
    details["E"] = model_e
    details["E_uses_xgb"] = model_e.use_xgb

    results.append(_eval("F. Stacking (A+D+E)", cs(model_f)))
    details["F"] = model_f
    details["F_weights"] = model_f.weights_
    details["F_intercept"] = model_f.intercept_

    rdf = pd.DataFrame([
        {
            "Модель": r.name,
            "Hit@1": round(r.hit_rate_1, 3),
            "NDCG@1": round(r.ndcg_1, 3),
            "NDCG@3": round(r.ndcg_3, 3),
            "Saving Lift, %": round(r.saving_lift["avg_lift_pct"] * 100, 2),
            "Total, руб": round(r.saving_lift["total_lift_rub"], 0),
            "% улучш.": round(r.saving_lift["pct_clients_better"] * 100, 1),
        }
        for r in results
    ])

    return rdf, details


if __name__ == "__main__":
    pass
