class LoyaltyError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class LoyaltyConflict(LoyaltyError):
    pass
