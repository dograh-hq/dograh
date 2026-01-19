"use client";

import { useCallback, useEffect, useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/lib/auth";

import DocumentList from "./DocumentList";
import DocumentUpload from "./DocumentUpload";

export default function FilesPage() {
    const { user, getAccessToken, redirectToLogin, loading } = useAuth();
    const [refreshKey, setRefreshKey] = useState(0);
    const [accessToken, setAccessToken] = useState<string>('');

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    // Get access token
    const fetchAccessToken = useCallback(async () => {
        if (user) {
            const token = await getAccessToken();
            setAccessToken(token);
        }
    }, [user, getAccessToken]);

    useEffect(() => {
        fetchAccessToken();
    }, [fetchAccessToken]);

    const handleUploadSuccess = () => {
        // Trigger refresh of document list
        setRefreshKey(prev => prev + 1);
    };

    if (loading || !user || !accessToken) {
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
                <h1 className="text-3xl font-bold mb-2">Knowledge Base Files</h1>
                <p className="text-muted-foreground">
                    Upload and manage documents for your voice agents to reference.
                </p>
            </div>

            <Tabs defaultValue="all" className="space-y-6">
                <TabsList>
                    <TabsTrigger value="all">All Files</TabsTrigger>
                    <TabsTrigger value="upload">Upload New</TabsTrigger>
                </TabsList>

                <TabsContent value="all" className="space-y-4">
                    <Card>
                        <CardHeader>
                            <CardTitle>Your Documents</CardTitle>
                            <CardDescription>
                                View and manage your uploaded documents
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <DocumentList
                                accessToken={accessToken}
                                refreshTrigger={refreshKey}
                            />
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="upload" className="space-y-4">
                    <Card>
                        <CardHeader>
                            <CardTitle>Upload Document</CardTitle>
                            <CardDescription>
                                Upload a PDF or document file to add to your knowledge base
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <DocumentUpload
                                accessToken={accessToken}
                                onUploadSuccess={handleUploadSuccess}
                            />
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}
