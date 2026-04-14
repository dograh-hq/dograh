'use client';

import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { getTestSessionApiV1LooptalkTestSessionsTestSessionIdGet } from '@/client/sdk.gen';
import type { TestSessionResponse } from '@/client/types.gen';
import { ConversationsList } from '@/components/looptalk/ConversationsList';
import { LiveAudioPlayer } from '@/components/looptalk/LiveAudioPlayer';
import { TestSessionControls } from '@/components/looptalk/TestSessionControls';
import { TestSessionDetails } from '@/components/looptalk/TestSessionDetails';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';

import LoopTalkLayout from "../LoopTalkLayout";

function TestSessionLoading() {
    return (
        <div className="container mx-auto px-4 py-8">
            <div className="space-y-4">
                <div className="h-32 bg-muted rounded-lg animate-pulse"></div>
                <div className="h-20 bg-muted rounded-lg animate-pulse"></div>
                <div className="h-64 bg-muted rounded-lg animate-pulse"></div>
            </div>
        </div>
    );
}

function TestSessionPageContent() {
    const params = useParams();
    const testSessionId = parseInt(params.id as string);
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);
    const [testSession, setTestSession] = useState<TestSessionResponse | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;

        const fetchTestSession = async () => {
            try {
                const response = await getTestSessionApiV1LooptalkTestSessionsTestSessionIdGet({
                    path: {
                        test_session_id: testSessionId
                    },
                });

                if (!response.data) {
                    setError('Test session not found');
                    return;
                }
                setTestSession(response.data);
            } catch (err) {
                logger.error(`Error fetching test session: ${err}`);
                setError('Failed to load test session');
            }
        };

        fetchTestSession();
    }, [authLoading, user, testSessionId]);

    if (authLoading || (testSession === null && !error)) {
        return <TestSessionLoading />;
    }

    if (error || !testSession) {
        return (
            <div className="container mx-auto px-4 py-8">
                <div className="text-red-500 text-center py-8">
                    {error || 'Test session not found'}
                </div>
            </div>
        );
    }

    const sessionForUI = {
        id: testSession.id,
        name: testSession.name,
        description: '',
        test_type: testSession.test_index !== null ? 'load_test' : 'single',
        status: testSession.status,
        actor_workflow_name: `Workflow ${testSession.actor_workflow_id}`,
        adversary_workflow_name: `Workflow ${testSession.adversary_workflow_id}`,
        created_at: testSession.created_at,
        updated_at: testSession.created_at,
        test_metadata: testSession.config
    };

    return (
        <div className="container mx-auto px-4 py-8">
            <TestSessionDetails session={sessionForUI} />
            <TestSessionControls session={sessionForUI} />
            <div className="mt-6">
                <LiveAudioPlayer
                    testSessionId={testSessionId}
                    sessionStatus={testSession.status as 'pending' | 'running' | 'completed' | 'failed'}
                    autoStart={true}
                />
            </div>
            <div className="mt-8">
                <h2 className="text-xl font-bold mb-4">Conversations</h2>
                <ConversationsList testSessionId={testSessionId} />
            </div>
        </div>
    );
}

export default function TestSessionPage() {
    const backButton = (
        <Link href="/looptalk">
            <Button variant="ghost" size="sm">
                <ArrowLeft className="h-4 w-4 mr-2" />
                Back to Test Sessions
            </Button>
        </Link>
    );

    return (
        <LoopTalkLayout backButton={backButton}>
            <TestSessionPageContent />
        </LoopTalkLayout>
    );
}
