"use client";

import { Plus } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { getCampaignsApiV1CampaignGet } from '@/client/sdk.gen';
import type { CampaignsResponse } from '@/client/types.gen';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table';
import { useAuth } from '@/lib/auth';

export default function CampaignsPage() {
    const { user, getAccessToken, redirectToLogin, loading } = useAuth();
    const router = useRouter();

    const [campaignsData, setCampaignsData] = useState<CampaignsResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const hasFetched = useRef(false);

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    // Fetch campaigns once when user is ready
    useEffect(() => {
        if (loading || !user || hasFetched.current) {
            return;
        }
        hasFetched.current = true;

        const fetchCampaigns = async () => {
            setIsLoading(true);
            try {
                const accessToken = await getAccessToken();
                const response = await getCampaignsApiV1CampaignGet({
                    headers: {
                        'Authorization': `Bearer ${accessToken}`,
                    }
                });

                if (response.data) {
                    setCampaignsData(response.data);
                }
            } catch (error) {
                console.error('Failed to fetch campaigns:', error);
            } finally {
                setIsLoading(false);
            }
        };

        fetchCampaigns();
    }, [loading, user, getAccessToken]);

    const handleRowClick = (campaignId: number) => {
        router.push(`/campaigns/${campaignId}`);
    };

    const handleCreateCampaign = () => {
        router.push('/campaigns/new');
    };

    const formatDate = (dateString: string) => {
        return new Date(dateString).toLocaleDateString();
    };

    const getStateBadgeVariant = (state: string) => {
        switch (state) {
            case 'created':
                return 'secondary';
            case 'running':
                return 'default';
            case 'paused':
                return 'outline';
            case 'completed':
                return 'secondary';
            case 'failed':
                return 'destructive';
            default:
                return 'secondary';
        }
    };

    return (
        <div className="container mx-auto space-y-8 px-4 py-10">
            <div className="flex flex-wrap items-end justify-between gap-4">
                <div className="space-y-1">
                    <p className="text-eyebrow text-muted-foreground">Outbound</p>
                    <h1 className="text-h1 text-foreground">Campaigns</h1>
                    <p className="text-body text-muted-foreground">
                        Manage your bulk workflow execution campaigns.
                    </p>
                </div>
                <Button onClick={handleCreateCampaign}>
                    <Plus className="mr-2 h-4 w-4" />
                    Create Campaign
                </Button>
            </div>

            <Card className="rounded-2xl border border-border/60 bg-card shadow-[var(--shadow-card)] transition-all duration-200">
                <CardHeader>
                    <CardTitle className="text-h3">All Campaigns</CardTitle>
                    <CardDescription className="text-small">
                        View and manage your campaigns
                    </CardDescription>
                </CardHeader>
                <CardContent className="p-0">
                    {isLoading ? (
                        <div className="animate-pulse space-y-3 px-6 pb-6">
                            {[...Array(5)].map((_, i) => (
                                <div key={i} className="h-12 rounded-lg bg-muted" />
                            ))}
                        </div>
                    ) : campaignsData && campaignsData.campaigns.length > 0 ? (
                        <div className="overflow-x-auto border-t border-border/50">
                            <Table>
                                <TableHeader>
                                    <TableRow className="border-border/50 bg-muted/40 hover:bg-muted/40">
                                        <TableHead className="text-label text-muted-foreground">ID</TableHead>
                                        <TableHead className="text-label text-muted-foreground">Name</TableHead>
                                        <TableHead className="text-label text-muted-foreground">Workflow</TableHead>
                                        <TableHead className="text-label text-muted-foreground">State</TableHead>
                                        <TableHead className="text-label text-muted-foreground">Progress</TableHead>
                                        <TableHead className="text-label text-muted-foreground">Spent</TableHead>
                                        <TableHead className="text-label text-muted-foreground">Created</TableHead>
                                        <TableHead className="text-label text-right text-muted-foreground">Action</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {campaignsData.campaigns.map((campaign) => (
                                        <TableRow
                                            key={campaign.id}
                                            className="cursor-pointer border-border/50 transition-colors duration-200 hover:bg-muted/40"
                                            onClick={() => handleRowClick(campaign.id)}
                                        >
                                            <TableCell className="py-3.5 tabular-nums text-muted-foreground">{campaign.id}</TableCell>
                                            <TableCell className="py-3.5 font-medium text-foreground">{campaign.name}</TableCell>
                                            <TableCell className="py-3.5 text-muted-foreground">{campaign.workflow_name}</TableCell>
                                            <TableCell className="py-3.5">
                                                <Badge variant={getStateBadgeVariant(campaign.state)} className="capitalize">
                                                    {campaign.state}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="py-3.5 tabular-nums text-muted-foreground">
                                                {campaign.executed_count} / {campaign.total_queued_count}
                                            </TableCell>
                                            <TableCell className="py-3.5 tabular-nums text-muted-foreground">
                                                ₹{(campaign.spent_inr ?? 0).toLocaleString()}
                                                {campaign.spent_minutes ? (
                                                    <span className="ml-1 text-xs text-muted-foreground/70">
                                                        ({campaign.spent_minutes} min)
                                                    </span>
                                                ) : null}
                                            </TableCell>
                                            <TableCell className="py-3.5 tabular-nums text-muted-foreground">{formatDate(campaign.created_at)}</TableCell>
                                            <TableCell className="py-3.5 text-right">
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        handleRowClick(campaign.id);
                                                    }}
                                                >
                                                    View
                                                </Button>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    ) : (
                        <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
                            <div className="space-y-1">
                                <p className="text-body font-medium text-foreground">No campaigns yet</p>
                                <p className="text-small text-muted-foreground">
                                    Launch your first campaign to start reaching contacts.
                                </p>
                            </div>
                            <Button onClick={handleCreateCampaign} variant="outline">
                                <Plus className="mr-2 h-4 w-4" />
                                Create your first campaign
                            </Button>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
