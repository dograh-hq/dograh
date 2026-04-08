'use client';

import { useEffect, useState } from 'react';

import { listTestSessionsApiV1LooptalkTestSessionsGet } from '@/client/sdk.gen';
import type { TestSessionResponse } from '@/client/types.gen';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';

import { TestSessionCard } from './TestSessionCard';
import { TestSession } from './types';

interface LoopTalkTestSessionsListProps {
    status?: 'active' | 'completed' | 'failed';
}

export function LoopTalkTestSessionsList({ status }: LoopTalkTestSessionsListProps) {
    const [sessions, setSessions] = useState<TestSession[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const { user, getAccessToken } = useAuth();

    useEffect(() => {
        const fetchSessions = async () => {
            if (!user) return;
            try {
                const accessToken = await getAccessToken();
                const response = await listTestSessionsApiV1LooptalkTestSessionsGet({
                    query: status ? { status } : undefined,
                    headers: {
                        'Authorization': `Bearer ${accessToken}`,
                    },
                });

                // Transform API response to match UI types
                const transformedSessions = (response.data || []).map((session: TestSessionResponse) => ({
                    id: session.id,
                    name: session.name,
                    description: '', // API doesn't return description
                    test_type: session.test_index !== null ? 'load_test' : 'single',
                    status: session.status,
                    actor_workflow_name: `Workflow ${session.actor_workflow_id}`,
                    adversary_workflow_name: `Workflow ${session.adversary_workflow_id}`,
                    created_at: session.created_at,
                    updated_at: session.created_at, // API doesn't have updated_at
                    test_metadata: session.config
                }));

                setSessions(transformedSessions);
            } catch (err) {
                logger.error('Error fetching test sessions:', err);
                setError('Failed to load test sessions');
            } finally {
                setLoading(false);
            }
        };

        fetchSessions();
    }, [status, user, getAccessToken]);

    if (loading) {
        return (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {Array.from({ length: 3 }, (_, i) => (
                    <div key={i} className="bg-gray-200 rounded-lg h-40 animate-pulse"></div>
                ))}
            </div>
        );
    }

    if (error) {
        return (
            <div className="text-red-500">
                {error}
            </div>
        );
    }

    if (sessions.length === 0) {
        return (
            <div className="text-center py-12 px-4">
                <div className="text-gray-500 mb-2">
                    No {status ? `${status} ` : ''}test sessions found
                </div>
                {!status && (
                    <p className="text-sm text-gray-400">
                        Create a new test session to start testing agent conversations
                    </p>
                )}
            </div>
        );
    }

    return (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {sessions.map((session) => (
                <TestSessionCard
                    key={session.id}
                    session={session}
                />
            ))}
        </div>
    );
}
