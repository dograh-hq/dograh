"use client";

import { Suspense } from 'react';

import { RunsView } from './RunsView';

export default function UsagePage() {
    return (
        <Suspense fallback={null}>
            <RunsView />
        </Suspense>
    );
}
