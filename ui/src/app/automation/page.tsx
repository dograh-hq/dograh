"use client";

import { Zap } from 'lucide-react';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export default function AutomationPage() {
    return (
        <div className="container mx-auto p-6 space-y-6">
            <div>
                <h1 className="text-3xl font-bold mb-2">Automation</h1>
                <p>Automate your workflows and processes</p>
            </div>

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
        </div>
    );
}
