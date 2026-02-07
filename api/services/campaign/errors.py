"""
Campaign service exceptions.
"""


class ConcurrentSlotAcquisitionError(Exception):
    """Raised when a concurrent call slot cannot be acquired within the timeout period."""

    def __init__(self, organization_id: int, campaign_id: int, wait_time: float):
        self.organization_id = organization_id
        self.campaign_id = campaign_id
        self.wait_time = wait_time
        super().__init__(
            f"Failed to acquire concurrent slot for org {organization_id}, "
            f"campaign {campaign_id} after waiting {wait_time:.1f}s"
        )


class PhoneNumberPoolExhaustedError(Exception):
    """Raised when no phone numbers are available in the pool for outbound calls."""

    def __init__(self, organization_id: int):
        self.organization_id = organization_id
        super().__init__(
            f"All phone numbers are in use for org {organization_id}. "
            f"No available from_number in pool."
        )
