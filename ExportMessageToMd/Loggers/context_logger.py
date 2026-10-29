from Loggers.base_logger import BaseLogger


class ContextLogger(BaseLogger):
    """Immutable metadata scope; nested operations cannot overwrite parent identity."""

    def __init__(self, delegate, **context):
        self.delegate = delegate
        self.context = context

    def child(self, segment, **context):
        path = self.context.get("item_path", "")
        return ContextLogger(self.delegate, **dict(self.context, item_path=f"{path}.{segment}" if path else segment, **context))

    def LogEvent(self, event, **details):
        self.delegate.LogEvent(event, **dict(self.context, **details))

    def LogWarning(self, error_type, exception=None):
        self.LogEvent("warning", exception_type=type(exception).__name__ if exception else None)
        # Forward presentation without creating a second, unscoped file event.
        target = self.delegate
        while isinstance(target, ContextLogger):
            target = target.delegate
        getattr(target, "delegate", target).LogWarning(error_type, exception)
