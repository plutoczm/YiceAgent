class LLMProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        provider: str = "unknown",
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable


class LLMTimeoutError(LLMProviderError):
    pass


class LLMAuthenticationError(LLMProviderError):
    pass


class LLMRateLimitError(LLMProviderError):
    pass


class LLMConnectionError(LLMProviderError):
    pass
