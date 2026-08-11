export interface WorkflowVersionJsonSource {
    workflow_json: unknown;
    workflow_configurations?: unknown;
    template_context_variables?: unknown;
}

export type DiffCellKind = "unchanged" | "added" | "removed";

export interface DiffSegment {
    kind: DiffCellKind;
    text: string;
}

export interface DiffCell {
    kind: DiffCellKind;
    lineNumber: number;
    text: string;
    segments?: DiffSegment[];
}

export interface SideBySideDiffRow {
    left: DiffCell | null;
    right: DiffCell | null;
}

type DiffOperation<Item> =
    | { kind: "unchanged"; value: Item }
    | { kind: "added"; value: Item }
    | { kind: "removed"; value: Item };

const MAX_LINE_DIFF_EDIT_DEPTH = 500;
const MAX_INLINE_DIFF_EDIT_DEPTH = 200;

const WORKFLOW_VISUAL_FIELDS = new Set(["viewport"]);

// React Flow owns these fields. They affect canvas presentation and interaction,
// not how a saved voice agent behaves.
const NODE_VISUAL_FIELDS = new Set([
    "className",
    "connectable",
    "deletable",
    "dragging",
    "draggable",
    "expandParent",
    "extent",
    "focusable",
    "height",
    "hidden",
    "invalid",
    "measured",
    "origin",
    "parentId",
    "position",
    "positionAbsolute",
    "resizing",
    "selectable",
    "selected",
    "style",
    "width",
    "zIndex",
]);

const EDGE_VISUAL_FIELDS = new Set([
    "animated",
    "className",
    "deletable",
    "focusable",
    "hidden",
    "interactionWidth",
    "invalid",
    "markerEnd",
    "markerStart",
    "reconnectable",
    "selectable",
    "selected",
    "style",
    "type",
    "zIndex",
]);

const NODE_DATA_RUNTIME_FIELDS = new Set([
    "hovered_through_edge",
    "invalid",
    "runtime_active",
    "selected_through_edge",
    "validationMessage",
]);

const EDGE_DATA_RUNTIME_FIELDS = new Set([
    "invalid",
    "validationMessage",
]);

/**
 * Sort object keys recursively so equivalent JSON is displayed consistently
 * even if object insertion order changed between saves. General array ordering
 * stays untouched; workflow nodes and edges are separately sorted by ID because
 * their persisted array order is React Flow presentation state.
 */
const sortJsonKeys = (value: unknown): unknown => {
    if (Array.isArray(value)) {
        return value.map(sortJsonKeys);
    }

    if (value && typeof value === "object") {
        return Object.fromEntries(
            Object.entries(value as Record<string, unknown>)
                .sort(([leftKey], [rightKey]) => leftKey.localeCompare(rightKey))
                .map(([key, nestedValue]) => [key, sortJsonKeys(nestedValue)]),
        );
    }

    return value;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
    Boolean(value) && typeof value === "object" && !Array.isArray(value);

const omitFields = (
    value: Record<string, unknown>,
    omittedFields: Set<string>,
): Record<string, unknown> => Object.fromEntries(
    Object.entries(value).filter(([key]) => !omittedFields.has(key)),
);

const compareEntityIds = (left: unknown, right: unknown): number => {
    if (!isRecord(left) || !isRecord(right)) return 0;
    return String(left.id ?? "").localeCompare(String(right.id ?? ""));
};

const normalizeNode = (value: unknown): unknown => {
    if (!isRecord(value)) return value;
    const node = omitFields(value, NODE_VISUAL_FIELDS);
    if (isRecord(node.data)) {
        node.data = omitFields(node.data, NODE_DATA_RUNTIME_FIELDS);
    }
    return node;
};

const normalizeEdge = (value: unknown): unknown => {
    if (!isRecord(value)) return value;
    const edge = omitFields(value, EDGE_VISUAL_FIELDS);
    if (isRecord(edge.data)) {
        edge.data = omitFields(edge.data, EDGE_DATA_RUNTIME_FIELDS);
    }
    return edge;
};

const normalizeWorkflowJson = (value: unknown): unknown => {
    if (!isRecord(value)) return sortJsonKeys(value);

    const workflowJson = omitFields(value, WORKFLOW_VISUAL_FIELDS);
    if (Array.isArray(workflowJson.nodes)) {
        workflowJson.nodes = workflowJson.nodes
            .map(normalizeNode)
            .sort(compareEntityIds);
    }
    if (Array.isArray(workflowJson.edges)) {
        workflowJson.edges = workflowJson.edges
            .map(normalizeEdge)
            .sort(compareEntityIds);
    }

    return sortJsonKeys(workflowJson);
};

/**
 * Version metadata is intentionally excluded: IDs, timestamps, and statuses
 * describe the version record rather than the workflow content being compared.
 */
export const serializeWorkflowVersionJson = (
    version: WorkflowVersionJsonSource,
): string => JSON.stringify(
    {
        workflow_json: normalizeWorkflowJson(version.workflow_json),
        workflow_configurations: sortJsonKeys(version.workflow_configurations ?? null),
        template_context_variables: sortJsonKeys(version.template_context_variables ?? null),
    },
    null,
    2,
);

const frontierValue = (frontier: Map<number, number>, diagonal: number): number =>
    frontier.get(diagonal) ?? Number.NEGATIVE_INFINITY;

/**
 * Myers diff. Memory grows with edit distance instead of the full input-area
 * table used by a traditional LCS implementation. Callers bound the search so
 * a wholesale rewrite cannot lock up the browser.
 */
const buildDiffOperations = <Item>(
    previousItems: Item[],
    selectedItems: Item[],
    maximumDepth: number,
): Array<DiffOperation<Item>> | null => {
    const previousLength = previousItems.length;
    const selectedLength = selectedItems.length;
    const maxDepth = Math.min(previousLength + selectedLength, maximumDepth);
    const trace: Array<Map<number, number>> = [];
    const frontier = new Map<number, number>([[1, 0]]);

    for (let depth = 0; depth <= maxDepth; depth += 1) {
        trace.push(new Map(frontier));

        for (let diagonal = -depth; diagonal <= depth; diagonal += 2) {
            const moveDown = diagonal === -depth || (
                diagonal !== depth &&
                frontierValue(frontier, diagonal - 1) < frontierValue(frontier, diagonal + 1)
            );
            let previousIndex = moveDown
                ? (frontier.get(diagonal + 1) ?? 0)
                : (frontier.get(diagonal - 1) ?? 0) + 1;
            let selectedIndex = previousIndex - diagonal;

            while (
                previousIndex < previousLength &&
                selectedIndex < selectedLength &&
                previousItems[previousIndex] === selectedItems[selectedIndex]
            ) {
                previousIndex += 1;
                selectedIndex += 1;
            }

            frontier.set(diagonal, previousIndex);

            if (previousIndex >= previousLength && selectedIndex >= selectedLength) {
                const operations: Array<DiffOperation<Item>> = [];
                let backtrackPreviousIndex = previousLength;
                let backtrackSelectedIndex = selectedLength;

                for (let backtrackDepth = trace.length - 1; backtrackDepth >= 0; backtrackDepth -= 1) {
                    const backtrackFrontier = trace[backtrackDepth];
                    const backtrackDiagonal = backtrackPreviousIndex - backtrackSelectedIndex;
                    const previousDiagonal = (
                        backtrackDiagonal === -backtrackDepth ||
                        (
                            backtrackDiagonal !== backtrackDepth &&
                            frontierValue(backtrackFrontier, backtrackDiagonal - 1) <
                                frontierValue(backtrackFrontier, backtrackDiagonal + 1)
                        )
                    )
                        ? backtrackDiagonal + 1
                        : backtrackDiagonal - 1;
                    const previousX = backtrackFrontier.get(previousDiagonal) ?? 0;
                    const previousY = previousX - previousDiagonal;

                    while (
                        backtrackPreviousIndex > previousX &&
                        backtrackSelectedIndex > previousY
                    ) {
                        operations.push({
                            kind: "unchanged",
                            value: previousItems[backtrackPreviousIndex - 1],
                        });
                        backtrackPreviousIndex -= 1;
                        backtrackSelectedIndex -= 1;
                    }

                    if (backtrackDepth === 0) {
                        break;
                    }

                    if (backtrackPreviousIndex === previousX) {
                        operations.push({
                            kind: "added",
                            value: selectedItems[backtrackSelectedIndex - 1],
                        });
                        backtrackSelectedIndex -= 1;
                    }
                    else {
                        operations.push({
                            kind: "removed",
                            value: previousItems[backtrackPreviousIndex - 1],
                        });
                        backtrackPreviousIndex -= 1;
                    }
                }

                return operations.reverse();
            }
        }
    }

    return null;
};

/**
 * Linear-time fallback for high-edit-distance inputs. It keeps the common
 * beginning and end surgical while treating the rewritten middle as one hunk.
 */
const buildAffixDiffOperations = <Item>(
    previousItems: Item[],
    selectedItems: Item[],
): Array<DiffOperation<Item>> => {
    let prefixLength = 0;
    while (
        prefixLength < previousItems.length &&
        prefixLength < selectedItems.length &&
        previousItems[prefixLength] === selectedItems[prefixLength]
    ) {
        prefixLength += 1;
    }

    let suffixLength = 0;
    while (
        suffixLength < previousItems.length - prefixLength &&
        suffixLength < selectedItems.length - prefixLength &&
        previousItems[previousItems.length - suffixLength - 1] ===
            selectedItems[selectedItems.length - suffixLength - 1]
    ) {
        suffixLength += 1;
    }

    const operations: Array<DiffOperation<Item>> = [];
    previousItems.slice(0, prefixLength).forEach((value) => {
        operations.push({ kind: "unchanged", value });
    });
    previousItems.slice(prefixLength, previousItems.length - suffixLength).forEach((value) => {
        operations.push({ kind: "removed", value });
    });
    selectedItems.slice(prefixLength, selectedItems.length - suffixLength).forEach((value) => {
        operations.push({ kind: "added", value });
    });
    previousItems.slice(previousItems.length - suffixLength).forEach((value) => {
        operations.push({ kind: "unchanged", value });
    });

    return operations;
};

const buildBoundedDiffOperations = <Item>(
    previousItems: Item[],
    selectedItems: Item[],
    maximumDepth: number,
): Array<DiffOperation<Item>> => buildDiffOperations(
    previousItems,
    selectedItems,
    maximumDepth,
) ?? buildAffixDiffOperations(previousItems, selectedItems);

const splitLines = (value: string): string[] => value.length === 0 ? [] : value.split("\n");

// Preserve whitespace and punctuation as tokens so a one-word prompt edit can
// be highlighted without painting the entire (potentially very long) JSON line.
const splitInlineTokens = (value: string): string[] => value
    .split(/(\s+|[.,!?;:()[\]{}"'`/\\<>+=*–—-])/g)
    .filter((token) => token.length > 0);

const appendSegment = (
    segments: DiffSegment[],
    kind: DiffCellKind,
    text: string,
) => {
    const previousSegment = segments.at(-1);
    if (previousSegment?.kind === kind) {
        previousSegment.text += text;
        return;
    }
    segments.push({ kind, text });
};

const buildInlineDiff = (
    previous: string,
    selected: string,
): { left: DiffSegment[]; right: DiffSegment[] } => {
    const operations = buildBoundedDiffOperations(
        splitInlineTokens(previous),
        splitInlineTokens(selected),
        MAX_INLINE_DIFF_EDIT_DEPTH,
    );
    const left: DiffSegment[] = [];
    const right: DiffSegment[] = [];

    for (const operation of operations) {
        if (operation.kind === "unchanged") {
            appendSegment(left, "unchanged", operation.value);
            appendSegment(right, "unchanged", operation.value);
        }
        else if (operation.kind === "removed") {
            appendSegment(left, "removed", operation.value);
        }
        else {
            appendSegment(right, "added", operation.value);
        }
    }

    return { left, right };
};

/**
 * Pair removals and additions within each changed hunk so both sides share the
 * same CSS-grid row and remain aligned even when long JSON lines wrap.
 */
export const buildSideBySideDiffRows = (
    previous: string,
    selected: string,
): SideBySideDiffRow[] => {
    const operations = buildBoundedDiffOperations(
        splitLines(previous),
        splitLines(selected),
        MAX_LINE_DIFF_EDIT_DEPTH,
    );
    const rows: SideBySideDiffRow[] = [];
    let previousLineNumber = 1;
    let selectedLineNumber = 1;
    let operationIndex = 0;

    while (operationIndex < operations.length) {
        const operation = operations[operationIndex];
        if (operation.kind === "unchanged") {
            rows.push({
                left: {
                    kind: "unchanged",
                    lineNumber: previousLineNumber,
                    text: operation.value,
                },
                right: {
                    kind: "unchanged",
                    lineNumber: selectedLineNumber,
                    text: operation.value,
                },
            });
            previousLineNumber += 1;
            selectedLineNumber += 1;
            operationIndex += 1;
            continue;
        }

        const removed: DiffCell[] = [];
        const added: DiffCell[] = [];
        while (
            operationIndex < operations.length &&
            operations[operationIndex].kind !== "unchanged"
        ) {
            const changedOperation = operations[operationIndex];
            if (changedOperation.kind === "removed") {
                removed.push({
                    kind: "removed",
                    lineNumber: previousLineNumber,
                    text: changedOperation.value,
                });
                previousLineNumber += 1;
            }
            else if (changedOperation.kind === "added") {
                added.push({
                    kind: "added",
                    lineNumber: selectedLineNumber,
                    text: changedOperation.value,
                });
                selectedLineNumber += 1;
            }
            operationIndex += 1;
        }

        const hunkLength = Math.max(removed.length, added.length);
        for (let hunkIndex = 0; hunkIndex < hunkLength; hunkIndex += 1) {
            const left = removed[hunkIndex] ?? null;
            const right = added[hunkIndex] ?? null;
            if (left && right) {
                const inlineDiff = buildInlineDiff(left.text, right.text);
                left.segments = inlineDiff.left;
                right.segments = inlineDiff.right;
            }
            else if (left) {
                left.segments = [{ kind: "removed", text: left.text }];
            }
            else if (right) {
                right.segments = [{ kind: "added", text: right.text }];
            }
            rows.push({
                left,
                right,
            });
        }
    }

    return rows;
};
