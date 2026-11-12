/**
 * PostHog event names — frontend events only.
 */
export const PostHogEvent = {
  WORKFLOW_EDITOR_OPENED: "workflow_editor_opened",
  WORKFLOW_NODE_ADDED: "workflow_node_added",
  // Agent-builder interaction events. Terminal outcomes (built/saved/error)
  // are emitted server-side; these capture what the user does in the UI.
  BUILDER_OPENED: "builder_opened",
  BUILDER_PROMPT_SUBMITTED: "builder_prompt_submitted",
  BUILDER_TEST_CLICKED: "builder_test_clicked",
  // In-session friction funnel: where in the conversation users stall.
  BUILDER_QUESTIONS_SHOWN: "builder_questions_shown",
  BUILDER_QUESTIONS_ANSWERED: "builder_questions_answered",
  BUILDER_PLAN_SHOWN: "builder_plan_shown",
  BUILDER_PLAN_APPROVED: "builder_plan_approved",
  BUILDER_PLAN_REVISE: "builder_plan_revise",
  BUILDER_TOOL_PROPOSED: "builder_tool_proposed",
  BUILDER_TOOL_APPROVED: "builder_tool_approved",
  BUILDER_TOOL_DENIED: "builder_tool_denied",
  BUILDER_CANCELLED: "builder_cancelled",
  BUILDER_NEW_CONVERSATION: "builder_new_conversation",
  WORKFLOW_RUN_DETAILS_VIEWED: "workflow_run_details_viewed",
  RECORDING_PLAYED: "recording_played",
  TRANSCRIPT_VIEWED: "transcript_viewed",
  WEB_CALL_INITIATED: "web_call_initiated",
  SIGNED_IN: "signed_in",
  GITHUB_STAR_CLICKED: "github_star_clicked",
  SLACK_COMMUNITY_CLICKED: "slack_community_clicked",
  HIRE_EXPERT_OPENED: "hire_expert_opened",
  HIRE_EXPERT_SUBMITTED: "hire_expert_submitted",
  BUY_CREDITS_CLICKED: "buy_credits_clicked",
  BUY_CREDITS_AMOUNT_SELECTED: "buy_credits_amount_selected",
  CUSTOM_PRICING_CLICKED: "custom_pricing_clicked",
  ENTERPRISE_LEAD_OPENED: "enterprise_lead_opened",
  ENTERPRISE_LEAD_SUBMITTED: "enterprise_lead_submitted",
  HIRE_NUDGE_SHOWN: "hire_nudge_shown",
  HIRE_NUDGE_CLICKED: "hire_nudge_clicked",
  HIRE_NUDGE_DISMISSED: "hire_nudge_dismissed",
  HIRE_NUDGE_EXPIRED: "hire_nudge_expired",
  ONBOARDING_SHOWN: "onboarding_shown",
  ONBOARDING_SUBMITTED: "onboarding_submitted",
  ONBOARDING_SKIPPED: "onboarding_skipped",
} as const;
