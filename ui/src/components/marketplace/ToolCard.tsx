'use client';

import { PackageOpen } from 'lucide-react';
import { useState } from 'react';

import { connectToolApiV1MarketplaceToolsToolIdConnectPost } from '@/client/sdk.gen';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/lib/auth';

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

interface ToolCardProps {
    tool: MarketplaceTool;
}

export function ToolCard({ tool }: ToolCardProps) {
    const [isLoading, setIsLoading] = useState(false);
    const [isInstalled, setIsInstalled] = useState(tool.is_installed);
    const { user } = useAuth();

    const handleConnect = async () => {
        if (!user) return;
        setIsLoading(true);
        try {
            const response = await connectToolApiV1MarketplaceToolsToolIdConnectPost({
                path: { tool_id: tool.id },
                body: {},
            });
            if (response.data) {
                setIsInstalled(true);
                const data = response.data as Record<string, unknown>;
                if (data.status === 'oauth_required' && data.redirect_url) {
                    window.location.href = data.redirect_url;
                }
            }
        } catch (error) {
            console.error('Failed to install marketplace tool:', error);
        } finally {
            setIsLoading(false);
        }
    };

    const categoryLabel = {
        mcp_direct: 'MCP Server',
        dify_workflow: 'Dify',
        http_api: 'HTTP API',
    }[tool.category] ?? tool.category;

    return (
        <Card className="flex flex-col">
            <CardHeader>
                <div className="flex items-center gap-2">
                    <span className="text-2xl">{tool.icon ?? <PackageOpen className="w-6 h-6" />}</span>
                    <div>
                        <CardTitle className="text-lg">{tool.display_name}</CardTitle>
                        <CardDescription>{tool.subcategory ?? categoryLabel}</CardDescription>
                    </div>
                </div>
            </CardHeader>
            <CardContent className="flex-1">
                <p className="text-sm text-muted-foreground">{tool.description}</p>
            </CardContent>
            <CardFooter className="flex justify-between">
                <Badge variant="outline">{categoryLabel}</Badge>
                {isInstalled ? (
                    <Button variant="outline" disabled>
                        Installed
                    </Button>
                ) : (
                    <Button onClick={handleConnect} disabled={isLoading}>
                        {isLoading ? 'Connecting...' : tool.oauth_enabled ? 'Connect with OAuth' : 'Connect'}
                    </Button>
                )}
            </CardFooter>
        </Card>
    );
}
