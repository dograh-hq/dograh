'use client';

import { ArrowRight, CheckCircle2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { DograhAccountConnectFlow } from '@/components/model-connections/DograhAccountConnect';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';

export type ModelSetupChoice = 'dograh' | 'own_keys' | 'skipped';

type ModelSetupModalProps = {
    open: boolean;
    // The installation has a Dograh key that Dograh no longer accepts.
    reconnect: boolean;
    // Called once an account is connected, before the person moves on.
    onConnected: () => Promise<void> | void;
    onDone: (choice: ModelSetupChoice) => void;
    onCancel: () => void;
};

/**
 * Shown on a builder submission or call attempt: choose where agents get their
 * LLM, voice and transcription models. A connected Dograh account also
 * unlocks the agent builder.
 */
export function ModelSetupModal({ open, reconnect, onConnected, onDone, onCancel }: ModelSetupModalProps) {
    const router = useRouter();
    const [connected, setConnected] = useState(false);
    // While a request waits for approval, only its Cancel ends it.
    const [pending, setPending] = useState(false);

    const finish = (choice: ModelSetupChoice, path?: string) => {
        onDone(choice);
        if (path) router.push(path);
    };

    return <Dialog open={open} onOpenChange={next => { if (!next && !pending) onCancel(); }}>
        {/* Clicking outside must not throw away a connection in progress. */}
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[600px]" onInteractOutside={event => event.preventDefault()}>
            <DialogHeader>
                <DialogTitle>Set up AI models</DialogTitle>
                <DialogDescription>
                    Your agents need large language models to hold a conversation.
                    Choose where this installation gets them. You can change it later on the Models page.
                </DialogDescription>
            </DialogHeader>

            {connected ? <div role="status" className="space-y-4 rounded-lg border p-5">
                <div className="flex items-start gap-3">
                    <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
                    <div>
                        <p className="font-medium">Dograh models connected</p>
                        <p className="mt-1 text-sm text-muted-foreground">Dograh models and the agent builder are ready to use.</p>
                    </div>
                </div>
                <Button onClick={() => finish('dograh')}>Continue<ArrowRight className="h-4 w-4" /></Button>
            </div> : <div className="space-y-4">
                <section aria-labelledby="model-setup-dograh" className="space-y-3 rounded-lg border border-primary/40 p-4">
                    <div>
                        <div className="flex items-center gap-2">
                            <h3 id="model-setup-dograh" className="font-medium">{reconnect ? 'Reconnect Dograh models' : 'Dograh models'}</h3>
                            <Badge variant="secondary">Recommended</Badge>
                        </div>
                        <p className="mt-1 text-sm text-muted-foreground">{reconnect
                            ? "Dograh no longer accepts this installation's key. Reconnect to keep using Dograh models and the agent builder."
                            : 'Use Dograh hosted AI Models. Dograh provides complementary signup credits.'}</p>
                    </div>
                    <DograhAccountConnectFlow
                        onPendingChange={setPending}
                        onConnected={async () => { setConnected(true); await onConnected(); }} />
                </section>

                <section aria-labelledby="model-setup-own-keys" className="space-y-3 rounded-lg border p-4">
                    <div>
                        <h3 id="model-setup-own-keys" className="font-medium">Your own provider keys</h3>
                        <p className="mt-1 text-sm text-muted-foreground">
                            Use API keys from LLM, voice and transcription providers you already have.
                            Build agents on the canvas without Dograh registration. The natural-language agent builder uses Dograh-managed services.
                        </p>
                    </div>
                    <Button variant="outline" disabled={pending} onClick={() => finish('own_keys', '/provider-connections/new')}>Add provider keys</Button>
                </section>
            </div>}

            {!connected && <DialogFooter>
                <Button variant="ghost" disabled={pending} onClick={() => finish('skipped')}>Skip for now</Button>
            </DialogFooter>}
        </DialogContent>
    </Dialog>;
}
