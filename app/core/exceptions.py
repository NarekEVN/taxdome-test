class DomainError(Exception):
    """A business rejection. The HTTP layer maps it to 422 with `code`."""

    code = "domain_error"

    def __init__(self, message: str, details: list | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


class UnknownPayerFirm(DomainError):
    code = "unknown_payer_firm"


class UnknownPayeeFirm(DomainError):
    code = "unknown_payee_firm"


class SelfPayment(DomainError):
    code = "self_payment"


class InsufficientFunds(DomainError):
    code = "insufficient_funds"


class TransientDbError(Exception):
    """Deadlock or serialization failure that persisted after all retries."""


class IdempotencyKeyReused(DomainError):
    code = "idempotency_key_reused"
