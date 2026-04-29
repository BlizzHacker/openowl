"""Platform abstraction layer -- auto-selects the right backend."""
import sys

if sys.platform == "win32":
    from .win32_backend import *  # noqa: F401,F403
elif sys.platform == "darwin":
    from .darwin_backend import *  # noqa: F401,F403
else:
    raise RuntimeError(
        f"openowl does not support platform '{sys.platform}'. "
        f"Supported: win32, darwin."
    )

