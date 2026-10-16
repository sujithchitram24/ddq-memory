"""Typed extraction errors. `retryable` decides what the job queue does next."""


class ExtractionError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


# Permanent: retrying the same bytes with the same code will fail the same way.
NO_TEXT_LAYER = "NO_TEXT_LAYER"  # scanned PDF, needs OCR (not implemented)
ENCRYPTED = "ENCRYPTED"
CORRUPT_FILE = "CORRUPT_FILE"
EMPTY_DOCUMENT = "EMPTY_DOCUMENT"
