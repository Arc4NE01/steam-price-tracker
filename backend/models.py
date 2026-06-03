from pydantic import BaseModel
from typing import Optional


class SettingsIn(BaseModel):
    steam_id: str


class ChatIn(BaseModel):
    message: str


class PriceInfo(BaseModel):
    price_raw: int
    price_usd: float
    currency: str
    discount_pct: int


class GameWithPrices(BaseModel):
    app_id: int
    name: str
    header_image: str
    prices: dict[str, PriceInfo]
    lowest_region: Optional[str] = None
    lowest_price_usd: Optional[float] = None
    is_upcoming: bool = False
    release_date: str = ""
    days_until_release: Optional[int] = None
    has_variants: bool = False


class SyncStatus(BaseModel):
    is_syncing: bool
    phase: str
    done: int
    total: int
    last_sync: Optional[str]
    game_count: int
    last_error: str = ""
