from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import requests


@dataclass
class CalendarEvent:
    title: str
    currency: str
    impact: str
    event_time: datetime
    source: str


class BaseCalendarProvider:
    def fetch_events(self, now_utc: datetime) -> List[CalendarEvent]:
        raise NotImplementedError


class TradingEconomicsProvider(BaseCalendarProvider):
    """Fetches calendar data from a TradingEconomics-compatible endpoint."""

    def __init__(self, endpoint: str, timeout_sec: int = 8):
        self.endpoint = endpoint
        self.timeout_sec = timeout_sec

    def fetch_events(self, now_utc: datetime) -> List[CalendarEvent]:
        response = requests.get(self.endpoint, timeout=self.timeout_sec)
        response.raise_for_status()

        payload = response.json()
        events: List[CalendarEvent] = []
        for row in payload if isinstance(payload, list) else []:
            dt_text = row.get("Date") or row.get("date") or row.get("event_time")
            event_time = _parse_datetime(dt_text)
            if not event_time:
                continue

            events.append(
                CalendarEvent(
                    title=row.get("Event") or row.get("event") or row.get("title", "Unknown"),
                    currency=(row.get("Currency") or row.get("currency") or "").upper(),
                    impact=(row.get("Importance") or row.get("impact") or "").lower(),
                    event_time=event_time,
                    source="tradingeconomics",
                )
            )

        return events


class JsonCalendarProvider(BaseCalendarProvider):
    """Generic JSON endpoint for Forex Factory / Investing adapters."""

    def __init__(self, endpoint: str, source_name: str, timeout_sec: int = 8):
        self.endpoint = endpoint
        self.source_name = source_name
        self.timeout_sec = timeout_sec

    def fetch_events(self, now_utc: datetime) -> List[CalendarEvent]:
        response = requests.get(self.endpoint, timeout=self.timeout_sec)
        response.raise_for_status()
        payload = response.json()

        events: List[CalendarEvent] = []
        for row in payload if isinstance(payload, list) else []:
            event_time = _parse_datetime(
                row.get("event_time") or row.get("datetime") or row.get("time")
            )
            if not event_time:
                continue

            events.append(
                CalendarEvent(
                    title=row.get("title", "Unknown"),
                    currency=(row.get("currency") or "").upper(),
                    impact=(row.get("impact") or "").lower(),
                    event_time=event_time,
                    source=self.source_name,
                )
            )

        return events


class NewsAPIProvider:
    """Optional incident feed from NewsAPI-like endpoint."""

    def __init__(self, endpoint: str, timeout_sec: int = 8):
        self.endpoint = endpoint
        self.timeout_sec = timeout_sec

    def fetch_events(self, now_utc: datetime) -> List[CalendarEvent]:
        response = requests.get(self.endpoint, timeout=self.timeout_sec)
        response.raise_for_status()
        payload = response.json()

        rows = payload.get("articles", []) if isinstance(payload, dict) else []
        events: List[CalendarEvent] = []
        for row in rows:
            title = row.get("title", "")
            published_at = _parse_datetime(row.get("publishedAt"))
            if not published_at:
                continue

            currency = _infer_currency_from_text(title)
            impact = "high" if _is_high_impact_headline(title) else "medium"

            events.append(
                CalendarEvent(
                    title=title,
                    currency=currency,
                    impact=impact,
                    event_time=published_at,
                    source="newsapi",
                )
            )

        return events


class NewsFilter:
    def __init__(self, cfg: Dict[str, Any]):
        module_cfg = cfg.get("news_filter", {})
        self.enabled = bool(module_cfg.get("enabled", False))
        self.high_impact_only = bool(module_cfg.get("high_impact_only", True))
        self.pre_news_block_min = int(module_cfg.get("pre_news_block_min", 45))
        self.post_news_block_min = int(module_cfg.get("post_news_block_min", 30))
        self.refresh_interval_sec = int(module_cfg.get("refresh_interval_sec", 180))
        self.fallback_mode = module_cfg.get("fallback_mode", "fail_open")

        self.providers: List[BaseCalendarProvider] = []
        provider_cfg = module_cfg.get("providers", {})

        if provider_cfg.get("tradingeconomics", {}).get("enabled"):
            endpoint = provider_cfg["tradingeconomics"].get("endpoint")
            if endpoint:
                self.providers.append(TradingEconomicsProvider(endpoint))

        if provider_cfg.get("forex_factory", {}).get("enabled"):
            endpoint = provider_cfg["forex_factory"].get("endpoint")
            if endpoint:
                self.providers.append(JsonCalendarProvider(endpoint, "forex_factory"))

        if provider_cfg.get("investing", {}).get("enabled"):
            endpoint = provider_cfg["investing"].get("endpoint")
            if endpoint:
                self.providers.append(JsonCalendarProvider(endpoint, "investing"))

        if provider_cfg.get("newsapi", {}).get("enabled"):
            endpoint = provider_cfg["newsapi"].get("endpoint")
            if endpoint:
                self.providers.append(NewsAPIProvider(endpoint))

        self._events_cache: List[CalendarEvent] = []
        self._last_refresh_utc: Optional[datetime] = None

    def should_block(self, symbol: str, now_utc: Optional[datetime] = None) -> Dict[str, Any]:
        if not self.enabled:
            return {"blocked": False, "reason": "news_filter_disabled"}

        now_utc = now_utc or datetime.now(timezone.utc)
        self._refresh_if_needed(now_utc)

        if not self._events_cache:
            return self._fallback_response()

        relevant_ccy = _symbol_currencies(symbol)

        active_events = []
        for event in self._events_cache:
            if self.high_impact_only and event.impact != "high":
                continue
            if event.currency and event.currency not in relevant_ccy:
                continue

            start = event.event_time - timedelta(minutes=self.pre_news_block_min)
            end = event.event_time + timedelta(minutes=self.post_news_block_min)
            if start <= now_utc <= end:
                active_events.append((event, end))

        if not active_events:
            return {"blocked": False, "reason": "news_clear"}

        latest_resume = max(end for _, end in active_events)
        top_event = active_events[0][0]

        return {
            "blocked": True,
            "reason": f"news_high_impact:{top_event.title}",
            "resume_at_utc": latest_resume.isoformat(),
            "source": top_event.source,
        }

    def _refresh_if_needed(self, now_utc: datetime) -> None:
        if self._last_refresh_utc and (now_utc - self._last_refresh_utc).total_seconds() < self.refresh_interval_sec:
            return

        events: List[CalendarEvent] = []
        for provider in self.providers:
            try:
                events.extend(provider.fetch_events(now_utc))
            except Exception:
                continue

        if events:
            self._events_cache = events
            self._last_refresh_utc = now_utc

    def _fallback_response(self) -> Dict[str, Any]:
        if self.fallback_mode == "fail_close":
            return {
                "blocked": True,
                "reason": "news_data_unavailable_fail_close",
            }

        return {
            "blocked": False,
            "reason": "news_data_unavailable_fail_open",
        }


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None

    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _symbol_currencies(symbol: str) -> Set[str]:
    symbol = symbol.upper()
    if len(symbol) >= 6 and symbol[:3].isalpha() and symbol[3:6].isalpha():
        return {symbol[:3], symbol[3:6]}

    mapping = {
        "XAUUSD": {"USD"},
        "XAGUSD": {"USD"},
        "BTCUSD": {"USD"},
        "ETHUSD": {"USD"},
        "US30": {"USD"},
        "NAS100": {"USD"},
        "SPX500": {"USD"},
        "GER40": {"EUR"},
        "UK100": {"GBP"},
        "XBRUSD": {"USD"},
    }
    return mapping.get(symbol, {"USD"})


def _infer_currency_from_text(text: str) -> str:
    upper = text.upper()
    for ccy in ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "CNY"):
        if ccy in upper:
            return ccy
    return "USD"


def _is_high_impact_headline(text: str) -> bool:
    upper = text.upper()
    keywords = ["CPI", "NFP", "RATE", "FOMC", "INTEREST", "EMERGENCY", "WAR"]
    return any(word in upper for word in keywords)
