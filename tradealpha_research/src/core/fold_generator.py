from datetime import datetime
from dateutil.relativedelta import relativedelta
from dataclasses import dataclass
from typing import List


@dataclass
class Fold:
    fold_id: int
    train_from: datetime
    train_to: datetime
    test_from: datetime
    test_to: datetime


class ExpandingWFOGenerator:
    """
    Expanding Window Walk Forward Generator
    Train: grows from initial start
    Test: fixed length
    Step: equal to test window
    """

    def __init__(
        self,
        start_date: str,
        end_date: str,
        initial_train_months: int = 24,
        test_months: int = 6,
        step_months: int = 6,
    ):
        self.start_date = datetime.strptime(start_date, "%Y.%m.%d")
        self.end_date = datetime.strptime(end_date, "%Y.%m.%d")
        self.initial_train_months = initial_train_months
        self.test_months = test_months
        self.step_months = step_months

    def generate(self) -> List[Fold]:
        folds = []

        train_start = self.start_date
        train_end = train_start + relativedelta(months=self.initial_train_months)

        fold_id = 1

        while True:
            test_start = train_end
            test_end = test_start + relativedelta(months=self.test_months)

            if test_end > self.end_date:
                break

            fold = Fold(
                fold_id=fold_id,
                train_from=train_start,
                train_to=train_end,
                test_from=test_start,
                test_to=test_end,
            )

            folds.append(fold)

            # Expanding: train always starts from initial start
            train_end = train_end + relativedelta(months=self.step_months)

            fold_id += 1

        return folds


def format_mt5_date(dt: datetime) -> str:
    return dt.strftime("%Y.%m.%d")


if __name__ == "__main__":
    generator = ExpandingWFOGenerator(
        start_date="2021.01.01",
        end_date="2025.12.31",
        initial_train_months=24,
        test_months=6,
        step_months=6,
    )

    folds = generator.generate()

    print("\nGenerated WFO Folds:\n")
    for f in folds:
        print(
            f"Fold {f.fold_id}: "
            f"Train [{format_mt5_date(f.train_from)} → {format_mt5_date(f.train_to)}] | "
            f"Test [{format_mt5_date(f.test_from)} → {format_mt5_date(f.test_to)}]"
        )