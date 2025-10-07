"use client";

import { Plus } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

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

    // Campaigns state
    const [campaignsData, setCampaignsData] = useState<CampaignsResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    // Fetch campaigns
    const fetchCampaigns = useCallback(async () => {
        if (!user) return;
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
    }, [user, getAccessToken]);

    // Initial load
    useEffect(() => {
        if (user) {
            fetchCampaigns();
        }
    }, [fetchCampaigns, user]);

    // Handle row click to navigate to campaign detail
    const handleRowClick = (campaignId: number) => {
        router.push(`/campaigns/${campaignId}`);
    };

    // Handle create campaign button
    const handleCreateCampaign = () => {
        router.push('/campaigns/new');
    };

    // Format date for display
    const formatDate = (dateString: string) => {
        return new Date(dateString).toLocaleDateString();
    };

    // Get badge variant for state
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
        <div className="container mx-auto p-6 space-y-6">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold text-gray-900 mb-2">Campaigns</h1>
                    <p className="text-gray-600">Manage your bulk workflow execution campaigns</p>
                </div>
                    <Button onClick={handleCreateCampaign}>
                        <Plus className="h-4 w-4 mr-2" />
                        Create Campaign
                    </Button>
                </div>

                {/* Campaigns Table */}
                <Card>
                    <CardHeader>
                        <CardTitle>All Campaigns</CardTitle>
                        <CardDescription>
                            View and manage your campaigns
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        {isLoading ? (
                            <div className="animate-pulse space-y-3">
                                {[...Array(5)].map((_, i) => (
                                    <div key={i} className="h-12 bg-gray-200 rounded"></div>
                                ))}
                            </div>
                        ) : campaignsData && campaignsData.campaigns.length > 0 ? (
                            <div className="overflow-x-auto">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Name</TableHead>
                                            <TableHead>Workflow</TableHead>
                                            <TableHead>State</TableHead>
                                            <TableHead>Created</TableHead>
                                            <TableHead className="text-right">Action</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {campaignsData.campaigns.map((campaign) => (
                                            <TableRow
                                                key={campaign.id}
                                                className="cursor-pointer hover:bg-gray-50"
                                                onClick={() => handleRowClick(campaign.id)}
                                            >
                                                <TableCell className="font-medium">{campaign.name}</TableCell>
                                                <TableCell>{campaign.workflow_name}</TableCell>
                                                <TableCell>
                                                    <Badge variant={getStateBadgeVariant(campaign.state)}>
                                                        {campaign.state}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell>{formatDate(campaign.created_at)}</TableCell>
                                                <TableCell className="text-right">
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
                            <div className="text-center py-8">
                                <p className="text-gray-500 mb-4">No campaigns found</p>
                                <Button onClick={handleCreateCampaign} variant="outline">
                                    <Plus className="h-4 w-4 mr-2" />
                                    Create your first campaign
                                </Button>
                            </div>
                        )}
                    </CardContent>
                </Card>
        </div>
    );
}
