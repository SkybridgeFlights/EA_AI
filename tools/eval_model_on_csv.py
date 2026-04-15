# tools/eval_model_on_csv.py

import os
import pandas as pd
from sklearn.metrics import classification_report, confusion_matrix

from app.ml.model import load_active_model, _load_prices_for_training
from app.ml.features import make_features, make_labels


def main():

    symbol = os.environ.get("SYMBOL", "XAUUSDr")

    print("Loading model...")
    model = load_active_model()

    if model is None:
        print("ERROR: no active model")
        return

    print("Loading prices...")
    dfp = _load_prices_for_training(symbol)

    print("Building features...")
    X = make_features(dfp, pd.DataFrame())

    print("Building labels...")
    y = make_labels(dfp, horizon=6)

    both = pd.concat([X, y.rename("y")], axis=1).dropna()

    X = both.drop(columns=["y"])
    y = both["y"]

    print("Predicting...")
    preds = model.predict(X)

    print("\nCONFUSION MATRIX:")
    print(confusion_matrix(y, preds))

    print("\nCLASSIFICATION REPORT:")
    print(classification_report(y, preds, digits=4))


if __name__ == "__main__":
    main()
