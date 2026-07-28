"""
All Pydantic request/response models in one place.
Import from here instead of defining models in route files.
"""
from pydantic import BaseModel
from typing import Optional, List


class OrderRequest(BaseModel):
    variety: str = "regular"
    exchange: str = "NSE"
    tradingsymbol: str
    transaction_type: str
    quantity: int
    product: str = "MIS"
    order_type: str = "MARKET"
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    validity: str = "DAY"
    tag: Optional[str] = None


class ModifyOrderRequest(BaseModel):
    variety: str = "regular"
    quantity: Optional[int] = None
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    order_type: Optional[str] = None
    validity: Optional[str] = None


class StrategyExecLeg(BaseModel):
    symbol: str
    type: str               # CE or PE
    strike: float
    transaction_type: str   # BUY or SELL
    qty: int
    lot_size: int
    premium: float


class ExecuteStrategyRequest(BaseModel):
    strategy_name: str
    underlying: str
    expiry: str
    spot_at_entry: float
    legs: List[StrategyExecLeg]
    sl_amount: Optional[float] = None
    target_amount: Optional[float] = None
    use_real: bool = False


class UpdateSlTargetRequest(BaseModel):
    sl_amount: Optional[float] = None
    target_amount: Optional[float] = None


class StrategyLeg(BaseModel):
    type: str
    strike: float
    premium: float
    qty: int
    lot_size: int = 50
    transaction_type: str


class StrategyPnlRequest(BaseModel):
    legs: List[StrategyLeg]
    spot_from: float
    spot_to: float
    steps: int = 100


class GTTRequest(BaseModel):
    trigger_type: str
    tradingsymbol: str
    exchange: str = "NSE"
    trigger_values: List[float]
    last_price: float
    orders: List[dict]


class OptionLegRequest(BaseModel):
    """A leg of the tracked options strategy (your real trade)."""
    underlying: str                 # NIFTY, BANKNIFTY, ...
    expiry: str                     # ISO date, e.g. 2026-06-25
    strike: float
    opt_type: str                   # CE or PE
    side: str                       # BUY or SELL
    lots: int
    lot_size: Optional[int] = None  # defaults to the underlying's NFO lot size
    entry_price: float              # premium per unit at entry


class BookLegRequest(BaseModel):
    exit_price: float               # premium per unit at square-off


class SaveKeysRequest(BaseModel):
    api_key: str
    api_secret: str


class ConnectTokenRequest(BaseModel):
    request_token: str


class DirectTokenRequest(BaseModel):
    api_key: str
    access_token: str
