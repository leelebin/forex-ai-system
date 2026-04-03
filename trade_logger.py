import json
import os
import queue
import random
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
class TradeLifecycleLogger:
    def __init__(self, base_dir="logs/trades", flush_interval_sec=10):
        self.base_dir = base_dir
        self.flush_interval_sec = max(1, int(flush_interval_sec))
        self.lock = threading.Lock()
        self.active_trades = {}
        self.ticket_index = {}
        self.closed_trades = {}
        self.write_queue = queue.Queue(maxsize=2000)
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._writer_loop, daemon=True)
        os.makedirs(self.base_dir, exist_ok=True)
        self._worker.start()

    def _utc_now_iso(self):
        return datetime.now(timezone.utc).isoformat()

    def _generate_trade_id(self, symbol):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        rand = f"{random.randint(1000, 9999)}"
        return f"{symbol}_{ts}_{rand}"

    def _build_trade_lifecycle(self, order):
        trade_id = order.get("trade_id") or self._generate_trade_id(order["symbol"])
        now_iso = self._utc_now_iso()
        direction = str(order.get("direction", "")).lower()

        return {
            "trade_id": trade_id,
            "symbol": order["symbol"],
            "open_time": now_iso,
            "close_time": None,
            "open_price": float(order.get("open_price", 0.0) or 0.0),
            "close_price": None,
            "volume": float(order.get("volume", 0.0) or 0.0),
            "direction": direction,
            "initial_features": order.get("initial_features", {}),
            "tick_updates": [],
            "events": [],
            "result": {
                "pnl": 0.0,
                "max_drawdown": 0.0,
                "max_profit": 0.0,
                "holding_seconds": 0,
            },
            "entry_reason": order.get("entry_reason"),
            "signal_score": order.get("signal_score"),
            "volatility_flag": order.get("volatility_flag"),
            "position_ticket": order.get("position_ticket"),
            "_peak_profit": None,
            "_trough_profit": None,
            "_last_profit": 0.0,
            "_last_price": float(order.get("open_price", 0.0) or 0.0),
            "_last_sl": order.get("sl"),
            "_last_tp": order.get("tp"),
            "_last_volume": float(order.get("volume", 0.0) or 0.0),
        }

    def _enqueue_write(self, trade_id, payload):
        item = (trade_id, payload)
        try:
            self.write_queue.put_nowait(item)
        except queue.Full:
            # logging must not block trading; drop oldest and try again
            try:
                self.write_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.write_queue.put_nowait(item)
            except queue.Full:
                return

    def _writer_loop(self):
        last_flush = time.time()
        while not self._stop_event.is_set():
            timeout = 0.5
            try:
                trade_id, payload = self.write_queue.get(timeout=timeout)
                self._write_trade_file(trade_id, payload)
            except queue.Empty:
                pass

            now = time.time()
            if now - last_flush >= self.flush_interval_sec:
                self.flush_active_snapshot()
                last_flush = now

    def _clean_payload(self, payload):
        clean = deepcopy(payload)
        for key in ("_peak_profit", "_trough_profit", "_last_profit", "_last_price", "_last_sl", "_last_tp", "_last_volume"):
            clean.pop(key, None)
        return clean

    def _write_trade_file(self, trade_id, payload):
        file_path = os.path.join(self.base_dir, f"{trade_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self._clean_payload(payload), f, ensure_ascii=False, indent=2)

    def init_trade_lifecycle(self, order):
        with self.lock:
            lifecycle = self._build_trade_lifecycle(order)
            trade_id = lifecycle["trade_id"]
            self.active_trades[trade_id] = lifecycle
            ticket = lifecycle.get("position_ticket")
            if ticket:
                self.ticket_index[int(ticket)] = trade_id
            return trade_id

    def bind_position(self, trade_id, position_ticket):
        if position_ticket is None:
            return
        with self.lock:
            trade = self.active_trades.get(trade_id)
            if not trade:
                return
            ticket = int(position_ticket)
            trade["position_ticket"] = ticket
            self.ticket_index[ticket] = trade_id

    def resolve_trade_id(self, position_ticket):
        if position_ticket is None:
            return None
        with self.lock:
            return self.ticket_index.get(int(position_ticket))

    def update_trade_lifecycle(self, trade_id, update):
        with self.lock:
            trade = self.active_trades.get(trade_id)
            if not trade:
                return False

            point = {
                "timestamp": update.get("timestamp") or self._utc_now_iso(),
                "price": float(update.get("price", trade.get("_last_price", 0.0)) or 0.0),
                "profit": float(update.get("profit", trade.get("_last_profit", 0.0)) or 0.0),
                "atr": update.get("atr"),
                "rsi": update.get("rsi"),
                "spread": update.get("spread"),
                "market_state": update.get("market_state"),
            }
            trade["tick_updates"].append(point)
            trade["_last_profit"] = point["profit"]
            trade["_last_price"] = point["price"]
            trade["_last_sl"] = update.get("sl", trade.get("_last_sl"))
            trade["_last_tp"] = update.get("tp", trade.get("_last_tp"))

            current_volume = float(update.get("volume", trade.get("_last_volume", trade.get("volume", 0.0))) or 0.0)
            prev_volume = float(trade.get("_last_volume", current_volume) or 0.0)
            if current_volume > prev_volume + 1e-9:
                self._append_event_no_lock(
                    trade,
                    {
                        "type": "ADD",
                        "timestamp": point["timestamp"],
                        "price": point["price"],
                        "profit": point["profit"],
                    },
                )
            elif current_volume + 1e-9 < prev_volume:
                self._append_event_no_lock(
                    trade,
                    {
                        "type": "REDUCE",
                        "timestamp": point["timestamp"],
                        "price": point["price"],
                        "profit": point["profit"],
                    },
                )
            trade["_last_volume"] = current_volume

            if trade["_peak_profit"] is None or point["profit"] > trade["_peak_profit"]:
                trade["_peak_profit"] = point["profit"]
            if trade["_trough_profit"] is None or point["profit"] < trade["_trough_profit"]:
                trade["_trough_profit"] = point["profit"]
            return True

    def _append_event_no_lock(self, trade, event):
        event_data = {
            "type": event.get("type", "UNKNOWN"),
            "timestamp": event.get("timestamp") or self._utc_now_iso(),
            "price": float(event.get("price", trade.get("_last_price", 0.0)) or 0.0),
            "profit": float(event.get("profit", trade.get("_last_profit", 0.0)) or 0.0),
        }
        trade["events"].append(event_data)

    def record_event(self, trade_id, event):
        with self.lock:
            trade = self.active_trades.get(trade_id)
            if not trade:
                return False
            self._append_event_no_lock(trade, event)
            return True

    def record_event_by_ticket(self, position_ticket, event):
        trade_id = self.resolve_trade_id(position_ticket)
        if not trade_id:
            return False
        return self.record_event(trade_id, event)

    def finalize_trade_lifecycle(self, trade_id, close_payload=None):
        close_payload = close_payload or {}
        with self.lock:
            trade = self.active_trades.pop(trade_id, None)
            if not trade:
                return None

            ticket = trade.get("position_ticket")
            if ticket in self.ticket_index:
                self.ticket_index.pop(ticket, None)

            close_time = close_payload.get("close_time") or self._utc_now_iso()
            close_price = close_payload.get("close_price", trade.get("_last_price"))
            pnl = float(close_payload.get("pnl", trade.get("_last_profit", 0.0)) or 0.0)
            close_type = close_payload.get("close_type")

            trade["close_time"] = close_time
            trade["close_price"] = float(close_price or 0.0)
            trade["result"]["pnl"] = pnl
            trade["result"]["max_profit"] = float(trade.get("_peak_profit") if trade.get("_peak_profit") is not None else pnl)
            trough = trade.get("_trough_profit")
            trade["result"]["max_drawdown"] = float(trough if trough is not None else min(0.0, pnl))

            try:
                open_dt = datetime.fromisoformat(trade["open_time"])
                close_dt = datetime.fromisoformat(close_time)
                trade["result"]["holding_seconds"] = max(0, int((close_dt - open_dt).total_seconds()))
            except Exception:
                trade["result"]["holding_seconds"] = 0

            if close_type:
                self._append_event_no_lock(
                    trade,
                    {
                        "type": close_type,
                        "timestamp": close_time,
                        "price": trade["close_price"],
                        "profit": pnl,
                    },
                )

            self.closed_trades[trade_id] = trade
            self._enqueue_write(trade_id, trade)
            return trade

    def _infer_close_type(self, trade):
        close_price = float(trade.get("_last_price", 0.0) or 0.0)
        sl = trade.get("_last_sl")
        tp = trade.get("_last_tp")
        if sl is not None and abs(close_price - float(sl)) <= max(1e-6, abs(close_price) * 0.0002):
            return "SL"
        if tp is not None and abs(close_price - float(tp)) <= max(1e-6, abs(close_price) * 0.0002):
            return "TP"
        return "MANUAL_CLOSE"

    def sync_open_positions(self, open_positions, market_by_symbol):
        now = self._utc_now_iso()

        current_tickets = set()
        for pos in open_positions:
            ticket = int(getattr(pos, "ticket"))
            current_tickets.add(ticket)
            trade_id = self.resolve_trade_id(ticket)
            if not trade_id:
                symbol = getattr(pos, "symbol", "")
                direction = "buy" if int(getattr(pos, "type", 0)) == 0 else "sell"
                with self.lock:
                    candidates = [
                        tid
                        for tid, trade in self.active_trades.items()
                        if trade.get("symbol") == symbol
                        and trade.get("direction") == direction
                        and not trade.get("position_ticket")
                    ]
                if len(candidates) == 1:
                    trade_id = candidates[0]
                    self.bind_position(trade_id, ticket)
                else:
                    continue

            symbol = getattr(pos, "symbol", "")
            market = market_by_symbol.get(symbol, {})
            self.update_trade_lifecycle(
                trade_id,
                {
                    "timestamp": now,
                    "price": float(market.get("price", 0.0) or 0.0),
                    "profit": float(getattr(pos, "profit", 0.0) or 0.0),
                    "atr": market.get("atr"),
                    "rsi": market.get("rsi"),
                    "spread": market.get("spread"),
                    "market_state": market.get("market_state"),
                    "sl": getattr(pos, "sl", None),
                    "tp": getattr(pos, "tp", None),
                    "volume": getattr(pos, "volume", None),
                },
            )

        tracked = []
        with self.lock:
            for t, trade_id in self.ticket_index.items():
                tracked.append((t, trade_id))

        for ticket, trade_id in tracked:
            if ticket in current_tickets:
                continue
            with self.lock:
                trade = self.active_trades.get(trade_id)
                if not trade:
                    continue
                close_type = self._infer_close_type(trade)
                pnl = float(trade.get("_last_profit", 0.0) or 0.0)
                close_price = float(trade.get("_last_price", 0.0) or 0.0)
            self.finalize_trade_lifecycle(
                trade_id,
                {
                    "close_time": now,
                    "close_price": close_price,
                    "pnl": pnl,
                    "close_type": close_type,
                },
            )

    def flush_active_snapshot(self):
        with self.lock:
            snapshots = [(tid, deepcopy(trade)) for tid, trade in self.active_trades.items()]

        for trade_id, trade in snapshots:
            self._enqueue_write(trade_id, trade)


LOGGER = TradeLifecycleLogger()


def init_trade_lifecycle(order):
    return LOGGER.init_trade_lifecycle(order)


def update_trade_lifecycle(trade_id, update=None):
    return LOGGER.update_trade_lifecycle(trade_id, update or {})


def record_event(trade_id, event):
    return LOGGER.record_event(trade_id, event)


def finalize_trade_lifecycle(trade_id, close_payload=None):
    return LOGGER.finalize_trade_lifecycle(trade_id, close_payload=close_payload)
