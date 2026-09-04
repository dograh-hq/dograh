'use client';

import Script from 'next/script';
import { useEffect } from 'react';

import { useAuth } from '@/lib/auth';

interface ReoIdentity {
    username: string;
    type: 'email';
    firstname?: string;
    lastname?: string;
}

declare global {
    interface Window {
        Reo?: {
            init: (config: { clientID: string; dnt?: string[] }) => void;
            identify: (identity: ReoIdentity) => void;
        };
    }
}

export default function ReoProvider({ clientId }: { clientId: string }) {
    const { user } = useAuth();

    useEffect(() => {
        if (!user) return;

        // Stack Auth users expose primaryEmail/displayName,
        // local users expose email/name — handle both.
        const email =
            'primaryEmail' in user ? user.primaryEmail :
            'email' in user ? user.email :
            undefined;
        if (!email) return;

        const name =
            'displayName' in user ? user.displayName :
            'name' in user ? user.name :
            undefined;
        const [firstname, ...rest] = (name ?? '').split(' ').filter(Boolean);

        const identify = () => {
            try {
                window.Reo?.identify({
                    username: email,
                    type: 'email',
                    ...(firstname && { firstname }),
                    ...(rest.length > 0 && { lastname: rest.join(' ') }),
                });
            } catch (err) {
                console.warn('Failed to identify user in Reo', err);
            }
        };

        if (window.Reo) {
            identify();
        } else {
            // reo.js loads async — retry until ready
            let attempts = 0;
            const interval = setInterval(() => {
                attempts++;
                if (window.Reo) {
                    identify();
                    clearInterval(interval);
                } else if (attempts >= 20) {
                    clearInterval(interval);
                }
            }, 200);
            return () => clearInterval(interval);
        }
    }, [user]);

    return (
        <Script
            id="reo-loader"
            src={`https://static.reo.dev/${clientId}/reo.js`}
            strategy="afterInteractive"
            onLoad={() => {
                window.Reo?.init({ clientID: clientId, dnt: ['copy'] });
            }}
        />
    );
}
