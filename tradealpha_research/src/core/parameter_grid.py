from dataclasses import dataclass
from itertools import product
from typing import List
import json


@dataclass
class ParameterSet:
    ma_fast: int
    ma_slow: int
    atr_sl_mult: float
    rr: float


class GridGenerator:

    def __init__(self):
        self.ma_fast_values = [20, 29, 40, 50, 60]
        self.ma_slow_values = [80, 100, 120, 150, 200]
        self.atr_sl_values = [1.4, 1.6, 1.8, 2.0, 2.2]
        self.rr_values = [2.0, 2.4, 2.8, 3.0]

    def generate(self) -> List[ParameterSet]:

        combinations = product(
            self.ma_fast_values,
            self.ma_slow_values,
            self.atr_sl_values,
            self.rr_values,
        )

        params = []

        for ma_fast, ma_slow, atr_sl, rr in combinations:

            # شرط منطقي مهم:
            # MA slow يجب أن يكون أكبر من MA fast
            if ma_slow <= ma_fast:
                continue

            params.append(
                ParameterSet(
                    ma_fast=ma_fast,
                    ma_slow=ma_slow,
                    atr_sl_mult=atr_sl,
                    rr=rr,
                )
            )

        return params


def save_grid_to_json(params: List[ParameterSet], path: str):

    data = [
        {
            "ma_fast": p.ma_fast,
            "ma_slow": p.ma_slow,
            "atr_sl_mult": p.atr_sl_mult,
            "rr": p.rr,
        }
        for p in params
    ]

    with open(path, "w") as f:
        json.dump(data, f, indent=4)


if __name__ == "__main__":

    generator = GridGenerator()
    params = generator.generate()

    print(f"\nGenerated parameter sets: {len(params)}\n")

    save_grid_to_json(params, "parameter_grid.json")

    print("Saved to parameter_grid.json")