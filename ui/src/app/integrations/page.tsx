'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';

import { getIntegrationsApiV1IntegrationGet } from "@/client/sdk.gen";
import type { IntegrationResponse } from '@/client/types.gen';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';

import CreateIntegrationButton from "./CreateIntegrationButton";

function IntegrationsLoading() {
    return (
        <div className="container mx-auto px-4 py-8">
            <div className="mb-6">
                <div className="flex justify-between items-center mb-6">
                    <div className="h-8 w-48 bg-muted rounded"></div>
                    <div className="h-10 w-32 bg-muted rounded"></div>
                </div>
                <div className="overflow-x-auto">
                    <table className="min-w-full bg-card border border-border">
                        <thead className="bg-muted">
                            <tr>
                                <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                    Integration ID
                                </th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                    Channel
                                </th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                    Action
                                </th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                    Created At
                                </th>
                            </tr>
                        </thead>
                        <tbody className="bg-card divide-y divide-border">
                            {Array.from({ length: 5 }, (_, i) => (
                                <tr key={i}>
                                    <td className="px-6 py-4 whitespace-nowrap">
                                        <div className="h-4 w-32 bg-muted rounded"></div>
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap">
                                        <div className="h-4 w-24 bg-muted rounded"></div>
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap">
                                        <div className="h-4 w-24 bg-muted rounded"></div>
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap">
                                        <div className="h-4 w-24 bg-muted rounded"></div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}

export default function IntegrationsPage() {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);
    const [integrations, setIntegrations] = useState<IntegrationResponse[] | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;

        const fetchIntegrations = async () => {
            try {
                const response = await getIntegrationsApiV1IntegrationGet({});

                const integrationData = response.data ? (Array.isArray(response.data) ? response.data : [response.data]) : [];
                const sorted = [...integrationData].sort((a, b) =>
                    new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
                );
                setIntegrations(sorted);
            } catch (err) {
                logger.error(`Error fetching integrations: ${err}`);
                setError('Failed to load Integrations. Please Try Again Later.');
            }
        };

        fetchIntegrations();
    }, [authLoading, user]);

    if (authLoading || (integrations === null && !error)) {
        return <IntegrationsLoading />;
    }

    return (
        <div className="container mx-auto px-4 py-8">
            <div className="mb-6">
                <div className="flex justify-between items-center mb-6">
                    <h1 className="text-2xl font-bold">Your Integrations</h1>
                    <CreateIntegrationButton />
                </div>

                {error ? (
                    <div className="text-red-500 text-center py-8">{error}</div>
                ) : !integrations || integrations.length === 0 ? (
                    <div className="text-center py-8 text-muted-foreground">
                        No integrations found. Create your first integration to get started.
                    </div>
                ) : (
                    <div className="space-y-6">
                        <div className="overflow-x-auto">
                            <table className="min-w-full bg-card border border-border">
                                <thead className="bg-muted">
                                    <tr>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                            Provider
                                        </th>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                            Channel
                                        </th>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                            Action
                                        </th>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                            Created At
                                        </th>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                            Actions
                                        </th>
                                    </tr>
                                </thead>
                                <tbody className="bg-card divide-y divide-border">
                                    {integrations.map((integration) => (
                                        <tr key={integration.id} className="hover:bg-muted/50">
                                            <td className="px-6 py-4 whitespace-nowrap text-sm font-medium">
                                                {integration.provider}
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">
                                                {integration.provider === 'slack' && integration.provider_data ? (integration.provider_data.channel as string) || '-' : '-'}
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">
                                                {integration.action}
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">
                                                {new Date(integration.created_at).toLocaleDateString('en-US', {
                                                    year: 'numeric',
                                                    month: 'short',
                                                    day: 'numeric',
                                                    hour: '2-digit',
                                                    minute: '2-digit'
                                                })}
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">
                                                {integration.provider === 'google-mail' && (
                                                    <Link
                                                        href={`/integrations/${integration.id}/gmail`}
                                                        className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                                                    >
                                                        Search
                                                    </Link>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
