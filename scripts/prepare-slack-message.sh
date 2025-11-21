#!/bin/bash
set -e  # Exit on any error

# Debug logging function
log_debug() {
    echo "[DEBUG] $1" >&2
}

log_debug "Script started with arguments count: $#"

# Get discussion data from environment variables or command line arguments
if [ -n "$DISCUSSION_TITLE" ] && [ -n "$DISCUSSION_BODY" ] && [ -n "$DISCUSSION_URL" ]; then
    # Use environment variables (preferred for GitHub Actions)
    TITLE="$DISCUSSION_TITLE"
    BODY="$DISCUSSION_BODY"
    URL="$DISCUSSION_URL"
    log_debug "Using environment variables"
else
    # Fall back to command line arguments
    TITLE="$1"
    BODY="$2"
    URL="$3"
    log_debug "Using command line arguments"
fi

if [ -z "$TITLE" ] || [ -z "$BODY" ] || [ -z "$URL" ]; then
    log_debug "Missing required data"
    echo "Usage: Set DISCUSSION_TITLE, DISCUSSION_BODY, DISCUSSION_URL env vars or pass as args: $0 <title> <body> <url>" >&2
    exit 1
fi

log_debug "Title length: ${#TITLE}"
log_debug "Body length: ${#BODY}"
log_debug "URL: $URL"

# Function to escape JSON strings
escape_json() {
    # Use python for reliable JSON escaping
    python3 -c "
import json
import sys
text = sys.stdin.read().rstrip('\n\r')  # Remove trailing newline and carriage return from stdin
print(json.dumps(text)[1:-1], end='')  # Remove outer quotes
" <<< "$1"
}

# Function to convert GitHub markdown to Slack mrkdwn format
convert_to_slack_format() {
    local text="$1"
    log_debug "Starting markdown conversion"
    
    # First remove all carriage returns to prevent formatting issues
    log_debug "Removing carriage returns"
    text=$(echo "$text" | tr -d '\r')
    
    # Convert headers (## Header -> *Header*)
    log_debug "Converting headers"
    text=$(echo "$text" | sed -E 's/^#{1,6}[[:space:]]+(.+)$/*\1*/g')
    
    # Handle code blocks carefully - leave them as triple backticks
    log_debug "Preserving code blocks"
    
    # Convert inline code (`code` -> `code`)
    log_debug "Converting inline code"
    text=$(echo "$text" | sed -E 's/`([^`\n]+)`/`\1`/g')
    
    # Convert HTML breaks to newlines
    log_debug "Converting HTML breaks"
    text=$(echo "$text" | sed -E 's/<br[[:space:]]*\/?>/\n/gi')
    
    # Convert markdown links [text](url) to Slack format <url|text>
    log_debug "Converting markdown links"
    text=$(echo "$text" | sed -E 's/\[([^]]+)\]\(([^)]+)\)/<\2|\1>/g')
    
    # Convert bold text to Slack format
    log_debug "Converting bold text"
    text=$(echo "$text" | sed -E 's/\*\*([^*]+)\*\*/*\1*/g')
    
    # Clean up excessive newlines
    log_debug "Cleaning up newlines"
    text=$(echo "$text" | sed -E 's/\n\n\n+/\n\n/g')
    
    log_debug "Markdown conversion completed"
    echo "$text"
}

# Convert body to Slack format
log_debug "Converting body to Slack format"
SLACK_BODY=$(convert_to_slack_format "$BODY")
log_debug "Slack body length after conversion: ${#SLACK_BODY}"

# Truncate if too long (Slack limit)
MAX_LENGTH=2500
if [ ${#SLACK_BODY} -gt $MAX_LENGTH ]; then
    log_debug "Truncating body from ${#SLACK_BODY} to $MAX_LENGTH characters"
    SLACK_BODY="${SLACK_BODY:0:$MAX_LENGTH}...\n\n_View full announcement on GitHub_"
fi

# Escape JSON strings
log_debug "Escaping JSON strings"
TITLE_ESCAPED=$(escape_json "$TITLE")
SLACK_BODY_ESCAPED=$(escape_json "$SLACK_BODY")
URL_ESCAPED=$(escape_json "$URL")

log_debug "Title escaped length: ${#TITLE_ESCAPED}"
log_debug "Slack body escaped length: ${#SLACK_BODY_ESCAPED}"

# Create Slack payload JSON
log_debug "Creating Slack payload JSON"
PAYLOAD=$(cat <<EOF
{
  "text": "New Announcement: $TITLE_ESCAPED",
  "blocks": [
    {
      "type": "header",
      "text": {
        "type": "plain_text",
        "text": "📢 New Announcement",
        "emoji": true
      }
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "*$TITLE_ESCAPED*"
      }
    },
    {
      "type": "divider"
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "$SLACK_BODY_ESCAPED"
      }
    },
    {
      "type": "divider"
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "<$URL_ESCAPED|View full discussion on GitHub>"
      }
    }
  ]
}
EOF
)

log_debug "Payload created successfully, length: ${#PAYLOAD}"

# Validate JSON syntax
if echo "$PAYLOAD" | jq empty 2>/dev/null; then
    log_debug "JSON payload is valid"
else
    log_debug "WARNING: JSON payload may be invalid"
fi

# Always output to stdout - let the YAML workflow handle GITHUB_OUTPUT
log_debug "Outputting payload to stdout"
log_debug "Payload first 100 chars: ${PAYLOAD:0:100}..."
echo "$PAYLOAD"

log_debug "Script completed successfully"