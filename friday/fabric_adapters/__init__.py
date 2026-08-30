"""
Fabric adapters: one module per external provider.

Each module here exposes a module-level ``DESCRIPTOR: fabric.Provider`` and,
optionally, four functions the fabric calls by name:

    start()               bring the provider up; return an opaque handle
    stop(handle)          take it down
    health(handle)        -> {"state": ..., "detail": ...} or a bool
    call(op, handle, **kw) run one operation

Nothing here is imported by Friday's core at startup. The fabric discovers
these with pkgutil when someone asks the registry a question, and only calls
``start`` when the router actually picks the provider. A module that fails to
import is reported as an UNAVAILABLE provider, not a broken Friday - which is
the whole point of keeping upstreams behind this boundary.
"""
