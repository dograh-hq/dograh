"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export default function AutomationPage() {
    return (
        <div className="container mx-auto p-6 space-y-6">
            <div>
                <h1 className="text-3xl font-bold text-gray-900 mb-2">Automation</h1>
                <p className="text-gray-600">Automate your workflows and processes</p>
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
                            <p className="text-gray-500 text-lg mb-4">
                                We&apos;re working on powerful automation features to help you streamline your workflows.
                            </p>
                            <p className="text-gray-500">
                                Check back soon for updates!
                            </p>
                        </div>
                    </CardContent>
                </Card>
        </div>
    );
}
