"use client";

import { Pencil, Phone, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/lib/auth";

import { getWorkflowsApiV1WorkflowFetchGet } from "@/client/sdk.gen";
import { client } from "@/client/client.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface DIDMapping {
  id: number;
  organization_id: number;
  did_number: string;
  workflow_id: number;
  is_active: boolean;
  created_at: string;
}

interface Workflow {
  id: number;
  name: string;
}

export default function DIDWorkflowMappingPage() {
  const { user, loading: authLoading } = useAuth();
  const [mappings, setMappings] = useState<DIDMapping[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);

  // Dialog state
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingMapping, setEditingMapping] = useState<DIDMapping | null>(null);
  const [didNumber, setDidNumber] = useState("");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState("");
  const [saving, setSaving] = useState(false);

  // Delete confirm state
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deletingMapping, setDeletingMapping] = useState<DIDMapping | null>(null);

  async function loadData() {
    setLoading(true);
    try {
      const [mappingsRes, workflowsRes] = await Promise.all([
        client.get<DIDMapping[]>({ url: "/api/v1/did-mappings/" }),
        getWorkflowsApiV1WorkflowFetchGet({ query: { status: "active" } }),
      ]);

      if (mappingsRes.data) setMappings(mappingsRes.data);
      if (workflowsRes.data) {
        setWorkflows(
          (workflowsRes.data as Workflow[]).map((w: Workflow) => ({
            id: w.id,
            name: w.name,
          }))
        );
      }
    } catch {
      toast.error("Failed to load data");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (authLoading || !user) return;
    loadData();
  }, [authLoading, user]);

  function openCreateDialog() {
    setEditingMapping(null);
    setDidNumber("");
    setSelectedWorkflowId("");
    setDialogOpen(true);
  }

  function openEditDialog(mapping: DIDMapping) {
    setEditingMapping(mapping);
    setDidNumber(mapping.did_number);
    setSelectedWorkflowId(String(mapping.workflow_id));
    setDialogOpen(true);
  }

  async function handleSave() {
    if (!didNumber.trim()) {
      toast.error("DID number is required");
      return;
    }
    if (!selectedWorkflowId) {
      toast.error("Please select a workflow");
      return;
    }

    setSaving(true);
    try {
      if (editingMapping) {
        await client.put({
          url: `/api/v1/did-mappings/${editingMapping.id}`,
          body: {
            did_number: didNumber.trim(),
            workflow_id: Number(selectedWorkflowId),
          },
          headers: { "Content-Type": "application/json" },
        });
        toast.success("DID mapping updated");
      } else {
        await client.post({
          url: "/api/v1/did-mappings/",
          body: {
            did_number: didNumber.trim(),
            workflow_id: Number(selectedWorkflowId),
          },
          headers: { "Content-Type": "application/json" },
        });
        toast.success("DID mapping created");
      }
      setDialogOpen(false);
      await loadData();
    } catch (err: unknown) {
      const msg =
        (err as { error?: { detail?: string } })?.error?.detail ||
        (editingMapping ? "Failed to update mapping" : "Failed to create mapping");
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleActive(mapping: DIDMapping) {
    try {
      await client.put({
        url: `/api/v1/did-mappings/${mapping.id}`,
        body: { is_active: !mapping.is_active },
        headers: { "Content-Type": "application/json" },
      });
      toast.success(mapping.is_active ? "Mapping disabled" : "Mapping enabled");
      await loadData();
    } catch {
      toast.error("Failed to update mapping");
    }
  }

  async function handleDelete() {
    if (!deletingMapping) return;
    try {
      await client.delete({ url: `/api/v1/did-mappings/${deletingMapping.id}` });
      toast.success("DID mapping deleted");
      setDeleteDialogOpen(false);
      setDeletingMapping(null);
      await loadData();
    } catch {
      toast.error("Failed to delete mapping");
    }
  }

  function getWorkflowName(workflowId: number) {
    return workflows.find((w) => w.id === workflowId)?.name ?? `#${workflowId}`;
  }

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Phone className="h-6 w-6" />
            DID Routing
          </h1>
          <p className="text-muted-foreground mt-1">
            Map inbound phone numbers (DIDs) to specific voice agent workflows.
            When a call arrives on a DID, it is automatically routed to the
            mapped workflow.
          </p>
        </div>
        <Button onClick={openCreateDialog}>
          <Plus className="h-4 w-4 mr-2" />
          Add Mapping
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>DID Mappings</CardTitle>
          <CardDescription>
            Each DID number is mapped to one workflow. DID mapping takes
            priority over the default inbound workflow configured in Telephony
            settings.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="py-8 text-center text-muted-foreground">
              Loading...
            </div>
          ) : mappings.length === 0 ? (
            <div className="py-8 text-center text-muted-foreground">
              No DID mappings yet. Click &ldquo;Add Mapping&rdquo; to create
              one.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>DID Number</TableHead>
                  <TableHead>Workflow</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {mappings.map((mapping) => (
                  <TableRow key={mapping.id}>
                    <TableCell className="font-mono font-medium">
                      {mapping.did_number}
                    </TableCell>
                    <TableCell>{getWorkflowName(mapping.workflow_id)}</TableCell>
                    <TableCell>
                      <Badge
                        variant={mapping.is_active ? "default" : "secondary"}
                        className="cursor-pointer"
                        onClick={() => handleToggleActive(mapping)}
                      >
                        {mapping.is_active ? "Active" : "Inactive"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right space-x-2">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => openEditDialog(mapping)}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => {
                          setDeletingMapping(mapping);
                          setDeleteDialogOpen(true);
                        }}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Create / Edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editingMapping ? "Edit DID Mapping" : "Add DID Mapping"}
            </DialogTitle>
            <DialogDescription>
              Map a phone number to the workflow that should handle its inbound
              calls.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="did-number">DID Number</Label>
              <Input
                id="did-number"
                placeholder="+1234567890"
                value={didNumber}
                onChange={(e) => setDidNumber(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Enter the number exactly as Asterisk reports it (e.g.
                +1234567890).
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="workflow-select">Workflow</Label>
              <Select
                value={selectedWorkflowId}
                onValueChange={setSelectedWorkflowId}
              >
                <SelectTrigger id="workflow-select">
                  <SelectValue placeholder="Select a workflow" />
                </SelectTrigger>
                <SelectContent>
                  {workflows.map((w) => (
                    <SelectItem key={w.id} value={String(w.id)}>
                      {w.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDialogOpen(false)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? "Saving..." : editingMapping ? "Update" : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirm dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete DID Mapping</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete the mapping for{" "}
              <span className="font-mono font-semibold">
                {deletingMapping?.did_number}
              </span>
              ? This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteDialogOpen(false)}
            >
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
