"""Iteration logging for tracking optimization progress."""

from __future__ import annotations
import csv
import json
import os
from datetime import datetime
from models.strategy import Strategy
from models.backtest_result import BacktestResult
from models.critique import Critique


class IterationLog:
    def __init__(self, output_dir: str = "output/logs"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.csv_path = os.path.join(output_dir, "iterations.csv")
        self._init_csv()

    def _init_csv(self):
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "iteration", "strategy_id", "version", "sharpe_ratio",
                    "total_return", "max_drawdown", "win_rate", "profit_factor",
                    "total_trades", "grade", "timestamp",
                ])

    def record(self, iteration: int, strategy: Strategy, result: BacktestResult, critique: Critique):
        with open(self.csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                iteration, strategy.id, strategy.version,
                f"{result.sharpe_ratio:.4f}", f"{result.total_return:.4f}",
                f"{result.max_drawdown:.4f}", f"{result.win_rate:.4f}",
                f"{result.profit_factor:.4f}", result.total_trades,
                critique.overall_grade, datetime.now().isoformat(),
            ])

        # Detailed JSON log
        detail = {
            "iteration": iteration,
            "strategy": strategy.to_dict(),
            "result": result.summary(),
            "critique": {
                "grade": critique.overall_grade,
                "weaknesses": critique.weaknesses,
                "suggestions": critique.suggestions,
                "scores": {k: f"{v:.4f}" if isinstance(v, float) else v for k, v in critique.scores.items()},
            },
        }
        json_path = os.path.join(self.output_dir, f"iter_{iteration:03d}.json")
        with open(json_path, "w") as f:
            json.dump(detail, f, indent=2, default=str)
