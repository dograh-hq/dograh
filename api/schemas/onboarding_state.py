from datetime import datetime

from pydantic import BaseModel, Field


class OnboardingState(BaseModel):
    """Per-user onboarding state, stored under UserConfigurationKey.ONBOARDING.

    Server-authoritative replacement for the browser-localStorage onboarding
    store, so the post-signup gate and one-time tooltips hold across devices.
    """

    # Post-signup onboarding form gate: set once on submit/skip.
    completed_at: datetime | None = None
    skipped: bool = False
    # One-time UI affordances (tooltip keys, milestone action keys). Kept as
    # free-form strings — the UI owns the vocabulary.
    seen_tooltips: list[str] = Field(default_factory=list)
    completed_actions: list[str] = Field(default_factory=list)


class OnboardingStateUpdate(BaseModel):
    """Partial update merged into the stored state.

    Scalars overwrite when supplied; list entries are unioned into the stored
    lists, so concurrent updates (e.g. two tabs marking different tooltips)
    don't drop each other's items.
    """

    completed_at: datetime | None = None
    skipped: bool | None = None
    seen_tooltips: list[str] | None = None
    completed_actions: list[str] | None = None

    def apply_to(self, state: OnboardingState) -> OnboardingState:
        merged = state.model_copy(deep=True)
        if self.completed_at is not None:
            merged.completed_at = self.completed_at
        if self.skipped is not None:
            merged.skipped = self.skipped
        for tooltip in self.seen_tooltips or []:
            if tooltip not in merged.seen_tooltips:
                merged.seen_tooltips.append(tooltip)
        for action in self.completed_actions or []:
            if action not in merged.completed_actions:
                merged.completed_actions.append(action)
        return merged
