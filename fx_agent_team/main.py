"""FX AgentTeam - Autonomous Quantitative Trading Strategy Optimizer.

Usage:
    python main.py                          # Default: EURUSD, 30 iterations
    python main.py --pair GBPUSD=X          # Different pair
    python main.py --iterations 50          # More iterations
    python main.py --target-sharpe 2.0      # Higher target
"""

import argparse
import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from data.provider import DataProvider
from agents.orchestrator_agent import OrchestratorAgent


def setup_logging():
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL),
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(os.path.join("output", "logs", "run.log"), mode="w"),
        ],
    )


def main():
    parser = argparse.ArgumentParser(description="FX AgentTeam - Autonomous Strategy Optimizer")
    parser.add_argument("--pair", default=config.DEFAULT_PAIR, help="FX pair (e.g., EURUSD=X)")
    parser.add_argument("--iterations", type=int, default=config.MAX_ITERATIONS, help="Max optimization iterations")
    parser.add_argument("--target-sharpe", type=float, default=config.TARGET_SHARPE, help="Target Sharpe ratio")
    parser.add_argument("--start", default=config.DATA_START, help="Data start date")
    parser.add_argument("--end", default=config.DATA_END, help="Data end date")
    args = parser.parse_args()

    os.makedirs(os.path.join("output", "logs"), exist_ok=True)
    setup_logging()
    logger = logging.getLogger("main")

    # Fetch data
    logger.info(f"Fetching {args.pair} data from {args.start} to {args.end}...")
    provider = DataProvider()
    data = provider.fetch(args.pair, args.start, args.end)
    logger.info(f"Loaded {len(data)} bars")

    # Run orchestrator
    orchestrator = OrchestratorAgent()
    best_strategy, best_result = orchestrator.run(
        data=data,
        pair=args.pair,
        max_iterations=args.iterations,
        target_sharpe=args.target_sharpe,
    )

    # Print final summary
    print("\n" + "=" * 60)
    print("FINAL RESULT")
    print("=" * 60)
    for k, v in best_result.summary().items():
        print(f"  {k:20s}: {v}")
    print(f"\nStrategy saved to: output/strategies/best_strategy.json")
    print(f"All logs at: output/logs/")


if __name__ == "__main__":
    main()
