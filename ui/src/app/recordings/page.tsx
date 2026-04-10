"use client";

import { useEffect } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

import RecordingsList from "./RecordingsList";

export default function RecordingsPage() {
    const { user, redirectToLogin, loading } = useAuth();

    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    if (loading || !user) {
        return (
            <div className="container mx-auto px-4 py-8">
                <div className="space-y-4">
                    <Skeleton className="h-12 w-64" />
                    <Skeleton className="h-64 w-full" />
                </div>
            </div>
        );
    }

    return (
        <div className="container mx-auto px-4 py-8">
            <div className="mb-8">
                <h1 className="text-3xl font-bold mb-2">Recordings</h1>
                <p className="text-muted-foreground">
                    View all audio recordings across your voice agents. Filter by agent, provider, model, or voice.
                </p>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>All Recordings</CardTitle>
                    <CardDescription>
                        Audio recordings scoped to your organization
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <RecordingsList />
                </CardContent>
            </Card>
        </div>
    );
}
