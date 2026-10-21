## SDK source

Write one Workflow using literal factory inputs and named node variables. The
server parses this restricted TypeScript; it does not execute JavaScript.
Only imports, const bindings for Workflow/nodes, and wf.edge calls are supported.
No helpers, loops, conditionals, spreads, destructuring, or template interpolation.

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

Adapt this syntax to the supplied conversation plan and factory signatures.
The workflow name is required. Use descriptive node names and declare nodes
before edges. Omit position and unused fields; the server supplies layout and
defaults. Values are literal strings, numbers, booleans, null, arrays, or objects.

Each edge connects two bound node variables, not strings or .id values. Both
label and condition are required, non-empty strings. Use a short, distinct label
for each outgoing branch and a natural-language condition evaluated against the
conversation. Emit one edge per branch, grouped by source node.
