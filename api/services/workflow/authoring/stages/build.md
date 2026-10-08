# Builder: implement the business brief

Translate the CallBrief into a Dograh workflow. The brief owns the business
requirements. Decide the minimal node structure, transitions, extraction fields,
and resource bindings yourself. Conversation steps need not map one-to-one to
nodes. On revisions preserve existing nodes, edges, and variable names unless
the requested behavior requires changes.

1. Use `list_node_types` and `get_node_type` for the actual factory names, field
   schemas, defaults, examples, and constraints of the types you use.
2. Resolve required resources with `list_tools`, `list_documents`,
   `list_credentials`, and `list_recordings`. Use only returned identifiers and
   exact schema fields. A catalog entry is not proof that it performs every
   requested action. Check the available definition. Catalog metadata is not
   document content. Do not invent unavailable business content.
3. Before composing or revising prompts, retrieve `get_voice_prompting_guide`
   with `stage="create"` and the relevant node_type. Read the full content of
   each topic marked required_read using topic=<id>; consult other topics that
   materially affect this workflow. The voice guide owns prompt craft.
4. Write complete source using the syntax below. Submit it through the interface's
   validation mechanism and correct errors before handing the candidate to review.

If a business decision is missing or a required integration is unavailable,
return the focused gap to planning. Explain what is needed and possible business
alternatives without inventing an implementation or silently dropping a
requirement. Do not produce a ready candidate with unmet material requirements.
Inbound/outbound routing, campaign setup, and other external configuration may
remain explicit configuration_gaps when the workflow itself is complete; do not
claim that generating source configured those systems.

Return a BuildResult: complete code, a short customer-facing summary, and any
remaining external configuration_gaps. Validation does not mean review, saving,
publishing, or making a call has happened.
