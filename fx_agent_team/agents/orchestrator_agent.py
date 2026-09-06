"""Orchestrator agent - coordinates the generate-backtest-critique-optimize loop.

Uses a population-based approach: maintains a pool of top strategies and
mutates all of them each iteration, keeping the best performers.
"""

from __future__ import annotations
import logging
import pandas as pd
from agents.base_agent import BaseAgent
from agents.strategy_agent import StrategyAgent
from agents.backtest_agent import BacktestAgent
from agents.critic_agent import CriticAgent
from agents.optimizer_agent import OptimizerAgent
from agents.risk_manager_agent import RiskManagerAgent
from models.strategy import Strategy
from models.backtest_result import BacktestResult
from models.critique import Critique
from storage.iteration_log import IterationLog
from storage.strategy_store import StrategyStore
import config


class OrchestratorAgent(BaseAgent):
    def __init__(self):
        super().__init__("OrchestratorAgent")
        self.strategy_agent = StrategyAgent()
        self.backtest_agent = BacktestAgent()
        self.critic_agent = CriticAgent()
        self.optimizer_agent = OptimizerAgent()
        self.risk_manager = RiskManagerAgent()
        self.iteration_log = IterationLog()
        self.strategy_store = StrategyStore()

    def _evaluate(self, strategy: Strategy, data: pd.DataFrame) -> tuple[BacktestResult, Critique]:
        strategy = self.risk_manager.run(strategy=strategy)
        result = self.backtest_agent.run(strategy=strategy, data=data)
        critique = self.critic_agent.run(result=result)
        return result, critique

    def run(
        self,
        data: pd.DataFrame,
        pair: str = config.DEFAULT_PAIR,
        max_iterations: int = config.MAX_ITERATIONS,
        target_sharpe: float = config.TARGET_SHARPE,
        pool_size: int = 5,
        initial_population: int = 15,
        mutations_per_strategy: int = 3,
        **kwargs,
    ) -> tuple[Strategy, BacktestResult]:
        self.logger.info("=" * 60)
        self.logger.info("FX AgentTeam - Population-Based Strategy Optimization")
        self.logger.info(f"Pair: {pair} | Max iters: {max_iterations} | Target Sharpe: {target_sharpe}")
        self.logger.info(f"Pool: {pool_size} | Initial pop: {initial_population} | Mutations: {mutations_per_strategy}")
        self.logger.info("=" * 60)

        # Phase 1: Generate initial population
        self.logger.info(f"\n--- Phase 1: Generating {initial_population} initial strategies ---")
        candidates: list[tuple[Strategy, BacktestResult, Critique]] = []

        for i in range(initial_population):
            strategy = self.strategy_agent.run(pair=pair)
            result, critique = self._evaluate(strategy, data)
            candidates.append((strategy, result, critique))
            self.iteration_log.record(i, strategy, result, critique)
            self.strategy_store.save(strategy)

        # Sort by Sharpe and keep top pool_size
        candidates.sort(key=lambda x: x[1].sharpe_ratio, reverse=True)
        pool = candidates[:pool_size]

        self.logger.info("\nInitial pool:")
        for rank, (s, r, c) in enumerate(pool):
            self.logger.info(
                f"  #{rank+1} {s.id} | Sharpe: {r.sharpe_ratio:.3f} | "
                f"Return: {r.total_return:.2%} | DD: {r.max_drawdown:.2%} | Grade: {c.overall_grade}"
            )

        best_strategy, best_result, _ = pool[0]
        best_sharpe = best_result.sharpe_ratio
        iteration = initial_population
        no_improve_count = 0

        # Phase 2: Iterative population-based optimization
        self.logger.info(f"\n--- Phase 2: Population-Based Optimization ---")

        while iteration < max_iterations:
            new_candidates = []

            # Mutate each strategy in the pool
            for parent_strategy, parent_result, parent_critique in pool:
                for _ in range(mutations_per_strategy):
                    child = self.optimizer_agent.run(strategy=parent_strategy, critique=parent_critique)
                    child_result, child_critique = self._evaluate(child, data)
                    new_candidates.append((child, child_result, child_critique))
                    self.iteration_log.record(iteration, child, child_result, child_critique)
                    iteration += 1
                    if iteration >= max_iterations:
                        break
                if iteration >= max_iterations:
                    break

            # Also inject 1-2 fresh random strategies for diversity
            for _ in range(2):
                if iteration >= max_iterations:
                    break
                fresh = self.strategy_agent.run(pair=pair)
                fresh_result, fresh_critique = self._evaluate(fresh, data)
                new_candidates.append((fresh, fresh_result, fresh_critique))
                self.iteration_log.record(iteration, fresh, fresh_result, fresh_critique)
                iteration += 1

            # Merge pool with new candidates, keep top pool_size
            all_candidates = pool + new_candidates
            all_candidates.sort(key=lambda x: x[1].sharpe_ratio, reverse=True)
            pool = all_candidates[:pool_size]

            current_best = pool[0]
            if current_best[1].sharpe_ratio > best_sharpe:
                improvement = current_best[1].sharpe_ratio - best_sharpe
                best_sharpe = current_best[1].sharpe_ratio
                best_strategy = current_best[0]
                best_result = current_best[1]
                no_improve_count = 0
                self.logger.info(
                    f"  Gen {iteration}: [IMPROVED +{improvement:.3f}] "
                    f"Best Sharpe: {best_sharpe:.3f} | Return: {best_result.total_return:.2%} | "
                    f"DD: {best_result.max_drawdown:.2%} | Trades: {best_result.total_trades}"
                )
            else:
                no_improve_count += 1
                self.logger.info(
                    f"  Gen {iteration}: No improvement ({no_improve_count}) | "
                    f"Best Sharpe: {best_sharpe:.3f}"
                )

            # Check target
            if best_sharpe >= target_sharpe and pool[0][2].overall_grade in ("A", "B"):
                self.logger.info(f"\nTarget reached! Sharpe: {best_sharpe:.3f}")
                break

        # Save best
        self.strategy_store.save_best(best_strategy)
        self._print_final_report(best_strategy, best_result, pool)
        return best_strategy, best_result

    def _print_final_report(self, best: Strategy, result: BacktestResult, pool):
        self.logger.info("\n" + "=" * 60)
        self.logger.info("OPTIMIZATION COMPLETE")
        self.logger.info("=" * 60)
        self.logger.info(f"Best Strategy: {best.id} (v{best.version})")
        self.logger.info(f"  Total Return:  {result.total_return:.2%}")
        self.logger.info(f"  Sharpe Ratio:  {result.sharpe_ratio:.3f}")
        self.logger.info(f"  Max Drawdown:  {result.max_drawdown:.2%}")
        self.logger.info(f"  Win Rate:      {result.win_rate:.2%}")
        self.logger.info(f"  Profit Factor: {result.profit_factor:.3f}")
        self.logger.info(f"  Total Trades:  {result.total_trades}")
        self.logger.info(f"  Calmar Ratio:  {result.calmar_ratio:.3f}")
        self.logger.info(f"\nFinal pool:")
        for rank, (s, r, c) in enumerate(pool):
            self.logger.info(
                f"  #{rank+1} {s.id} | Sharpe: {r.sharpe_ratio:.3f} | "
                f"Return: {r.total_return:.2%} | Grade: {c.overall_grade}"
            )
        self.logger.info(f"\nSaved to: output/strategies/best_strategy.json")
