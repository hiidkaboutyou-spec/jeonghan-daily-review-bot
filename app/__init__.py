"""Application package initialization."""

# Install the production ChannelStyleCaptionWriter fidelity hardening before
# callers import the runtime verifier/writer symbols. The layer is deterministic,
# source-authorized, and shared by normal private-review runtime and PART 4.
from . import channel_part4_hardening as _channel_part4_hardening  # noqa: F401,E402
from . import channel_part4_finalfix as _channel_part4_finalfix  # noqa: F401,E402
