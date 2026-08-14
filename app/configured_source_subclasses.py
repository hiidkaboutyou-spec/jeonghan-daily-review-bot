"""Close configured-source policy gaps in completeness/health collector subclasses."""

from __future__ import annotations

from .x_client import XCollector
from .x_completeness import CompleteWindowXCollector

# CompleteWindowXCollector defines collect_source itself, so patching XCollector's
# method does not propagate through normal inheritance. Reuse the already-installed
# configured-source implementation explicitly. HealthTrackingXCollector inherits
# this method and therefore receives the same 24h source-authority boundary.
CompleteWindowXCollector.collect_source = XCollector.collect_source
