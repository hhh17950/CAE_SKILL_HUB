"""Integration boundary reserved for the real SDK; it must never fall back to Mock."""


class JusmarAdapter:
    def __init__(self):
        raise RuntimeError("真实 jusmar_app SDK 尚未接入；请使用 CAE_PROVIDER=mock")
