import os

# Skip pydantic's plugin discovery (a full `importlib.metadata.distributions()`
# scan of every installed package, ~30-50ms) unless the environment already
# asked for a specific setting. Tau ships no pydantic plugins and none are
# expected to be installed alongside it; this must run before the first
# pydantic BaseModel subclass is defined anywhere in the process, so it lives
# in this package's __init__ (executed before any tau submodule).
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "1")
