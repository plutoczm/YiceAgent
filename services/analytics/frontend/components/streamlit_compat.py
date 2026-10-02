class _NoopStreamlit:
    session_state: dict = {}

    def __getattr__(self, name):
        def _noop(*args, **kwargs):
            if name == "Page":
                return _NoopPage(args[0] if args else None)
            if name == "navigation":
                return _NoopNavigation(args[0] if args else [])
            if name in {"button", "form_submit_button", "checkbox"}:
                return False
            if name in {"text_area", "text_input", "selectbox"}:
                return kwargs.get("value") or (args[1] if len(args) > 1 else "")
            if name == "segmented_control":
                return kwargs.get("default")
            if name == "slider":
                return kwargs.get("value", 5)
            if name == "file_uploader":
                return None
            if name == "columns":
                count = args[0] if args else 1
                if isinstance(count, int):
                    return [self for _ in range(count)]
                return [self for _ in count]
            if name in {"tabs"}:
                return [self for _ in (args[0] if args else [])]
            if name in {"container", "form", "expander", "spinner", "chat_message"}:
                return self
            return None

        return _noop

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _NoopPage:
    def __init__(self, target):
        self.target = target

    def run(self):
        if callable(self.target):
            return self.target()
        return None


class _NoopNavigation:
    def __init__(self, pages):
        self.pages = pages

    def run(self):
        if self.pages:
            return self.pages[0].run()
        return None


try:
    import streamlit as st
except ModuleNotFoundError:
    st = _NoopStreamlit()


__all__ = ["st"]
