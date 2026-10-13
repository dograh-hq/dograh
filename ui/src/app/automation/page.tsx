"use client";

import { Zap } from 'lucide-react';

import { PageLayout } from '@/components/layout/PageLayout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export default function AutomationPage() {
    return (
        <PageLayout title="Automation" description="Automate your workflows and processes">

            <Card>
                <CardHeader>
                    <CardTitle>Coming Soon</CardTitle>
                    <CardDescription>
                        Automation features are currently under development
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="text-center py-12">
                        <Zap className="w-16 h-16 mx-auto mb-6" />
                        <p className="text-lg mb-4">
                            We&apos;re working on powerful automation features to help you streamline your workflows.
                        </p>
                        <p>
                            Automate repetitive tasks, trigger actions based on events, and create intelligent workflow pipelines.
                        </p>
                        <p className="mt-4">
                            Check back soon for updates!
                        </p>
                    </div>
                </CardContent>
            </Card>
        </PageLayout>
    );
}
