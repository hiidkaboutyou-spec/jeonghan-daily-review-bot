"""Application package initialization."""

# Install the production ChannelStyleCaptionWriter fidelity hardening before
# callers import the runtime verifier/writer symbols. These layers are deterministic,
# source-authorized, and shared by normal private-review runtime and PART 4.
from . import channel_part4_hardening as _channel_part4_hardening  # noqa: F401,E402
from . import channel_part4_finalfix as _channel_part4_finalfix  # noqa: F401,E402
from . import channel_part4_humanfix as _channel_part4_humanfix  # noqa: F401,E402
from . import channel_part4_qualityfix as _channel_part4_qualityfix  # noqa: F401,E402
from . import channel_part4_benchmark_hook as _channel_part4_benchmark_hook  # noqa: F401,E402

# Every non-Fanfic X retrieval path is source-authoritative. Fanfic/AO3 keeps its
# independent workflow and optional direct X recommendation lookup.
from . import source_authority_hardening as _source_authority_hardening  # noqa: F401,E402
from . import configured_source_subclasses as _configured_source_subclasses  # noqa: F401,E402

# Protect runnable legacy/private/webhook boundaries and stale durable state too:
# external historical rows/queued items must not bypass the collector policy.
from . import configured_source_runtime as _configured_source_runtime  # noqa: F401,E402
from . import configured_source_complete_windows as _configured_source_complete_windows  # noqa: F401,E402

# Phase 3 extends the already-authoritative source collector with bounded retries and
# durable provider-cursor checkpoints. Install/harden it before Phase 2 so lifecycle
# and Zero-Silent-Miss observability wrap the final resumable retrieval behavior.
from . import phase3_recovery as _phase3_recovery  # noqa: F401,E402
from . import phase3_recovery_hardening as _phase3_recovery_hardening  # noqa: F401,E402

# Phase 2 is intentionally installed last so it observes the exact Phase 1/Phase 3
# source-authority/runtime behavior rather than replacing it. It adds lifecycle,
# correlation, failure visibility, quarantine records and privacy-safe telemetry only.
from . import zero_silent_miss as _zero_silent_miss  # noqa: F401,E402
from . import phase2_runtime_compat as _phase2_runtime_compat  # noqa: F401,E402
from . import phase2_final_visibility as _phase2_final_visibility  # noqa: F401,E402
from . import phase2_correlation_stability as _phase2_correlation_stability  # noqa: F401,E402
