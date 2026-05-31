import { redirect } from 'next/navigation';

export const dynamic = 'force-dynamic';

// No-auth mode: redirect directly to the main app
export default async function AfterSignInPage() {
    redirect('/workflow');
}
