"use client";

import Link from 'next/link';

import { GitHubStarBadge } from '@/components/layout/GitHubStarBadge';
import { PageLayout } from '@/components/layout/PageLayout';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/lib/auth';

export default function OverviewPage() {
    const { user, provider } = useAuth();
    const isOSSMode = provider !== 'stack';

    return (
        <PageLayout
            title={isOSSMode ? "Welcome to Dograh" : `Welcome${user?.displayName ? `, ${user.displayName.split(' ')[0]}` : ''}!`}
            description={isOSSMode ? "Open source alternative to Vapi. Help us support the project by giving us a star on GitHub." : "Get started with building voice AI workflows"}
            actions={isOSSMode && <GitHubStarBadge label="Star us on GitHub" showCount source="overview_page" />}>
            {/* Quick Actions */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <Card>
                    <CardHeader>
                        <CardTitle>Create and Manage your Voice Agents</CardTitle>
                        <CardDescription>
                            Build powerful AI Voice Agents with our visual editor
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <Button asChild>
                            <Link href="/workflow">
                                Go to Agents
                            </Link>
                        </Button>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Configure Services</CardTitle>
                        <CardDescription>
                            Set up your AI services like LLM, TTS, and STT providers
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <Button asChild variant="outline">
                            <Link href="/model-configurations">
                                Configure Models
                            </Link>
                        </Button>
                    </CardContent>
                </Card>
            </div>

            {/* Resources Section */}
            <Card>
                <CardHeader>
                    <CardTitle>Resources</CardTitle>
                    <CardDescription>
                        Get help and learn more about Dograh
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex flex-wrap gap-4">
                        <Button asChild variant="outline">
                            <a
                                href="https://docs.dograh.com"
                                target="_blank"
                                rel="noopener noreferrer"
                            >
                                Documentation
                            </a>
                        </Button>
                        <Button asChild variant="outline">
                            <a
                                href="https://github.com/dograh-hq/dograh/issues"
                                target="_blank"
                                rel="noopener noreferrer"
                            >
                                Report an Issue
                            </a>
                        </Button>
                    </div>
                </CardContent>
            </Card>
        </PageLayout>
    );
}
