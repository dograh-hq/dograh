"use client";

import { MessageSquare } from 'lucide-react';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export default function LoopTalkPage() {
    return (
        <div className="container mx-auto p-6 space-y-6">
            <div>
                <h1 className="text-3xl font-bold mb-2">LoopTalk</h1>
                <p>Enable voice agents to talk to each other and create artificial datasets</p>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Coming Soon</CardTitle>
                    <CardDescription>
                        LoopTalk features are currently under development
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="text-center py-12">
                        <MessageSquare className="w-16 h-16 mx-auto mb-6" />
                        <p className="text-lg mb-4">
                            We&apos;re building LoopTalk to enable voice agents to communicate with each other,
                            allowing you to generate artificial datasets for training and testing.
                        </p>
                        <p>
                            This powerful feature will help you create comprehensive test scenarios and improve your voice AI workflows.
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
