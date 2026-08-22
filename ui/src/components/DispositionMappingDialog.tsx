"use client";

import { Plus, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDispositionCodes } from "@/hooks/useDispositionCodes";

type Row = {
  /** The Dograh disposition being translated. Fixed for a built-in row. */
  source: string;
  /** What this organization calls it. Seeded with `source`. */
  target: string;
  /** Built-in rows come from the platform catalog and cannot be renamed. */
  builtIn: boolean;
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Overrides currently stored, keyed by Dograh disposition. */
  mapping: Record<string, string>;
  onSave: (mapping: Record<string, string>) => void;
};

/**
 * Build the editor's rows: the platform's built-in dispositions seeded with
 * themselves, plus a row for every override on a disposition not in that list.
 *
 * Those extra rows matter because a workflow's end-call tool passes the model's
 * own free-text reason straight through as the disposition, so an override can
 * legitimately name something no enum contains. Dropping such a row here would
 * silently discard the override on save.
 */
function buildRows(systemCodes: string[], mapping: Record<string, string>): Row[] {
  const builtIn = systemCodes.map((source) => ({
    source,
    target: mapping[source] ?? source,
    builtIn: true,
  }));
  const known = new Set(systemCodes);
  const custom = Object.entries(mapping)
    .filter(([source]) => !known.has(source))
    .map(([source, target]) => ({ source, target, builtIn: false }));
  return [...builtIn, ...custom];
}

/**
 * Only the rows that changed something. An identity row carries no information:
 * a disposition mapped to itself behaves exactly as an unmapped one, and storing
 * it would freeze the mapping against today's disposition catalog.
 */
function toMapping(rows: Row[]): Record<string, string> {
  const mapping: Record<string, string> = {};
  for (const row of rows) {
    const source = row.source.trim();
    const target = row.target.trim();
    if (!source || !target || source === target) continue;
    mapping[source] = target;
  }
  return mapping;
}

export function DispositionMappingDialog({
  open,
  onOpenChange,
  mapping,
  onSave,
}: Props) {
  const { systemCodes, isLoading } = useDispositionCodes();
  const [rows, setRows] = useState<Row[]>([]);

  // Reseed each time the dialog opens so a cancelled edit is discarded and a
  // newly loaded catalog is picked up.
  useEffect(() => {
    if (!open || isLoading) return;
    setRows(buildRows(systemCodes, mapping));
    // `mapping` is only read when the dialog opens; re-seeding on every parent
    // render would throw away what the user is typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isLoading, systemCodes]);

  const overrideCount = useMemo(
    () => Object.keys(toMapping(rows)).length,
    [rows],
  );

  function updateRow(index: number, patch: Partial<Row>) {
    setRows((current) =>
      current.map((row, position) =>
        position === index ? { ...row, ...patch } : row,
      ),
    );
  }

  function addRow() {
    setRows((current) => [
      ...current,
      { source: "", target: "", builtIn: false },
    ]);
  }

  function removeRow(index: number) {
    setRows((current) => current.filter((_row, position) => position !== index));
  }

  function handleSave() {
    onSave(toMapping(rows));
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Configure disposition mapping</DialogTitle>
          <DialogDescription>
            Each Dograh disposition is sent as your own code wherever a call
            outcome is reported &mdash; webhooks, run filters, reports, and
            external PBX write-backs. Leave a row unchanged to send the
            disposition as-is.
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <p className="text-sm text-muted-foreground">
            Loading dispositions...
          </p>
        ) : (
          <>
            <div className="grid grid-cols-[1fr_1fr_auto] items-center gap-x-3 gap-y-1 px-1 text-xs font-medium text-muted-foreground">
              <span>Dograh disposition</span>
              <span>Your code</span>
              <span className="w-8" />
            </div>

            <div className="max-h-[50vh] space-y-2 overflow-y-auto px-1 pb-1">
              {rows.map((row, index) => {
                const changed =
                  row.target.trim() !== "" &&
                  row.target.trim() !== row.source.trim();
                return (
                  <div
                    key={row.builtIn ? row.source : `custom-${index}`}
                    className="grid grid-cols-[1fr_1fr_auto] items-center gap-3"
                  >
                    {row.builtIn ? (
                      <Label
                        htmlFor={`disposition-target-${index}`}
                        className="truncate font-mono text-xs font-normal"
                        title={row.source}
                      >
                        {row.source}
                      </Label>
                    ) : (
                      <Input
                        aria-label="Dograh disposition"
                        value={row.source}
                        onChange={(event) =>
                          updateRow(index, { source: event.target.value })
                        }
                        placeholder="e.g. no_medicare_card"
                        className="font-mono text-xs"
                      />
                    )}
                    <Input
                      id={`disposition-target-${index}`}
                      aria-label={`Code for ${row.source || "new disposition"}`}
                      value={row.target}
                      onChange={(event) =>
                        updateRow(index, { target: event.target.value })
                      }
                      placeholder={row.source || "Your code"}
                      className="font-mono text-xs"
                    />
                    {row.builtIn ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8"
                        // Disabled rather than hidden so the column does not
                        // reflow as rows are edited.
                        disabled={!changed}
                        onClick={() => updateRow(index, { target: row.source })}
                        title="Reset to the Dograh disposition"
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        <span className="sr-only">
                          Reset {row.source} to its default
                        </span>
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8"
                        onClick={() => removeRow(index)}
                        title="Remove this disposition"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        <span className="sr-only">
                          Remove {row.source || "this disposition"}
                        </span>
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>

            <div className="flex items-center justify-between gap-4">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={addRow}
              >
                <Plus className="mr-2 h-3.5 w-3.5" />
                Add disposition
              </Button>
              <p className="text-xs text-muted-foreground">
                {overrideCount === 0
                  ? "No overrides — every disposition is sent as-is."
                  : `${overrideCount} override${overrideCount === 1 ? "" : "s"}`}
              </p>
            </div>
          </>
        )}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button type="button" onClick={handleSave} disabled={isLoading}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
