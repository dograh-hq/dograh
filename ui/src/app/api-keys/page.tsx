"use client";

import { Copy, Eye, EyeOff, Key, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import {
    archiveApiKeyApiV1UserApiKeysApiKeyIdDelete,
    archiveServiceKeyApiV1UserServiceKeysServiceKeyIdDelete,
    createApiKeyApiV1UserApiKeysPost,
    createServiceKeyApiV1UserServiceKeysPost,
    getApiKeysApiV1UserApiKeysGet,
    getServiceKeysApiV1UserServiceKeysGet,
    reactivateApiKeyApiV1UserApiKeysApiKeyIdReactivatePut
} from '@/client/sdk.gen';
import type { ApiKeyResponse, CreateApiKeyResponse, CreateServiceKeyResponse,ServiceKeyResponse } from '@/client/types.gen';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';
import { isOSSMode } from '@/lib/utils';

export default function APIKeysPage() {
    const { user, getAccessToken, redirectToLogin, loading } = useAuth();

    logger.debug('[APIKeysPage] Component render', {
        loading,
        hasUser: !!user,
        userId: user?.id,
        timestamp: new Date().toISOString()
    });

    const [apiKeys, setApiKeys] = useState<ApiKeyResponse[]>([]);
    const [serviceKeys, setServiceKeys] = useState<ServiceKeyResponse[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [isServiceKeysLoading, setIsServiceKeysLoading] = useState(true);
    const [showArchived, setShowArchived] = useState(false);
    const [showServiceArchived, setShowServiceArchived] = useState(false);
    const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
    const [isCreateServiceDialogOpen, setIsCreateServiceDialogOpen] = useState(false);
    const [newKeyName, setNewKeyName] = useState('');
    const [newServiceKeyName, setNewServiceKeyName] = useState('');
    const [createdKey, setCreatedKey] = useState<CreateApiKeyResponse | null>(null);
    const [createdServiceKey, setCreatedServiceKey] = useState<CreateServiceKeyResponse | null>(null);
    const [showCreatedKeyDialog, setShowCreatedKeyDialog] = useState(false);
    const [showCreatedServiceKeyDialog, setShowCreatedServiceKeyDialog] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    const fetchApiKeys = useCallback(async () => {
        logger.debug('[APIKeysPage] fetchApiKeys called', {
            loading,
            hasUser: !!user,
            userId: user?.id
        });

        // Follow the pattern from UserConfigContext - check both loading and user
        if (loading || !user) {
            logger.debug('[APIKeysPage] fetchApiKeys - skipping due to loading or no user');
            return;
        }

        try {
            setIsLoading(true);
            setError(null);
            logger.debug('[APIKeysPage] fetchApiKeys - calling getAccessToken...');
            const accessToken = await getAccessToken();
            logger.debug('[APIKeysPage] fetchApiKeys - got access token');

            const response = await getApiKeysApiV1UserApiKeysGet({
                query: {

                        include_archived: showArchived

                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setApiKeys(response.data);
            }
        } catch (err) {
            setError('Failed to fetch API keys');
            console.error('Error fetching API keys:', err);
        } finally {
            setIsLoading(false);
        }
    }, [loading, user, getAccessToken, showArchived]);

    const fetchServiceKeys = useCallback(async () => {
        logger.debug('[APIKeysPage] fetchServiceKeys called', {
            loading,
            hasUser: !!user,
            userId: user?.id
        });

        // Follow the pattern from UserConfigContext - check both loading and user
        if (loading || !user) {
            logger.debug('[APIKeysPage] fetchServiceKeys - skipping due to loading or no user');
            return;
        }

        try {
            setIsServiceKeysLoading(true);
            setError(null);
            logger.debug('[APIKeysPage] fetchServiceKeys - calling getAccessToken...');
            const accessToken = await getAccessToken();
            logger.debug('[APIKeysPage] fetchServiceKeys - got access token');

            const response = await getServiceKeysApiV1UserServiceKeysGet({
                query: {
                    include_archived: showServiceArchived
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setServiceKeys(response.data);
            }
        } catch (err) {
            setError('Failed to fetch service keys');
            console.error('Error fetching service keys:', err);
        } finally {
            setIsServiceKeysLoading(false);
        }
    }, [loading, user, getAccessToken, showServiceArchived]);

    useEffect(() => {
        logger.debug('[APIKeysPage] useEffect for fetchApiKeys triggered');
        fetchApiKeys();
    }, [fetchApiKeys]);

    useEffect(() => {
        logger.debug('[APIKeysPage] useEffect for fetchServiceKeys triggered');
        fetchServiceKeys();
    }, [fetchServiceKeys]);

    const handleCreateKey = async () => {
        if (!newKeyName.trim()) {
            setError('Please enter a name for the API key');
            return;
        }

        try {
            setError(null);
            const accessToken = await getAccessToken();

            const response = await createApiKeyApiV1UserApiKeysPost({
                body: {
                    name: newKeyName
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setCreatedKey(response.data);
                setIsCreateDialogOpen(false);
                setShowCreatedKeyDialog(true);
                setNewKeyName('');
                fetchApiKeys();
            }
        } catch (err) {
            setError('Failed to create API key');
            console.error('Error creating API key:', err);
        }
    };

    const handleCreateServiceKey = async () => {
        if (!newServiceKeyName.trim()) {
            setError('Please enter a name for the service key');
            return;
        }

        try {
            setError(null);
            const accessToken = await getAccessToken();

            const response = await createServiceKeyApiV1UserServiceKeysPost({
                body: {
                    name: newServiceKeyName,
                    expires_in_days: 90
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setCreatedServiceKey(response.data);
                setIsCreateServiceDialogOpen(false);
                setShowCreatedServiceKeyDialog(true);
                setNewServiceKeyName('');
                fetchServiceKeys();
            }
        } catch (err) {
            setError('Failed to create service key');
            console.error('Error creating service key:', err);
        }
    };

    const handleArchiveKey = async (keyId: number) => {
        try {
            setError(null);
            const accessToken = await getAccessToken();

            await archiveApiKeyApiV1UserApiKeysApiKeyIdDelete({
                path: {
                    api_key_id: keyId
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            fetchApiKeys();
        } catch (err) {
            setError('Failed to archive API key');
            console.error('Error archiving API key:', err);
        }
    };

    const handleArchiveServiceKey = async (keyId: string) => {
        try {
            setError(null);
            const accessToken = await getAccessToken();

            await archiveServiceKeyApiV1UserServiceKeysServiceKeyIdDelete({
                path: {
                    service_key_id: keyId
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            fetchServiceKeys();
        } catch (err) {
            setError('Failed to archive service key');
            console.error('Error archiving service key:', err);
        }
    };

    const handleReactivateKey = async (keyId: number) => {
        try {
            setError(null);
            const accessToken = await getAccessToken();

            await reactivateApiKeyApiV1UserApiKeysApiKeyIdReactivatePut({
                path: {

                        api_key_id: keyId

                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            fetchApiKeys();
        } catch (err) {
            setError('Failed to reactivate API key');
            console.error('Error reactivating API key:', err);
        }
    };


    const copyToClipboard = async (text: string) => {
        try {
            await navigator.clipboard.writeText(text);
        } catch (err) {
            console.error('Failed to copy to clipboard:', err);
        }
    };

    const formatDate = (dateString: string | null) => {
        if (!dateString) return 'Never';
        return new Date(dateString).toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    };

    // Don't render content until auth is loaded
    if (loading || !user) {
        return (
            <div className="min-h-screen bg-gray-50 flex items-center justify-center">
                <div className="space-y-4">
                    <Skeleton className="h-12 w-64" />
                    <Skeleton className="h-64 w-96" />
                </div>
            </div>
        );
    }

    // In OSS mode, check if there's already an active service key
    const activeServiceKeys = serviceKeys.filter(key => !key.archived_at);
    const canCreateServiceKey = !isOSSMode() || activeServiceKeys.length === 0;
    const showServiceKeyArchiveControls = !isOSSMode();

    return (
        <div className="min-h-screen bg-gray-50">
            <div className="container mx-auto px-4 py-8">
                <div className="max-w-6xl mx-auto">
                    <div className="mb-8">
                        <h1 className="text-3xl font-bold text-gray-900 mb-2">Developer Portal</h1>
                        <p className="text-gray-600">Manage your API keys to access Dograh services programmatically</p>
                    </div>

                    {error && (
                        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
                            {error}
                        </div>
                    )}

                    <Card className="mb-6">
                        <CardHeader>
                            <div className="flex justify-between items-center">
                                <div>
                                    <CardTitle>API Keys</CardTitle>
                                    <CardDescription>
                                        Create and manage API keys for your organization
                                    </CardDescription>
                                </div>
                                <div className="flex gap-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => setShowArchived(!showArchived)}
                                    >
                                        {showArchived ? <Eye className="w-4 h-4 mr-2" /> : <EyeOff className="w-4 h-4 mr-2" />}
                                        {showArchived ? 'Hide' : 'Show'} Archived
                                    </Button>
                                    <Button
                                        onClick={() => setIsCreateDialogOpen(true)}
                                        size="sm"
                                    >
                                        <Plus className="w-4 h-4 mr-2" />
                                        Create New Key
                                    </Button>
                                </div>
                            </div>
                        </CardHeader>
                        <CardContent>
                            {isLoading ? (
                                <div className="space-y-4">
                                    {[1, 2, 3].map((i) => (
                                        <div key={i} className="flex items-center justify-between p-4 border rounded-lg">
                                            <div className="space-y-2">
                                                <Skeleton className="h-4 w-32" />
                                                <Skeleton className="h-3 w-24" />
                                            </div>
                                            <Skeleton className="h-8 w-20" />
                                        </div>
                                    ))}
                                </div>
                            ) : apiKeys.length === 0 ? (
                                <div className="text-center py-12">
                                    <Key className="w-12 h-12 text-gray-400 mx-auto mb-4" />
                                    <p className="text-gray-600 mb-4">No API keys found</p>
                                    <Button onClick={() => setIsCreateDialogOpen(true)}>
                                        Create Your First API Key
                                    </Button>
                                </div>
                            ) : (
                                <div className="space-y-4">
                                    {apiKeys.map((key) => (
                                        <div
                                            key={key.id}
                                            className={`flex items-center justify-between p-4 border rounded-lg ${
                                                key.archived_at ? 'bg-gray-50 opacity-60' : 'bg-white'
                                            }`}
                                        >
                                            <div className="flex-1">
                                                <div className="flex items-center gap-2 mb-1">
                                                    <span className="font-medium text-gray-900">{key.name}</span>
                                                    {key.archived_at ? (
                                                        <Badge variant="secondary">Archived</Badge>
                                                    ) : key.is_active ? (
                                                        <Badge variant="default">Active</Badge>
                                                    ) : (
                                                        <Badge variant="destructive">Inactive</Badge>
                                                    )}
                                                </div>
                                                <div className="flex items-center gap-2 text-sm text-gray-600">
                                                    <span className="font-mono bg-gray-100 px-2 py-1 rounded">{key.key_prefix}...</span>
                                                    <span className="text-xs text-gray-400">
                                                        (Full key hidden for security)
                                                    </span>
                                                </div>
                                                <div className="mt-2 text-xs text-gray-500">
                                                    Created: {formatDate(key.created_at)} •
                                                    Last used: {formatDate(key.last_used_at ?? null)}
                                                </div>
                                            </div>
                                            <div className="flex gap-2">
                                                {key.archived_at ? (
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        onClick={() => handleReactivateKey(key.id)}
                                                    >
                                                        <RefreshCw className="w-4 h-4 mr-1" />
                                                        Reactivate
                                                    </Button>
                                                ) : (
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={() => handleArchiveKey(key.id)}
                                                        className="text-red-600 hover:text-red-700"
                                                    >
                                                        <Trash2 className="w-4 h-4" />
                                                    </Button>
                                                )}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </CardContent>
                    </Card>

                    {/* Dograh Service Keys Section */}
                    <Card className="mb-6">
                        <CardHeader>
                            <div className="flex justify-between items-center">
                                <div>
                                    <CardTitle>Dograh Service Keys</CardTitle>
                                    <CardDescription>
                                        Manage service keys for accessing Dograh AI services (LLM, TTS, STT)
                                    </CardDescription>
                                </div>
                                <div className="flex gap-2">
                                    {showServiceKeyArchiveControls && (
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={() => setShowServiceArchived(!showServiceArchived)}
                                        >
                                            {showServiceArchived ? <Eye className="w-4 h-4 mr-2" /> : <EyeOff className="w-4 h-4 mr-2" />}
                                            {showServiceArchived ? 'Hide' : 'Show'} Archived
                                        </Button>
                                    )}
                                    {canCreateServiceKey && (
                                        <Button
                                            onClick={() => setIsCreateServiceDialogOpen(true)}
                                            size="sm"
                                        >
                                            <Plus className="w-4 h-4 mr-2" />
                                            Create Service Key
                                        </Button>
                                    )}
                                </div>
                            </div>
                        </CardHeader>
                        <CardContent>
                            {isServiceKeysLoading ? (
                                <div className="space-y-4">
                                    {[1, 2].map((i) => (
                                        <div key={i} className="flex items-center justify-between p-4 border rounded-lg">
                                            <div className="space-y-2">
                                                <Skeleton className="h-4 w-32" />
                                                <Skeleton className="h-3 w-24" />
                                            </div>
                                            <Skeleton className="h-8 w-20" />
                                        </div>
                                    ))}
                                </div>
                            ) : serviceKeys.length === 0 ? (
                                <div className="text-center py-12">
                                    <Key className="w-12 h-12 text-gray-400 mx-auto mb-4" />
                                    <p className="text-gray-600 mb-4">No service keys found</p>
                                    {canCreateServiceKey && (
                                        <Button onClick={() => setIsCreateServiceDialogOpen(true)}>
                                            Create Your First Service Key
                                        </Button>
                                    )}
                                </div>
                            ) : (
                                <div className="space-y-4">
                                    {serviceKeys.map((key) => (
                                        <div
                                            key={key.id}
                                            className={`flex items-center justify-between p-4 border rounded-lg ${
                                                key.archived_at ? 'bg-gray-50 opacity-60' : 'bg-white'
                                            }`}
                                        >
                                            <div className="flex-1">
                                                <div className="flex items-center gap-2 mb-1">
                                                    <span className="font-medium text-gray-900">{key.name}</span>
                                                    {key.archived_at ? (
                                                        <Badge variant="secondary">Archived</Badge>
                                                    ) : key.is_active ? (
                                                        <Badge variant="default">Active</Badge>
                                                    ) : (
                                                        <Badge variant="destructive">Inactive</Badge>
                                                    )}
                                                    {key.expires_at && new Date(key.expires_at) > new Date() && (
                                                        <Badge variant="outline">
                                                            Expires: {formatDate(key.expires_at)}
                                                        </Badge>
                                                    )}
                                                </div>
                                                <div className="flex items-center gap-2 text-sm text-gray-600">
                                                    <span className="font-mono bg-gray-100 px-2 py-1 rounded">{key.key_prefix}...</span>
                                                    <span className="text-xs text-gray-400">
                                                        (Full key hidden for security)
                                                    </span>
                                                </div>
                                                <div className="mt-2 text-xs text-gray-500">
                                                    Created: {formatDate(key.created_at)} •
                                                    Last used: {formatDate(key.last_used_at ?? null)}
                                                </div>
                                            </div>
                                            <div className="flex gap-2">
                                                {!key.archived_at && showServiceKeyArchiveControls && (
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={() => handleArchiveServiceKey(String(key.id))}
                                                        className="text-red-600 hover:text-red-700"
                                                    >
                                                        <Trash2 className="w-4 h-4" />
                                                    </Button>
                                                )}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </CardContent>
                    </Card>

                    <div className="p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
                        <p className="text-sm text-yellow-800">
                            <strong>Important:</strong> Keep your API keys secure. Never share them publicly or commit them to version control.
                            API keys provide full access to your organization&apos;s resources.
                        </p>
                    </div>
                </div>
            </div>

            {/* Create API Key Dialog */}
            <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Create New API Key</DialogTitle>
                        <DialogDescription>
                            Enter a descriptive name for your API key to help you identify it later.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="grid gap-4 py-4">
                        <div className="grid gap-2">
                            <Label htmlFor="name">Key Name</Label>
                            <Input
                                id="name"
                                value={newKeyName}
                                onChange={(e) => setNewKeyName(e.target.value)}
                                placeholder="e.g., Production Server, Development Environment"
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setIsCreateDialogOpen(false)}>
                            Cancel
                        </Button>
                        <Button onClick={handleCreateKey}>
                            Create Key
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Show Created Key Dialog */}
            <Dialog open={showCreatedKeyDialog} onOpenChange={setShowCreatedKeyDialog}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>API Key Created Successfully</DialogTitle>
                        <DialogDescription>
                            Make sure to copy your API key now. You won&apos;t be able to see it again!
                        </DialogDescription>
                    </DialogHeader>
                    {createdKey && (
                        <div className="space-y-4">
                            <div className="p-4 bg-gray-100 rounded-lg">
                                <p className="text-sm text-gray-600 mb-2">Your API Key:</p>
                                <div className="flex items-center gap-2">
                                    <code className="flex-1 p-2 bg-white rounded text-sm font-mono break-all">
                                        {createdKey.api_key}
                                    </code>
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={() => copyToClipboard(createdKey.api_key)}
                                    >
                                        <Copy className="w-4 h-4" />
                                    </Button>
                                </div>
                            </div>
                            <div className="p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
                                <p className="text-sm text-yellow-800">
                                    Store this key securely. It will only be shown once and cannot be retrieved later.
                                </p>
                            </div>
                        </div>
                    )}
                    <DialogFooter>
                        <Button onClick={() => {
                            setShowCreatedKeyDialog(false);
                            setCreatedKey(null);
                        }}>
                            Done
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Create Service Key Dialog */}
            <Dialog open={isCreateServiceDialogOpen} onOpenChange={setIsCreateServiceDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Create New Service Key</DialogTitle>
                        <DialogDescription>
                            Create a service key to access Dograh AI services (LLM, TTS, STT)
                        </DialogDescription>
                    </DialogHeader>
                    <div className="grid gap-4 py-4">
                        <div className="grid gap-2">
                            <Label htmlFor="service-name">Service Key Name</Label>
                            <Input
                                id="service-name"
                                value={newServiceKeyName}
                                onChange={(e) => setNewServiceKeyName(e.target.value)}
                                placeholder="e.g., Production AI Services, Development LLM Access"
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setIsCreateServiceDialogOpen(false)}>
                            Cancel
                        </Button>
                        <Button onClick={handleCreateServiceKey}>
                            Create Service Key
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Show Created Service Key Dialog */}
            <Dialog open={showCreatedServiceKeyDialog} onOpenChange={setShowCreatedServiceKeyDialog}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Service Key Created Successfully</DialogTitle>
                        <DialogDescription>
                            Make sure to copy your service key now. You won&apos;t be able to see it again!
                        </DialogDescription>
                    </DialogHeader>
                    {createdServiceKey && (
                        <div className="space-y-4">
                            <div className="p-4 bg-gray-100 rounded-lg">
                                <p className="text-sm text-gray-600 mb-2">Your Service Key:</p>
                                <div className="flex items-center gap-2">
                                    <code className="flex-1 p-2 bg-white rounded text-sm font-mono break-all">
                                        {createdServiceKey.service_key}
                                    </code>
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={() => copyToClipboard(createdServiceKey.service_key)}
                                    >
                                        <Copy className="w-4 h-4" />
                                    </Button>
                                </div>
                            </div>
                            <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg">
                                <p className="text-sm text-blue-800">
                                    This key provides access to Dograh AI services including LLM, Text-to-Speech, and Speech-to-Text.
                                    {createdServiceKey.expires_at && (
                                        <span className="block mt-1">
                                            Expires on: {formatDate(createdServiceKey.expires_at)}
                                        </span>
                                    )}
                                </p>
                            </div>
                            <div className="p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
                                <p className="text-sm text-yellow-800">
                                    Store this key securely. It will only be shown once and cannot be retrieved later.
                                </p>
                            </div>
                        </div>
                    )}
                    <DialogFooter>
                        <Button onClick={() => {
                            setShowCreatedServiceKeyDialog(false);
                            setCreatedServiceKey(null);
                        }}>
                            Done
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
