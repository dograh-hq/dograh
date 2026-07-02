"use client";

import { useTranslations } from 'next-intl';
import { Zap } from 'lucide-react';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export default function AutomationPage() {
    const t = useTranslations('automation');
    return (
        <div className="container mx-auto p-6 space-y-6">
            <div>
                <h1 className="text-3xl font-bold mb-2">{t('title')}</h1>
                <p>{t('description')}</p>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>{t('comingSoon')}</CardTitle>
                    <CardDescription>
                        {t('comingSoon')}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="text-center py-12">
                        <Zap className="w-16 h-16 mx-auto mb-6" />
                        <p className="text-lg mb-4">
                            {t('comingSoonMessage1')}
                        </p>
                        <p>
                            {t('comingSoonMessage2')}
                        </p>
                        <p className="mt-4">
                            {t('comingSoonMessage3')}
                        </p>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
