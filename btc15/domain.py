from datetime import datetime, timezone
from pydantic import BaseModel, field_validator
import math

def utc(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if dt.tzinfo is None:
        raise ValueError('Timezone required')
    return dt.astimezone(timezone.utc)

def now():
    return datetime.now(timezone.utc)

class Market(BaseModel):
    ticker: str
    title: str = ''
    open_time: datetime
    close_time: datetime
    status: str
    floor_strike: float | None = None
    rules_primary: str = ''
    rules_secondary: str = ''
    result: str = ''
    settlement_ts: datetime | None = None
    yes_bid_dollars: float | None = None
    yes_ask_dollars: float | None = None
    no_bid_dollars: float | None = None
    no_ask_dollars: float | None = None
    volume_fp: float = 0
    open_interest_fp: float = 0
    @field_validator('open_time', 'close_time', mode='before')
    @classmethod
    def dates(cls, value):
        return utc(value)
    @property
    def supported_rules(self):
        r = self.rules_primary.lower()
        return all(s in r for s in ['simple average', 'sixty seconds', 'brti', 'at least']) and abs((self.close_time-self.open_time).total_seconds()-900) < 2
    @property
    def threshold(self):
        x = self.floor_strike
        return x if x is not None and math.isfinite(x) and x > 0 else None
    def remaining(self, at):
        return (self.close_time-utc(at)).total_seconds()

def active_market(markets, at):
    choices = [m for m in markets if m.status in ('open','active') and m.open_time <= at < m.close_time]
    return min(choices, key=lambda m: m.close_time) if choices else None

def official_label(market):
    if market.status in ('settled','finalized') and market.result in ('yes','no'):
        return int(market.result == 'yes')
    return None
