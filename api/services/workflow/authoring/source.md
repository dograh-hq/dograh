## Allowed source shape

The parser accepts a constrained TypeScript DSL. At the top level, use only:

```text
import ... from "...";
const <var> = <initializer>;
wf.edge(<src>, <tgt>, { label, condition });
```

An initializer is one of:

```text
new Workflow({ name: "..." })
wf.addTyped(<factory>({ ...fields }) [, { position: [x, y] }])
wf.add({ type: "<nodeType>", ...fields [, position: [x, y]] })
```

The ellipses and optional brackets above describe the grammar, not literal code.
No functions, arrow functions, loops, conditionals, ternaries, spreads,
destructuring, template interpolation, exports, or `.map`/`.forEach`. Values must
be plain literals: strings, numbers, booleans, null, arrays/objects of those.
Declare exactly one `new Workflow(...)`; its required `name` is the display name.

### Edges

- Source and target are bare variables already bound by `wf.addTyped(...)` or
  `wf.add(...)`, not strings, `.id`, or inline factories.
- `label` is a short tag (at most four words) shown in call logs.
- `condition` is a clear natural-language predicate evaluated against the live
  conversation, such as "caller confirmed interest in a demo".
- Both fields are required and must be non-empty strings. Edges are directional;
  emit one call per outgoing branch, after all node bindings, grouped by source.

A minimal syntax example (adapt the content using node schemas and the guide):

```typescript
import { Workflow } from "@dograh/sdk";
import { startCall, endCall } from "@dograh/sdk/typed";

const wf = new Workflow({ name: "Greeting" });
const greet = wf.addTyped(startCall({ name: "Greet", prompt: "Greet the caller." }));
const done = wf.addTyped(endCall({ name: "Done", prompt: "Say goodbye." }));
wf.edge(greet, done, {
    label: "wrap up",
    condition: "the caller acknowledged the greeting and is ready to end"
});
```

### Fields and style

- `data.name` is the canonical node identifier. Use descriptive names because
  generated variable names and call logs derive from them.
- Reference properties take identifiers from the resource catalogs, not human
  names. Property types such as `tool_refs` and `credential_ref` describe reference
  categories; the node schema defines the actual field names.
- `mention_textarea` fields accept `{{template_variables}}` resolved at call time
  from caller context, pre-call fetching, or earlier extraction passes. These are
  literal prompt text, not JavaScript template interpolation.
- Prefer `wf.addTyped(factory({ ... }))`. Include fields that differ from their
  defaults; the parser reapplies defaults. Omit `position` so the server handles
  layout. Declare nodes in call-flow order, followed by the edges.
