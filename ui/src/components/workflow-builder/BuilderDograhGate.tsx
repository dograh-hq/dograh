'use client';

import Link from 'next/link';

import { DograhAccountConnectFlow } from '@/components/model-connections/DograhAccountConnect';
import { Button } from '@/components/ui/button';
import { useCreateBlankWorkflow } from '@/components/workflow/useCreateBlankWorkflow';
import { useAppConfig } from '@/context/AppConfigContext';

type BuilderDograhGateProps = {
    reason: 'missing' | 'invalid';
    onConnected: () => Promise<void> | void;
    // Creating a new agent can fall back to the canvas; editing one already has it.
    offerBlankCanvas: boolean;
};

/**
 * Shown in place of the builder's composer when the organization has no
 * Dograh key the builder can run on, so nobody writes a request that can
 * only fail.
 */
export function BuilderDograhGate({ reason, onConnected, offerBlankCanvas }: BuilderDograhGateProps) {
    const { config } = useAppConfig();
    const selfHosted = config?.deploymentMode === 'oss';
    const { createBlankWorkflow, isCreating } = useCreateBlankWorkflow();

    const title = reason === 'invalid'
        ? (selfHosted ? 'Reconnect your Dograh account' : 'Check your Dograh connection')
        : 'Agent builder runs on Dograh models';
    const description = selfHosted
        ? (reason === 'invalid'
            ? "Dograh no longer accepts this installation's key. Reconnect your Dograh account to keep using the agent builder."
            : 'Connect a free Dograh account to use the agent builder. You get trial credits, with no API keys to copy.')
        : (reason === 'invalid'
            ? "Dograh no longer accepts this organization's Dograh key. Check the Dograh connection on the Models page."
            : 'This organization has no Dograh connection. Add one on the Models page to use the agent builder.');

    return <div role="region" aria-label="Agent builder setup" className="space-y-4 rounded-xl border bg-background p-5 text-left">
        <div>
            <h2 className="text-base font-medium">{title}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        </div>
        {selfHosted
            ? <DograhAccountConnectFlow onConnected={onConnected} prompt="Sign in to Dograh with your email. New accounts get trial credits." />
            : <Button asChild variant="outline"><Link href="/model-configurations">Open Models page</Link></Button>}
        {offerBlankCanvas && <p className="text-sm text-muted-foreground">
            Prefer to build it yourself?{' '}
            <button type="button" className="font-medium text-foreground underline underline-offset-4 disabled:opacity-50" disabled={isCreating} onClick={() => void createBlankWorkflow()}>
                {isCreating ? 'Creating…' : 'Start from a blank canvas'}
            </button>
        </p>}
    </div>;
}
