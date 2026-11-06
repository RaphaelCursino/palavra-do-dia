class PreflightError(RuntimeError):
    """A safe, stage-coded migration failure."""

    DEFAULT_DIAGNOSTIC_CODE = "MIGRATION_PREFLIGHT_FAILED"

    def __init__(
        self,
        message,
        diagnostic_code=DEFAULT_DIAGNOSTIC_CODE,
        diagnostic_details=None,
        stage=None,
    ):
        super().__init__(message)
        self.diagnostic_code = diagnostic_code
        self.diagnostic_details = diagnostic_details or {}
        self.stage = stage
