import numpy as np
from sklearn.ensemble import RandomForestClassifier


class FloodNowcastModel:
    """
    Lightweight Random Forest classifier for <2s urban flash-flood nowcasting
    along the Rachenahalli – Manyata Tech Park corridor.
    Features: [water_depth_m, rate_of_rise_cm_min, rainfall_mm_hr, slope_pct]
    """

    def __init__(self):
        self.model = RandomForestClassifier(
            n_estimators=50,
            max_depth=5,
            random_state=42,
        )
        self._train_on_corridor_scenarios()

    def _train_on_corridor_scenarios(self):
        # Training vectors grounded in BBMP/WELL Labs 30–90 mm/hr benchmarks
        # Columns: [depth_m, rise_cm_min, rain_mm_hr, slope_pct]
        X_train = np.array([
            # Normal / Dry / Light shower (Class 0: Safe)
            [0.15, 0.2,  5.0,  1.2],
            [0.25, 0.5,  12.0, 1.2],
            [0.40, 1.5,  25.0, 1.0],
            [0.55, 2.5,  32.0, 0.9],
            [0.70, 3.0,  38.0, 0.8],
            [0.85, 4.0,  42.0, 0.8],
            [0.95, 5.5,  48.0, 0.7],
            # High surge / Cloudburst inundation risk (Class 1: Flood Risk)
            [1.05, 8.5,  55.0, 0.6],
            [1.15, 10.0, 62.0, 0.5],
            [1.25, 12.0, 70.0, 0.5],
            [1.35, 13.5, 78.0, 0.4],
            [1.45, 14.5, 85.0, 0.4],
            [1.55, 15.0, 90.0, 0.4],
            [1.70, 18.0, 95.0, 0.3],
        ])
        y_train = np.array([0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1])
        self.model.fit(X_train, y_train)

    def predict_risk(
        self,
        depth_m: float,
        rise_cm_min: float,
        rain_mm_hr: float = 45.0,
        slope_pct: float = 0.6,
    ) -> dict:
        features = np.array([[depth_m, rise_cm_min, rain_mm_hr, slope_pct]])
        prob = float(self.model.predict_proba(features)[0][1])
        risk_pct = round(prob * 100, 1)

        if risk_pct >= 75.0:
            label = "HIGH INUNDATION RISK"
        elif risk_pct >= 40.0:
            label = "MODERATE SURGE RISK"
        else:
            label = "LOW RISK"

        return {
            "inundation_probability_pct": risk_pct,
            "risk_classification": label,
            "model_type": "RandomForestClassifier (n=50)",
        }


nowcast_engine = FloodNowcastModel()