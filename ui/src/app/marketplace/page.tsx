'use client';

import { useEffect, useState } from 'react';

import { useTranslations } from "next-intl";

import { listToolsApiV1MarketplaceToolsGet } from '@/client/sdk.gen';
import { CategoryFilter, DifyImportDialog, ToolCard } from '@/components/marketplace';

interface MarketplaceTool {
    id: number;
    name: string;
    display_name: string;
    category: string;
    subcategory: string | null;
    icon: string | null;
    description: string;
    oauth_enabled: boolean;
    is_installed: boolean;
}

export default function MarketplacePage() {
    const t = useTranslations("marketplace");
    const [tools, setTools] = useState<MarketplaceTool[]>([]);
    const [category, setCategory] = useState('');
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        async function fetchTools() {
            setIsLoading(true);
            try {
                const params: Record<string, unknown> = {};
                if (category) params.query = { category };
                const response = await listToolsApiV1MarketplaceToolsGet(params);
                if (response.data) {
                    setTools(response.data as MarketplaceTool[]);
                }
            } catch (error) {
                console.error('Failed to fetch marketplace tools:', error);
            } finally {
                setIsLoading(false);
            }
        }
        fetchTools();
    }, [category]);

    return (
        <div className="container mx-auto py-8 space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold">Tool Marketplace</h1>
                    <p className="text-muted-foreground mt-1">
                        Add pre-built integrations to your voice agents
                    </p>
                </div>
                <DifyImportDialog />
            </div>

            <CategoryFilter selected={category} onSelect={setCategory} />

            {isLoading ? (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {[1, 2, 3].map((i) => (
                        <div key={i} className="h-48 bg-muted animate-pulse rounded-lg" />
                    ))}
                </div>
            ) : tools.length === 0 ? (
                <div className="text-center py-12 text-muted-foreground">
                    {t("noToolsAvailable")}
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {tools.map((tool) => (
                        <ToolCard key={tool.id} tool={tool} />
                    ))}
                </div>
            )}
        </div>
    );
}
