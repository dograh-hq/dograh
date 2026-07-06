'use client';

import { useState } from 'react';

import { connectToolApiV1MarketplaceToolsToolIdConnectPost } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAuth } from '@/lib/auth';

export function DifyImportDialog() {
    const [open, setOpen] = useState(false);
    const [url, setUrl] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const { user } = useAuth();

    const handleImport = async () => {
        if (!user || !url.trim()) return;
        setIsLoading(true);
        setError(null);
        try {
            const response = await connectToolApiV1MarketplaceToolsToolIdConnectPost({
                // The Dify marketplace entry has id=2 (from seed)
                path: { tool_id: 2 },
                body: {
                    user_url: url.trim(),
                },
            });
            if (response.error) {
                setError(typeof response.error === 'string' ? response.error : 'Import failed');
            } else {
                setOpen(false);
                window.location.reload();
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Import failed');
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button variant="secondary">Import from Dify</Button>
            </DialogTrigger>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Import Dify Workflow</DialogTitle>
                    <DialogDescription>
                        Paste your Dify workflow MCP Server URL. Find it in your Dify app under
                        Publish → MCP Server.
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 py-4">
                    <div className="space-y-2">
                        <Label htmlFor="dify-url">MCP Server URL</Label>
                        <Input
                            id="dify-url"
                            placeholder="https://your-dify.app/mcp/..."
                            value={url}
                            onChange={(e) => setUrl(e.target.value)}
                        />
                    </div>
                    {error && (
                        <p className="text-sm text-destructive">{error}</p>
                    )}
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={() => setOpen(false)}>
                        Cancel
                    </Button>
                    <Button onClick={handleImport} disabled={isLoading || !url.trim()}>
                        {isLoading ? 'Importing...' : 'Import'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
