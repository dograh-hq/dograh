import { useTranslations, useMessages } from 'next-intl';

import * as LucideIcons from 'lucide-react';
import { Circle, ExternalLink, type LucideIcon, X } from 'lucide-react';
import { useEffect, useMemo } from 'react';

import type { NodeSpec } from '@/client/types.gen';
import { useNodeSpecs } from '@/components/flow/renderer';
import { Button } from '@/components/ui/button';

import { FlowNode, NodeType } from './types';

/** Resolve a translated node name, falling back to spec.display_name. */
function useNodeName(nodeType: string | undefined, fallback: string): string {
    const t = useTranslations('nodeNames');
    const messages = useMessages() as Record<string, Record<string, string>>;
    if (!nodeType) return fallback;
    const exists = messages?.nodeNames?.[nodeType]?.name;
    if (!exists) return fallback;
    const translated = t(`${nodeType}.name`);
    return translated;
}

function useNodeDesc(nodeType: string | undefined, fallback?: string): string | undefined {
    const t = useTranslations('nodeNames');
    const messages = useMessages() as Record<string, Record<string, string>>;
    if (!nodeType) return fallback;
    const exists = messages?.nodeNames?.[nodeType]?.desc;
    if (!exists) return fallback;
    const translated = t(`${nodeType}.desc`);
    return translated;
}

type AddNodePanelProps = {
    isOpen: boolean;
    onClose: () => void;
    onNodeSelect: (nodeType: NodeType) => void;
    nodes: FlowNode[];
};

// Section ordering and labels. Drives both the category → section title
// mapping and the rendering order.
const SECTION_ORDER: Array<NodeSpec['category']> = [
    'trigger',
    'call_node',
    'global_node',
    'integration',
];

const SECTION_TITLE_KEYS: Record<string, string> = {
    trigger: 'sectionTriggers',
    call_node: 'sectionAgentNodes',
    global_node: 'sectionGlobalNodes',
    integration: 'sectionIntegrations',
};

function resolveIcon(name: string): LucideIcon {
    const icons = LucideIcons as unknown as Record<string, LucideIcon>;
    return icons[name] ?? Circle;
}

function NodeSection({
    title,
    specs,
    onNodeSelect,
    nodeTypeCounts,
}: {
    title: string;
    specs: NodeSpec[];
    onNodeSelect: (nodeType: NodeType) => void;
    nodeTypeCounts: Map<string, number>;
}) {
    if (specs.length === 0) return null;
    return (
        <div className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {title}
            </h3>
            <div className="space-y-2">
                {specs.map((spec) => {
                    const Icon = resolveIcon(spec.icon);
                    const maxInstances = spec.graph_constraints?.max_instances;
                    const disabled =
                        maxInstances !== undefined &&
                        maxInstances !== null &&
                        (nodeTypeCounts.get(spec.name) ?? 0) >= maxInstances;
                    const nodeName = useNodeName(spec.name, spec.display_name);
                    const nodeDesc = useNodeDesc(spec.name, spec.description);
                    return (
                        <Button
                            key={spec.name}
                            variant="outline"
                            className="w-full justify-start p-4 h-auto hover:bg-accent/50 transition-colors"
                            onClick={() => onNodeSelect(spec.name as NodeType)}
                            disabled={disabled}
                            title={
                                disabled
                                    ? `${nodeName} limit reached for this workflow`
                                    : undefined
                            }
                        >
                            <div className="flex items-center">
                                <div className="bg-muted p-2 rounded-lg mr-3 border border-border">
                                    <Icon className="h-5 w-5" />
                                </div>
                                <div className="flex flex-col items-start text-left min-w-0">
                                    <span className="font-medium text-sm">
                                        {nodeName}
                                    </span>
                                    <span className="text-xs text-muted-foreground whitespace-normal">
                                        {nodeDesc}
                                    </span>
                                </div>
                            </div>
                        </Button>
                    );
                })}
            </div>
        </div>
    );
}

export default function AddNodePanel({ isOpen, onNodeSelect, onClose, nodes }: AddNodePanelProps) {
    const t = useTranslations('addNodePanel');
    const { specs } = useNodeSpecs();

    // Group registered specs by category, preserving the SECTION_ORDER.
    // Adding a new node type with a new spec.category just shows up here.
    const sections = useMemo(() => {
        return SECTION_ORDER.map((category) => ({
            title: t(SECTION_TITLE_KEYS[category] ?? category),
            specs: specs.filter((s) => s.category === category),
        }));
    }, [specs, t]);

    const nodeTypeCounts = useMemo(() => {
        const counts = new Map<string, number>();
        nodes.forEach((node) => {
            counts.set(node.type, (counts.get(node.type) ?? 0) + 1);
        });
        return counts;
    }, [nodes]);

    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape' && isOpen) {
                onClose();
            }
        };

        document.addEventListener('keydown', handleKeyDown);
        return () => document.removeEventListener('keydown', handleKeyDown);
    }, [isOpen, onClose]);

    return (
        <div
            className={`fixed z-51 right-0 top-0 h-full w-80 bg-background shadow-lg transform transition-transform duration-300 ease-in-out ${isOpen ? 'translate-x-0' : 'translate-x-full'
                }`}
        >
            <div className="p-4 h-full overflow-y-auto">
                <div className="flex justify-between items-center mb-6">
                    <div className="flex flex-col gap-1">
                        <h2 className="text-lg font-semibold">{t('addNewNode')}</h2>
                        {process.env.NEXT_PUBLIC_SHOW_DOCS_LINKS !== "false" && (
                        <a
                            href="https://docs.dograh.com/voice-agent/introduction"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-muted-foreground hover:text-primary flex items-center gap-1 transition-colors"
                        >
                            <ExternalLink className="w-3 h-3" />
                            {t('viewNodesDocs')}
                        </a>
                        )}
                    </div>
                    <Button variant="ghost" size="icon" onClick={onClose}>
                        <X className="w-5 h-5" />
                    </Button>
                </div>

                <div className="space-y-6">
                    {sections.map(({ title, specs }) => (
                        <NodeSection
                            key={title}
                            title={title}
                            specs={specs}
                            onNodeSelect={onNodeSelect}
                            nodeTypeCounts={nodeTypeCounts}
                        />
                    ))}
                </div>
            </div>
        </div>
    );
}
