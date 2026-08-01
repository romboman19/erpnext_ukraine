class GiftCertificateError(Exception):
    def __init__(self, message: str, code: str, *, retryable: bool = False, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}


class GiftCertificateConflict(GiftCertificateError):
    pass
