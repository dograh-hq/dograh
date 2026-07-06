'use client';

import { Button } from '@/components/ui/button';

const CATEGORIES = [
    { value: '', label: 'All' },
    { value: 'mcp_direct', label: 'MCP Servers' },
    { value: 'dify_workflow', label: 'Dify' },
    { value: 'http_api', label: 'HTTP API' },
];

interface CategoryFilterProps {
    selected: string;
    onSelect: (category: string) => void;
}

export function CategoryFilter({ selected, onSelect }: CategoryFilterProps) {
    return (
        <div className="flex gap-2 flex-wrap">
            {CATEGORIES.map((cat) => (
                <Button
                    key={cat.value}
                    variant={selected === cat.value ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => onSelect(cat.value)}
                >
                    {cat.label}
                </Button>
            ))}
        </div>
    );
}
