import os
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from nse_features import FEATURES

CSV = "data/nse_features.csv"
MODEL = "data/model.joblib"

if not os.path.exists(CSV):
    raise SystemExit("No data/nse_features.csv yet. Collect market data first.")
df = pd.read_csv(CSV).dropna()
required = FEATURES + ["target"]
missing = [c for c in required if c not in df.columns]
if missing:
    raise SystemExit("Missing columns: " + ",".join(missing))
if len(df) < 200:
    raise SystemExit("Need more labeled rows; only " + str(len(df)) + " available.")

X = df[FEATURES]
y = df["target"].astype(int)
Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
model = RandomForestClassifier(n_estimators=300, min_samples_leaf=3,
                               random_state=42, class_weight="balanced")
model.fit(Xtr, ytr)
print("validation accuracy:", round(model.score(Xte, yte), 4))
os.makedirs("data", exist_ok=True)
joblib.dump(model, MODEL)
print("saved", MODEL)
