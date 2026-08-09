"""Application package initialization."""

# Install the production ChannelStyleCaptionWriter fidelity hardening before
# callers import the runtime verifier/writer symbols. These layers are deterministic,
# source-authorized, and shared by normal private-review runtime and PART 4.
from . import channel_part4_hardening as _channel_part4_hardening  # noqa: F401,E402
from . import channel_part4_finalfix as _channel_part4_finalfix  # noqa: F401,E402
from . import channel_part4_humanfix as _channel_part4_humanfix  # noqa: F401,E402
from . import channel_part4_benchmark_hook as _channel_part4_benchmark_hook  # noqa: F401,E402
