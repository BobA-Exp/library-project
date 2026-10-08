"""Custom errors raised by the catalog tool."""


class LibraryError(Exception):
    """Base error for the catalog tool."""


class APIError(LibraryError):
    """The Open Library request failed after retries."""


class RateLimitError(APIError):
    """Open Library returned HTTP 429 after the retry budget was used."""


class DataValidationError(LibraryError):
    """The request or the JSON payload was not usable."""
