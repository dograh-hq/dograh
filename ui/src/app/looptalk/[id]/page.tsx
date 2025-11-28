import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Suspense } from 'react';

import { getTestSessionApiV1LooptalkTestSessionsTestSessionIdGet } from '@/client/sdk.gen';
import { ConversationsList } from '@/components/looptalk/ConversationsList';
import { LiveAudioPlayer } from '@/components/looptalk/LiveAudioPlayer';
import { TestSessionControls } from '@/components/looptalk/TestSessionControls';
import { TestSessionDetails } from '@/components/looptalk/TestSessionDetails';
import { Button } from '@/components/ui/button';
import { getServerAccessToken,getServerAuthProvider } from '@/lib/auth/server';
import logger from '@/lib/logger';

import LoopTalkLayout from "../LoopTalkLayout";

interface PageProps {
    params: Promise<{
        id: string;
    }>;
}

async function PageContent({ params }: PageProps) {
    const authProvider = getServerAuthProvider();
    const accessToken = await getServerAccessToken();

    if (!accessToken) {
        const { redirect } = await import('next/navigation');
        if (authProvider === 'stack') {
            redirect('/');
        } else {
            // For OSS mode, this shouldn't happen as token is auto-generated
            return (
                <div className="text-red-500">
                    Authentication required. Please refresh the page.
                </div>
            );
        }
    }

    try {
        const resolvedParams = await params;
        const testSessionId = parseInt(resolvedParams.id);
        const response = await getTestSessionApiV1LooptalkTestSessionsTestSessionIdGet({
            path: {
                test_session_id: testSessionId
            },
            headers: {
                'Authorization': `Bearer ${accessToken}`,
            },
        });

        const testSession = response.data;

        if (!testSession) {
            notFound();
        }

        // Transform the API response to match our UI types
        const sessionForUI = {
            id: testSession.id,
            name: testSession.name,
            description: '', // API doesn't return description
            test_type: testSession.test_index !== null ? 'load_test' : 'single',
            status: testSession.status,
            actor_workflow_name: `Workflow ${testSession.actor_workflow_id}`, // We'll need to fetch actual names
            adversary_workflow_name: `Workflow ${testSession.adversary_workflow_id}`,
            created_at: testSession.created_at,
            updated_at: testSession.created_at, // API doesn't have updated_at
            test_metadata: testSession.config
        };

        return (
            <div className="container mx-auto px-4 py-8">
                <TestSessionDetails session={sessionForUI} />
                <TestSessionControls session={sessionForUI} />
                {/* Persistent Audio Player */}
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
    } catch (err) {
        logger.error(`Error fetching test session: ${err}`);
        notFound();
    }
}

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

export default function TestSessionPage({ params }: PageProps) {
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
            <Suspense fallback={<TestSessionLoading />}>
                <PageContent params={params} />
            </Suspense>
        </LoopTalkLayout>
    );
}
