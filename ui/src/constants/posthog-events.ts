/**
 * PostHog event names — frontend events only.
 */
export const PostHogEvent = {
  WORKFLOW_EDITOR_OPENED: "workflow_editor_opened",
  WORKFLOW_NODE_ADDED: "workflow_node_added",
  WORKFLOW_RUN_DETAILS_VIEWED: "workflow_run_details_viewed",
  RECORDING_PLAYED: "recording_played",
  TRANSCRIPT_VIEWED: "transcript_viewed",
  WEB_CALL_INITIATED: "web_call_initiated",
  SIGNED_IN: "signed_in",
  GITHUB_STAR_CLICKED: "github_star_clicked",
  SLACK_COMMUNITY_CLICKED: "slack_community_clicked",
  HIRE_EXPERT_OPENED: "hire_expert_opened",
  HIRE_EXPERT_SUBMITTED: "hire_expert_submitted",
  TOPUP_REQUEST_OPENED: "topup_request_opened",
  TOPUP_REQUESTED: "topup_requested",
  ENTERPRISE_LEAD_OPENED: "enterprise_lead_opened",
  ENTERPRISE_LEAD_SUBMITTED: "enterprise_lead_submitted",
  HIRE_NUDGE_SHOWN: "hire_nudge_shown",
  HIRE_NUDGE_CLICKED: "hire_nudge_clicked",
  HIRE_NUDGE_DISMISSED: "hire_nudge_dismissed",
  HIRE_NUDGE_EXPIRED: "hire_nudge_expired",
} as const;
