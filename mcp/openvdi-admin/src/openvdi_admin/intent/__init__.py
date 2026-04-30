"""Intent tools — orchestration over the thin-wrapper layer.

Each module registers one @mcp.tool() that composes multiple thin
wrappers from openvdi_admin.tools.*. Per F2/T4 in the M5 seed:
intent tools never call the broker directly; they always go through
the wrapper layer so a wrapper change propagates for free.
"""
