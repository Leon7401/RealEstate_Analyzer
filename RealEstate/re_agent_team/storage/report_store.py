"""レポート永続化"""
import json
from datetime import datetime
from pathlib import Path

from config.settings import REPORT_DIR


class ReportStore:
    """判定レポートの保存と読込"""

    def __init__(self):
        self.report_dir = REPORT_DIR
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def save(self, property, valuation, simulation, judgment) -> Path:
        """全結果を1つのJSONレポートとして保存"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{property.id}_{judgment.grade}.json"
        filepath = self.report_dir / filename

        report = {
            "generated_at": datetime.now().isoformat(),
            "property": property.to_dict(),
            "valuation": valuation.to_dict(),
            "simulation": simulation.to_dict(),
            "judgment": judgment.to_dict(),
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        return filepath

    def load(self, filepath: str) -> dict:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_reports(self) -> list:
        return sorted(self.report_dir.glob("*.json"), reverse=True)
