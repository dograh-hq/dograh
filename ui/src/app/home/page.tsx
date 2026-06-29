'use client';

import { ArrowUp, CalendarClock, Headphones, Home as HomeIcon, Loader2, Target } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { HomeMetrics } from '@/components/home/HomeMetrics';
import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import {
    type AgentTemplate,
    createAgent,
    listAgentTemplates,
} from '@/lib/api/agentBuilder';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';

const LANGUAGE_OPTIONS = [
    { value: 'Hinglish (a natural, friendly Hindi-English mix)', label: 'Hinglish (Hindi-English mix)' },
    { value: 'Hindi', label: 'Hindi' },
    { value: 'English', label: 'English' },
];

const TEMPLATE_ICONS: Record<string, typeof HomeIcon> = {
    real_estate_cold_caller: HomeIcon,
    appointment_setter: CalendarClock,
    lead_qualifier: Target,
    support_callback: Headphones,
};

export default function HomePage() {
    const router = useRouter();
    const { user, loading: authLoading, getAccessToken } = useAuth();

    // Describe-mode state
    const [description, setDescription] = useState('');
    const [describeBusinessName, setDescribeBusinessName] = useState('');
    const [isCreating, setIsCreating] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Templates
    const [templates, setTemplates] = useState<AgentTemplate[]>([]);
    const [templatesLoading, setTemplatesLoading] = useState(true);
    const [templatesError, setTemplatesError] = useState<string | null>(null);
    const hasFetched = useRef(false);

    // Template dialog state
    const [selectedTemplate, setSelectedTemplate] = useState<AgentTemplate | null>(null);
    const [businessName, setBusinessName] = useState('');
    const [industry, setIndustry] = useState('');
    const [details, setDetails] = useState('');
    const [language, setLanguage] = useState(LANGUAGE_OPTIONS[0].value);
    const [dialogError, setDialogError] = useState<string | null>(null);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;

        const fetchTemplates = async () => {
            try {
                const accessToken = await getAccessToken();
                const data = await listAgentTemplates(accessToken);
                setTemplates(data);
            } catch (err) {
                logger.error(`Error loading agent templates: ${err}`);
                setTemplatesError('Could not load templates. Please refresh the page.');
            } finally {
                setTemplatesLoading(false);
            }
        };
        fetchTemplates();
    }, [authLoading, user, getAccessToken]);

    const redirectToWorkflow = (workflowId: number) => {
        router.push(`/workflow/${workflowId}`);
    };

    const handleDescribeCreate = async () => {
        if (!description.trim() || !describeBusinessName.trim() || isCreating) return;
        setIsCreating(true);
        setError(null);
        try {
            const accessToken = await getAccessToken();
            const result = await createAgent(
                {
                    mode: 'describe',
                    description: description.trim(),
                    business: { name: describeBusinessName.trim() },
                },
                accessToken,
            );
            redirectToWorkflow(result.workflow_id);
        } catch (err) {
            logger.error(`Error creating agent from description: ${err}`);
            setError(err instanceof Error ? err.message : 'Failed to create the agent. Please try again.');
            setIsCreating(false);
        }
    };

    const openTemplateDialog = (template: AgentTemplate) => {
        setSelectedTemplate(template);
        setBusinessName('');
        setIndustry('');
        setDetails('');
        setLanguage(LANGUAGE_OPTIONS[0].value);
        setDialogError(null);
    };

    const handleTemplateCreate = async () => {
        if (!selectedTemplate || !businessName.trim() || isCreating) return;
        setIsCreating(true);
        setDialogError(null);
        try {
            const accessToken = await getAccessToken();
            const result = await createAgent(
                {
                    mode: 'template',
                    template_id: selectedTemplate.id,
                    business: {
                        name: businessName.trim(),
                        industry: industry.trim() || undefined,
                        details: details.trim() || undefined,
                        language,
                    },
                },
                accessToken,
            );
            redirectToWorkflow(result.workflow_id);
        } catch (err) {
            logger.error(`Error creating agent from template: ${err}`);
            setDialogError(err instanceof Error ? err.message : 'Failed to create the agent. Please try again.');
            setIsCreating(false);
        }
    };

    return (
        <div className="min-h-screen bg-background">
            <div className="container mx-auto px-4 py-12 max-w-4xl">
                {/* Dashboard — at-a-glance metrics */}
                <div className="mb-12">
                    <p className="text-eyebrow text-primary">Dashboard</p>
                    <h1 className="text-h1 mt-1">Welcome back</h1>
                    <p className="text-body mt-1 text-muted-foreground">
                        Here&apos;s how your voice agents are performing.
                    </p>
                    <div className="mt-6">
                        <HomeMetrics />
                    </div>
                </div>

                {/* Hero — describe your agent */}
                <div className="text-center mb-8">
                    <p className="text-eyebrow text-primary">Build an agent</p>
                    <h2 className="text-h2 mt-1 mb-2">
                        What should your voice agent do?
                    </h2>
                    <p className="text-body text-muted-foreground">
                        Describe the agent you want and we&apos;ll build a working voice agent for you.
                    </p>
                </div>

                <div className="mx-auto max-w-2xl mb-16">
                    <div className="rounded-2xl border border-border/60 bg-card shadow-[var(--shadow-card)] transition-all duration-200 focus-within:border-border focus-within:shadow-[var(--shadow-pop)]">
                        <Textarea
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            placeholder="Describe the agent you want… e.g. Call my property leads, pitch our new 2BHK project in Pune and book site visits."
                            className="min-h-[120px] resize-none border-0 bg-transparent shadow-none focus-visible:ring-0 text-base p-5"
                            disabled={isCreating}
                        />
                        <div className="flex items-center gap-2 border-t border-border/50 px-3 py-2.5">
                            <Input
                                value={describeBusinessName}
                                onChange={(e) => setDescribeBusinessName(e.target.value)}
                                placeholder="Your business name"
                                className="h-9 max-w-[220px] border-0 bg-muted/60 shadow-none focus-visible:ring-0"
                                disabled={isCreating}
                            />
                            <div className="flex-1" />
                            <Button
                                size="sm"
                                onClick={handleDescribeCreate}
                                disabled={isCreating || !description.trim() || !describeBusinessName.trim()}
                                className="rounded-full bg-cta text-cta-foreground hover:bg-cta/90 transition-all duration-200"
                            >
                                {isCreating ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                    <>
                                        Create
                                        <ArrowUp className="h-4 w-4 ml-1" />
                                    </>
                                )}
                            </Button>
                        </div>
                    </div>
                    {error && <p className="mt-3 text-small text-destructive text-center">{error}</p>}
                </div>

                {/* Templates */}
                <div className="mb-6">
                    <p className="text-eyebrow text-primary">Templates</p>
                    <h2 className="text-h3 mt-1">Or start from a template</h2>
                    <p className="text-body mt-1 text-muted-foreground">
                        Pick a ready-made agent and fill in your business details.
                    </p>
                </div>

                {templatesError && (
                    <p className="text-small text-destructive mb-4">{templatesError}</p>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {templatesLoading
                        ? Array.from({ length: 4 }).map((_, i) => (
                              <Card
                                  key={i}
                                  className="rounded-2xl border-border/60 bg-card shadow-[var(--shadow-card)]"
                              >
                                  <CardHeader>
                                      <div className="flex items-center gap-3">
                                          <Skeleton className="h-10 w-10 shrink-0 rounded-xl" />
                                          <Skeleton className="h-5 w-40" />
                                      </div>
                                      <Skeleton className="mt-1 h-4 w-full" />
                                      <Skeleton className="h-4 w-2/3" />
                                  </CardHeader>
                              </Card>
                          ))
                        : templates.map((template) => {
                              const Icon = TEMPLATE_ICONS[template.id] ?? Target;
                              return (
                                  <Card
                                      key={template.id}
                                      role="button"
                                      tabIndex={0}
                                      onClick={() => openTemplateDialog(template)}
                                      onKeyDown={(e) => {
                                          if (e.key === 'Enter' || e.key === ' ') {
                                              e.preventDefault();
                                              openTemplateDialog(template);
                                          }
                                      }}
                                      className="group cursor-pointer rounded-2xl border-border/60 bg-card shadow-[var(--shadow-card)] transition-all duration-200 hover:-translate-y-0.5 hover:border-border hover:shadow-[var(--shadow-pop)] focus-visible:ring-1 focus-visible:ring-ring outline-none"
                                  >
                                      <CardHeader>
                                          <div className="flex items-center gap-3">
                                              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border/60 bg-muted text-muted-foreground transition-colors duration-200 group-hover:border-primary/30 group-hover:bg-accent group-hover:text-primary">
                                                  <Icon className="h-[18px] w-[18px]" />
                                              </div>
                                              <CardTitle className="text-base">{template.name}</CardTitle>
                                          </div>
                                          <CardDescription className="pt-1">
                                              {template.description}
                                          </CardDescription>
                                      </CardHeader>
                                  </Card>
                              );
                          })}
                </div>
            </div>

            {/* Template details dialog */}
            <Dialog
                open={selectedTemplate !== null}
                onOpenChange={(open) => {
                    if (!open && !isCreating) setSelectedTemplate(null);
                }}
            >
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>{selectedTemplate?.name}</DialogTitle>
                        <DialogDescription>
                            Tell us about your business so we can personalise this agent.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-2">
                        <div className="space-y-2">
                            <Label htmlFor="business-name">Business name</Label>
                            <Input
                                id="business-name"
                                placeholder="e.g. Sunrise Homes"
                                value={businessName}
                                onChange={(e) => setBusinessName(e.target.value)}
                                disabled={isCreating}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="industry">Industry (optional)</Label>
                            <Input
                                id="industry"
                                placeholder="e.g. Real estate"
                                value={industry}
                                onChange={(e) => setIndustry(e.target.value)}
                                disabled={isCreating}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="details">Business details (optional)</Label>
                            <Textarea
                                id="details"
                                placeholder="What you sell, offers, prices, locations — anything the agent should know."
                                value={details}
                                onChange={(e) => setDetails(e.target.value)}
                                className="min-h-[90px]"
                                disabled={isCreating}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="language">Language</Label>
                            <Select value={language} onValueChange={setLanguage} disabled={isCreating}>
                                <SelectTrigger id="language">
                                    <SelectValue placeholder="Select language" />
                                </SelectTrigger>
                                <SelectContent>
                                    {LANGUAGE_OPTIONS.map((option) => (
                                        <SelectItem key={option.value} value={option.value}>
                                            {option.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        {dialogError && <p className="text-sm text-destructive">{dialogError}</p>}
                    </div>

                    <DialogFooter>
                        <Button
                            onClick={handleTemplateCreate}
                            disabled={isCreating || !businessName.trim()}
                            className="w-full"
                        >
                            {isCreating ? (
                                <>
                                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                                    Creating agent…
                                </>
                            ) : (
                                'Create agent'
                            )}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
