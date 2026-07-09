'use client';

import { useState } from 'react';
import { Plug } from 'lucide-react';

import { createToolApiV1ToolsPost } from '@/client/sdk.gen';
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
import { client } from '@/client/client.gen';
import type { McpToolConfig } from '@/client/types.gen';

export function DifyImportDialog() {
    const [open, setOpen] = useState(false);
    const [name, setName] = useState('');
    const [url, setUrl] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [isTesting, setIsTesting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [testResult, setTestResult] = useState<string | null>(null);
    const { user } = useAuth();

    const handleTest = async () => {
        if (!url.trim()) return;
        setIsTesting(true);
        setTestResult(null);
        setError(null);
        try {
            const response = await client.get('/api/v1/marketplace/test-mcp', {
                params: { url: url.trim() },
            });
            const data = response.data as Record<string, unknown>;
            if (data.ok) {
                const count = data.tool_count as number;
                const tools = data.tools as Array<{ name: string; description: string }>;
                setTestResult(`✅ Connected! ${count} tool(s) found:` + tools.map(t => `\n  • ${t.name}`).join(''));
            } else {
                setTestResult('❌ Connection failed');
            }
        } catch (err: unknown) {
            const msg = err && typeof err === 'object' && 'message' in err ? (err as Error).message : 'Test failed';
            setTestResult(`❌ ${msg}`);
        } finally {
            setIsTesting(false);
        }
    };

    const handleImport = async () => {
        if (!user || !url.trim() || !name.trim()) return;
        setIsLoading(true);
        setError(null);
        try {
            const config: McpToolConfig = {
                transport: 'streamable_http',
                url: url.trim(),
            };
            const response = await createToolApiV1ToolsPost({
                body: {
                    name: name.trim(),
                    description: `Dify workflow imported from ${url.trim()}`,
                    category: 'mcp',
                    icon: 'Workflow',
                    definition: {
                        type: 'mcp',
                        schema_version: 1,
                        config,
                    },
                },
            });
            if (response.error) {
                setError(typeof response.error === 'string' ? response.error : 'Import failed');
            } else {
                setOpen(false);
                setName('');
                setUrl('');
                setTestResult(null);
                window.location.reload();
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Import failed');
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) { setName(''); setUrl(''); setError(null); setTestResult(null); } }}>
            <DialogTrigger asChild>
                <Button variant="secondary">Import from Dify</Button>
            </DialogTrigger>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Import Dify Workflow</DialogTitle>
                    <DialogDescription>
                        Each Dify workflow published as an MCP Server gets its own URL. Give it a name and paste the URL below. You can import as many workflows as you need.
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 py-4">
                    <div className="space-y-2">
                        <Label htmlFor="dify-name">Tool Name</Label>
                        <Input
                            id="dify-name"
                            placeholder="e.g. Dify: Sentiment Analysis"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                        />
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="dify-url">MCP Server URL</Label>
                        <div className="flex gap-2">
                            <Input
                                id="dify-url"
                                placeholder="https://your-dify.app/mcp/..."
                                value={url}
                                onChange={(e) => { setUrl(e.target.value); setTestResult(null); }}
                                className="flex-1"
                            />
                            <Button
                                variant="outline"
                                size="icon"
                                onClick={handleTest}
                                disabled={isTesting || !url.trim()}
                                title="Test connection"
                            >
                                <Plug className={`h-4 w-4 ${isTesting ? 'animate-spin' : ''}`} />
                            </Button>
                        </div>
                    </div>
                    {testResult && (
                        <pre className="text-xs bg-muted p-3 rounded-md whitespace-pre-wrap">{testResult}</pre>
                    )}
                    {error && (
                        <p className="text-sm text-destructive">{error}</p>
                    )}
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={() => setOpen(false)}>
                        Cancel
                    </Button>
                    <Button onClick={handleImport} disabled={isLoading || !url.trim() || !name.trim()}>
                        {isLoading ? 'Importing...' : 'Import'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
