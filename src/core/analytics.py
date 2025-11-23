"""Portfolio analytics and statistical calculations."""

from typing import Any, Dict, List, Optional

import numpy as np
from nautilus_trader.model.position import Position


class PortfolioAnalytics:
    """Statistical calculations and analytics for portfolio positions."""

    def calculate_avg_duration(self, positions: List[Position]) -> Optional[float]:
        """
        Calculate average trade duration in hours.

        Args:
            positions: List of closed positions

        Returns:
            Average duration in hours, or None if no valid positions
        """
        if not positions:
            return None

        durations = []
        for position in positions:
            if (
                hasattr(position, "closed_time")
                and position.closed_time
                and hasattr(position, "opened_time")
                and position.opened_time
            ):
                duration = (position.closed_time - position.opened_time).total_seconds() / 3600
                durations.append(duration)

        return sum(durations) / len(durations) if durations else None

    def calculate_pnl_statistics(self, positions: List[Position]) -> Dict[str, Any]:
        """
        Calculate PnL statistics from positions.

        Args:
            positions: List of closed positions

        Returns:
            Dictionary with PnL statistics
        """
        if not positions:
            return {"total_pnl": 0.0, "avg_pnl": 0.0, "win_rate": 0.0}

        pnls = []
        for position in positions:
            if hasattr(position, "realized_pnl") and position.realized_pnl is not None:
                pnls.append(float(position.realized_pnl))

        if not pnls:
            return {"total_pnl": 0.0, "avg_pnl": 0.0, "win_rate": 0.0}

        winning_trades = [p for p in pnls if p > 0]

        return {
            "total_pnl": sum(pnls),
            "avg_pnl": np.mean(pnls),
            "win_rate": len(winning_trades) / len(pnls),
            "max_win": max(pnls) if pnls else 0.0,
            "max_loss": min(pnls) if pnls else 0.0,
            "std_pnl": np.std(pnls) if len(pnls) > 1 else 0.0,
        }

    def calculate_side_statistics(self, positions: List[Position]) -> Dict[str, Any]:
        """
        Calculate statistics by position side (LONG/SHORT).

        Args:
            positions: List of closed positions

        Returns:
            Dictionary with statistics for LONG and SHORT sides
        """
        long_pnls = []
        short_pnls = []

        for position in positions:
            if hasattr(position, "realized_pnl") and position.realized_pnl is not None:
                pnl = float(position.realized_pnl)
                if hasattr(position, "is_long") and position.is_long:
                    long_pnls.append(pnl)
                else:
                    short_pnls.append(pnl)

        return {
            "long": {
                "trade_count": len(long_pnls),
                "total_pnl": sum(long_pnls) if long_pnls else 0.0,
                "avg_pnl": np.mean(long_pnls) if long_pnls else 0.0,
                "win_rate": len([p for p in long_pnls if p > 0]) / len(long_pnls)
                if long_pnls
                else 0.0,
            },
            "short": {
                "trade_count": len(short_pnls),
                "total_pnl": sum(short_pnls) if short_pnls else 0.0,
                "avg_pnl": np.mean(short_pnls) if short_pnls else 0.0,
                "win_rate": len([p for p in short_pnls if p > 0]) / len(short_pnls)
                if short_pnls
                else 0.0,
            },
        }

    def calculate_position_size_stats(self, positions: List[Position]) -> Dict[str, Any]:
        """
        Calculate position size statistics.

        Args:
            positions: List of closed positions

        Returns:
            Dictionary with size statistics
        """
        if not positions:
            return {"avg_size": 0.0, "max_size": 0.0, "min_size": 0.0}

        sizes = []
        for position in positions:
            if hasattr(position, "quantity") and position.quantity is not None:
                sizes.append(abs(float(position.quantity)))

        if not sizes:
            return {"avg_size": 0.0, "max_size": 0.0, "min_size": 0.0}

        return {
            "avg_size": np.mean(sizes),
            "max_size": max(sizes),
            "min_size": min(sizes),
            "std_size": np.std(sizes) if len(sizes) > 1 else 0.0,
        }

    def get_largest_positions(
        self, positions: List[Position], limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Get the largest positions by absolute PnL.

        Args:
            positions: List of closed positions
            limit: Maximum number of positions to return

        Returns:
            List of position data sorted by absolute PnL
        """
        if not positions:
            return []

        position_data = []
        for position in positions:
            if hasattr(position, "realized_pnl") and position.realized_pnl is not None:
                position_data.append(
                    {
                        "instrument_id": str(position.instrument_id)
                        if hasattr(position, "instrument_id")
                        else "UNKNOWN",
                        "realized_pnl": float(position.realized_pnl),
                        "quantity": float(position.quantity)
                        if hasattr(position, "quantity")
                        else 0.0,
                        "side": "LONG"
                        if hasattr(position, "is_long") and position.is_long
                        else "SHORT",
                    }
                )

        # Sort by absolute PnL
        position_data.sort(
            key=lambda x: abs(x["realized_pnl"]),  # type: ignore[arg-type]
            reverse=True,
        )

        return position_data[:limit]

    def calculate_performance_attribution(self, positions: List[Position]) -> Dict[str, Any]:
        """
        Calculate performance attribution by instrument and side.

        Args:
            positions: List of closed positions

        Returns:
            Dictionary with performance attribution metrics
        """
        if not positions:
            return {
                "by_instrument": {},
                "by_side": {},
                "total_realized_pnl": 0.0,
            }

        # Group by instrument
        by_instrument = {}
        by_side: Dict[str, List[float]] = {"LONG": [], "SHORT": []}

        for position in positions:
            if not hasattr(position, "realized_pnl") or position.realized_pnl is None:
                continue

            instrument = (
                str(position.instrument_id) if hasattr(position, "instrument_id") else "UNKNOWN"
            )
            pnl = float(position.realized_pnl)

            if instrument not in by_instrument:
                by_instrument[instrument] = {
                    "total_pnl": 0.0,
                    "trade_count": 0,
                    "winning_trades": 0,
                    "avg_pnl": 0.0,
                }

            by_instrument[instrument]["total_pnl"] += pnl
            by_instrument[instrument]["trade_count"] += 1
            if pnl > 0:
                by_instrument[instrument]["winning_trades"] += 1

            # Side analysis
            side = "LONG" if hasattr(position, "is_long") and position.is_long else "SHORT"
            by_side[side].append(pnl)

        # Calculate averages
        for instrument_data in by_instrument.values():
            if instrument_data["trade_count"] > 0:
                instrument_data["avg_pnl"] = (
                    instrument_data["total_pnl"] / instrument_data["trade_count"]
                )
                instrument_data["win_rate"] = (
                    instrument_data["winning_trades"] / instrument_data["trade_count"]
                )

        # Side statistics
        side_stats = {}
        for side, pnls in by_side.items():
            if pnls:
                side_stats[side] = {
                    "total_pnl": sum(pnls),
                    "trade_count": len(pnls),
                    "avg_pnl": np.mean(pnls),
                    "win_rate": len([p for p in pnls if p > 0]) / len(pnls),
                }

        return {
            "by_instrument": by_instrument,
            "by_side": side_stats,
            "total_realized_pnl": sum(
                float(p.realized_pnl)
                for p in positions
                if hasattr(p, "realized_pnl") and p.realized_pnl is not None
            ),
        }
