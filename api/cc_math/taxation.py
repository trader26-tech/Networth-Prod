"""
Re-export of cc_charges helpers under the cc_math namespace.

Kept as a thin shim so future strategy code only imports from `cc_math.taxation`,
giving us a clean migration path if the rate card changes.
"""
from api.cc_charges import (
    equity_delivery_charges,
    option_charges,
    tax_on_fno_income,
    tax_on_etf_gain,
)

__all__ = [
    "equity_delivery_charges",
    "option_charges",
    "tax_on_fno_income",
    "tax_on_etf_gain",
]
