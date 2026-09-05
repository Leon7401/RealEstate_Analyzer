"""Strategy persistence."""

from __future__ import annotations
import json
import os
from models.strategy import Strategy


class StrategyStore:
    def __init__(self, output_dir: str = "output/strategies"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def save(self, strategy: Strategy, tag: str | None = None):
        filename = f"{strategy.id}_v{strategy.version}"
        if tag:
            filename += f"_{tag}"
        filepath = os.path.join(self.output_dir, f"{filename}.json")
        with open(filepath, "w") as f:
            f.write(strategy.to_json())

    def save_best(self, strategy: Strategy):
        filepath = os.path.join(self.output_dir, "best_strategy.json")
        with open(filepath, "w") as f:
            f.write(strategy.to_json())

    def load(self, filepath: str) -> Strategy:
        with open(filepath) as f:
            return Strategy.from_json(f.read())
