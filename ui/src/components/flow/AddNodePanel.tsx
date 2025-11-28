import { Globe, Headset, OctagonX, Play, X } from 'lucide-react';
import { useEffect } from 'react';

import { Button } from '@/components/ui/button';

import { NodeType } from './types';

type AddNodePanelProps = {
    isOpen: boolean;
    onClose: () => void;
    onNodeSelect: (nodeType: NodeType) => void;
};

const NODE_TYPES = [
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

const GLOBAL_NODE_TYPES = [
    {
        type: NodeType.GLOBAL_NODE,
        label: 'Global Node',
        description: 'Create a global node',
        icon: Globe
    }
]

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
            <div className="p-4">
                <div className="flex justify-between items-center mb-4">
                    <h2 className="text-lg font-semibold">Add New Node</h2>
                    <Button variant="ghost" size="icon" onClick={onClose}>
                        <X className="w-5 h-5" />
                    </Button>
                </div>

                <h1 className="text-sm text-muted-foreground mb-2">Agent Nodes</h1>

                <div className="space-y-2">
                    {NODE_TYPES.map((node) => (
                        <Button
                            key={node.type}
                            variant="outline"
                            className="w-full justify-start p-4 h-auto"
                            onClick={() => onNodeSelect(node.type)}
                        >
                            <div className="flex items-center">
                                <div className="bg-muted p-2 rounded-lg mr-3 border border-border">
                                    <node.icon className="h-6 w-6" />
                                </div>
                                <div className="flex flex-col items-start">
                                    <span className="font-medium">{node.label}</span>
                                    <span className="text-sm text-muted-foreground">{node.description}</span>
                                </div>
                            </div>
                        </Button>
                    ))}
                </div>

                <h1 className="text-sm text-muted-foreground mb-2">Global Nodes</h1>

                <div className="space-y-2">
                    {GLOBAL_NODE_TYPES.map((node) => (
                        <Button
                            variant="outline"
                            className="w-full justify-start p-4 h-auto"
                            key={node.type}
                            onClick={() => onNodeSelect(node.type)}
                        >
                            <div className="flex items-center">
                                <div className="bg-muted p-2 rounded-lg mr-3 border border-border">
                                    <node.icon className="h-6 w-6" />
                                </div>
                                <div className="flex flex-col items-start">
                                    <span className="font-medium">{node.label}</span>
                                    <span className="text-sm text-muted-foreground">{node.description}</span>
                                </div>
                            </div>
                        </Button>
                    ))}
                </div>
            </div>
        </div>
    );
}
