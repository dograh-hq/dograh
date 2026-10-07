"use client";

import { Info, PhoneForwarded, PhoneOff,ShieldCheck } from 'lucide-react';
import React from 'react';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { cn } from '@/lib/utils';

interface WhatsAppPermissionCardProps {
    value: 'skip' | 'request_and_wait';
    onChange: (value: 'skip' | 'request_and_wait') => void;
    className?: string;
}

export function WhatsAppPermissionCard({
    value = 'request_and_wait',
    onChange,
    className,
}: WhatsAppPermissionCardProps) {
    return (
        <Card className={cn("border-emerald-500/30 bg-emerald-50/30 dark:bg-emerald-950/10", className)}>
            <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <div className="p-1.5 rounded-md bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                            <ShieldCheck className="h-5 w-5" />
                        </div>
                        <div>
                            <CardTitle className="text-base flex items-center gap-2">
                                WhatsApp Call Permission Policy
                                <Badge variant="outline" className="text-[11px] border-emerald-500/40 text-emerald-700 dark:text-emerald-300 font-normal">
                                    WhatsApp Outbound
                                </Badge>
                            </CardTitle>
                            <CardDescription className="text-xs mt-0.5">
                                Meta requires recipient consent before placing business-initiated WhatsApp calls.
                            </CardDescription>
                        </div>
                    </div>
                </div>
            </CardHeader>

            <CardContent className="space-y-4">
                <RadioGroup
                    value={value}
                    onValueChange={(val) => onChange(val as 'skip' | 'request_and_wait')}
                    className="grid grid-cols-1 gap-3 sm:grid-cols-2"
                >
                    {/* Option 1: Request and Wait */}
                    <Label
                        htmlFor="perm-request-and-wait"
                        className={cn(
                            "flex flex-col justify-between p-4 rounded-lg border-2 cursor-pointer transition-all hover:bg-muted/40",
                            value === 'request_and_wait'
                                ? "border-emerald-500 bg-background shadow-xs"
                                : "border-muted bg-background/60 text-muted-foreground"
                        )}
                    >
                        <div className="space-y-2">
                            <div className="flex items-start justify-between">
                                <div className="flex items-center gap-2">
                                    <PhoneForwarded className={cn(
                                        "h-4 w-4 shrink-0",
                                        value === 'request_and_wait' ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"
                                    )} />
                                    <span className="font-semibold text-sm text-foreground">
                                        Request Permission & Dial
                                    </span>
                                </div>
                                <RadioGroupItem value="request_and_wait" id="perm-request-and-wait" />
                            </div>
                            <div className="flex items-center gap-1.5">
                                <Badge variant="success" className="text-[10px] py-0 px-2 font-medium">
                                    Recommended
                                </Badge>
                            </div>
                            <p className="text-xs text-muted-foreground leading-relaxed">
                                If permission hasn&apos;t been granted yet, Dograh automatically sends a WhatsApp permission request first. The call connects automatically as soon as the recipient taps &ldquo;Allow&rdquo;.
                            </p>
                        </div>
                    </Label>

                    {/* Option 2: Skip */}
                    <Label
                        htmlFor="perm-skip"
                        className={cn(
                            "flex flex-col justify-between p-4 rounded-lg border-2 cursor-pointer transition-all hover:bg-muted/40",
                            value === 'skip'
                                ? "border-emerald-500 bg-background shadow-xs"
                                : "border-muted bg-background/60 text-muted-foreground"
                        )}
                    >
                        <div className="space-y-2">
                            <div className="flex items-start justify-between">
                                <div className="flex items-center gap-2">
                                    <PhoneOff className={cn(
                                        "h-4 w-4 shrink-0",
                                        value === 'skip' ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"
                                    )} />
                                    <span className="font-semibold text-sm text-foreground">
                                        Skip Leads Without Permission
                                    </span>
                                </div>
                                <RadioGroupItem value="skip" id="perm-skip" />
                            </div>
                            <div className="flex items-center gap-1.5">
                                <Badge variant="secondary" className="text-[10px] py-0 px-2 font-normal">
                                    Pre-approved Only
                                </Badge>
                            </div>
                            <p className="text-xs text-muted-foreground leading-relaxed">
                                Only dial leads who have already granted call permission in advance. Unapproved leads will be skipped without sending messages and marked with status &ldquo;no_permission&rdquo;.
                            </p>
                        </div>
                    </Label>
                </RadioGroup>

                <div className="flex items-start gap-2 p-2.5 rounded-md bg-muted/40 text-[11px] text-muted-foreground border border-muted">
                    <Info className="h-4 w-4 shrink-0 text-muted-foreground mt-0.5" />
                    <div>
                        <span>
                            Permission requests are delivered as interactive WhatsApp messages with &ldquo;Allow&rdquo; and &ldquo;Decline&rdquo; buttons. Meta grants a 24-hour calling window upon approval.
                        </span>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
