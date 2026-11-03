"use client";

import { ExternalLink, Upload } from "lucide-react";
import { useEffect, useState } from "react";

import { PageLayout, PageSection } from "@/components/layout/PageLayout";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/lib/auth";

import RecordingsList from "./RecordingsList";
import { RecordingsUploadDialog } from "./RecordingsUploadDialog";
import TtsCacheList from "./TtsCacheList";

export default function RecordingsPage() {
    const { user, redirectToLogin, loading } = useAuth();
    const [isUploadOpen, setIsUploadOpen] = useState(false);
    const [refreshKey, setRefreshKey] = useState(0);

    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    if (loading || !user) {
        return (
            <PageLayout title="Recordings">
                <div className="space-y-4">
                    <Skeleton className="h-12 w-64" />
                    <Skeleton className="h-64 w-full" />
                </div>
            </PageLayout>
        );
    }

    return (
        <PageLayout title="Recordings" description={<>Manage audio recordings for your organization. Use{" "}
            <code className="rounded bg-muted px-1 text-xs">@</code> in prompt fields to insert them,
            or as transition messages in tool calls.{" "}
            <a href="https://docs.dograh.com/voice-agent/pre-recorded-audio" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                Learn more <ExternalLink className="h-3 w-3" />
            </a></>}>

            <Tabs defaultValue="recordings" key={`${user.id}:${"selectedTeam" in user ? user.selectedTeam?.id : user.organizationId}`}>
                <TabsList className="mb-4">
                    <TabsTrigger value="recordings">Uploaded recordings</TabsTrigger>
                    <TabsTrigger value="tts-cache">TTS cache</TabsTrigger>
                </TabsList>
                <TabsContent value="recordings">
                    <PageSection title="All Recordings"
                        description="Audio recordings shared across all agents in your organization"
                        actions={<Button onClick={() => setIsUploadOpen(true)}>
                            <Upload className="w-4 h-4 mr-2" />
                            Upload Recording
                        </Button>}>
                        <RecordingsList refreshKey={refreshKey} />
                    </PageSection>
                </TabsContent>
                <TabsContent value="tts-cache">
                    <PageSection title="Cached speech"
                        description="Speech reused across your organization’s workflows. Listen to a phrase and invalidate it to generate fresh audio on its next request.">
                        <TtsCacheList />
                    </PageSection>
                </TabsContent>
            </Tabs>

            <RecordingsUploadDialog
                open={isUploadOpen}
                onOpenChange={setIsUploadOpen}
                onUploadComplete={() => setRefreshKey((k) => k + 1)}
            />
        </PageLayout>
    );
}
