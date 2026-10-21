'use client';

import '@xyflow/react/dist/style.css';

import { Background, Controls, ReactFlow } from '@xyflow/react';
import { useTheme } from 'next-themes';
import { useMemo, useState } from 'react';

import type { BuilderProposal } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

type GraphNode = { id: string; type: string; position?: { x: number; y: number }; data: { name?: string; prompt?: string } };
type GraphEdge = { id: string; source: string; target: string; data?: { label?: string } };

export function WorkflowPreview({ proposal, onSave, disabled, saving }: {
    proposal: BuilderProposal; onSave: () => void; disabled: boolean; saving: boolean;
}) {
    const [selected, setSelected] = useState<GraphNode | null>(null);
    const { resolvedTheme } = useTheme();
    const graph = proposal.workflow as { nodes: GraphNode[]; edges: GraphEdge[] };
    const nodes = useMemo(() => graph.nodes.map((node, index) => ({ id: node.id,
        position: node.position ?? { x: (index % 3) * 240, y: Math.floor(index / 3) * 160 },
        data: { label: <div className="font-medium">{node.data.name || 'Conversation step'}</div> },
        style: { width: 190, borderRadius: 12, padding: 14, background: 'var(--background)', color: 'var(--foreground)', borderColor: 'var(--border)' },
    })), [graph.nodes]);
    const edges = useMemo(() => graph.edges.map(edge => ({ id: edge.id, source: edge.source, target: edge.target, label: edge.data?.label, type: 'smoothstep' })), [graph.edges]);
    return <div className="flex h-full min-h-[550px] flex-col overflow-hidden rounded-2xl border bg-background">
        <div className="border-b p-5"><div className="mb-2 flex items-center justify-between gap-3"><h2 className="font-semibold">{proposal.name}</h2><span className="rounded-full bg-emerald-500/10 px-2 py-1 text-xs text-emerald-700 dark:text-emerald-400">Validated</span></div><p className="text-sm text-muted-foreground">{proposal.summary}</p></div>
        <Tabs defaultValue="graph" className="flex min-h-0 flex-1 flex-col">
            <TabsList className="mx-5 mt-4 w-fit"><TabsTrigger value="graph">Workflow</TabsTrigger><TabsTrigger value="code">Code</TabsTrigger></TabsList>
            <TabsContent value="graph" className="relative min-h-[380px] flex-1">
                <ReactFlow key={proposal.code} colorMode={resolvedTheme === 'dark' ? 'dark' : 'light'} nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.3 }} nodesDraggable={false} nodesConnectable={false}
                    onNodeClick={(_, node) => setSelected(graph.nodes.find(item => item.id === node.id) ?? null)}><Background /><Controls showInteractive={false} /></ReactFlow>
            </TabsContent>
            <TabsContent value="code" className="min-h-[380px] flex-1 overflow-auto p-5"><pre className="whitespace-pre-wrap break-words text-xs leading-relaxed">{proposal.code}</pre></TabsContent>
        </Tabs>
        {selected && <div className="max-h-44 overflow-auto border-t px-5 py-3"><h3 className="mb-1 text-sm font-medium">{selected.data.name}</h3><p className="whitespace-pre-wrap text-sm text-muted-foreground">{selected.data.prompt || 'This node controls the call flow.'}</p></div>}
        <div className="flex items-center justify-between gap-4 border-t p-5"><p className="text-xs text-muted-foreground">Save a draft to configure and test in the editor.</p><Button onClick={onSave} disabled={disabled || saving}>{saving ? 'Saving…' : 'Save draft'}</Button></div>
    </div>;
}
