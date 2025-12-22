import { Globe, Headset, Link2, LucideIcon, OctagonX, Play, Webhook, X } from 'lucide-react';
import { useEffect } from 'react';

import { Button } from '@/components/ui/button';

import { NodeType } from './types';

type NodeTypeConfig = {
    type: NodeType;
    label: string;
    description: string;
    icon: LucideIcon;
};

type AddNodePanelProps = {
    isOpen: boolean;
    onClose: () => void;
    onNodeSelect: (nodeType: NodeType) => void;
};

const NODE_TYPES: NodeTypeConfig[] = [
    {
        type: NodeType.START_CALL,
        label: 'Start Call',
        description: 'Create a start call node',
        icon: Play
    },
    {
        type: NodeType.AGENT_NODE,
        label: 'Agent Node',
        description: 'Create an agent node',
        icon: Headset
    },
    {
        type: NodeType.END_CALL,
        label: 'End Call',
        description: 'Create an end call node',
        icon: OctagonX
    }
];

const GLOBAL_NODE_TYPES: NodeTypeConfig[] = [
    {
        type: NodeType.GLOBAL_NODE,
        label: 'Global Node',
        description: 'Create a global node',
        icon: Globe
    }
];

const TRIGGER_NODE_TYPES: NodeTypeConfig[] = [
    {
        type: NodeType.TRIGGER,
        label: 'API Trigger',
        description: 'Enable API-based call triggering',
        icon: Webhook
    }
];

const WEBHOOK_NODE_TYPES: NodeTypeConfig[] = [
    {
        type: NodeType.WEBHOOK,
        label: 'Webhook',
        description: 'Send HTTP request after workflow completion',
        icon: Link2
    }
];

function NodeSection({
    title,
    nodes,
    onNodeSelect
}: {
    title: string;
    nodes: NodeTypeConfig[];
    onNodeSelect: (nodeType: NodeType) => void;
}) {
    return (
        <div className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {title}
            </h3>
            <div className="space-y-2">
                {nodes.map((node) => (
                    <Button
                        key={node.type}
                        variant="outline"
                        className="w-full justify-start p-4 h-auto hover:bg-accent/50 transition-colors"
                        onClick={() => onNodeSelect(node.type)}
                    >
                        <div className="flex items-center">
                            <div className="bg-muted p-2 rounded-lg mr-3 border border-border">
                                <node.icon className="h-5 w-5" />
                            </div>
                            <div className="flex flex-col items-start text-left min-w-0">
                                <span className="font-medium text-sm">{node.label}</span>
                                <span className="text-xs text-muted-foreground whitespace-normal">
                                    {node.description}
                                </span>
                            </div>
                        </div>
                    </Button>
                ))}
            </div>
        </div>
    );
}

export default function AddNodePanel({ isOpen, onNodeSelect, onClose }: AddNodePanelProps) {
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
                    <h2 className="text-lg font-semibold">Add New Node</h2>
                    <Button variant="ghost" size="icon" onClick={onClose}>
                        <X className="w-5 h-5" />
                    </Button>
                </div>

                <div className="space-y-6">
                    <NodeSection
                        title="Triggers"
                        nodes={TRIGGER_NODE_TYPES}
                        onNodeSelect={onNodeSelect}
                    />

                    <NodeSection
                        title="Agent Nodes"
                        nodes={NODE_TYPES}
                        onNodeSelect={onNodeSelect}
                    />

                    <NodeSection
                        title="Global Nodes"
                        nodes={GLOBAL_NODE_TYPES}
                        onNodeSelect={onNodeSelect}
                    />

                    <NodeSection
                        title="Integrations"
                        nodes={WEBHOOK_NODE_TYPES}
                        onNodeSelect={onNodeSelect}
                    />
                </div>
            </div>
        </div>
    );
}
