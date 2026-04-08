'use client';

import { addDays, format, subDays } from 'date-fns';
import { Calendar, ChevronLeft, ChevronRight, Download } from 'lucide-react';
import { useEffect,useState } from 'react';

import {
  getDailyReportApiV1OrganizationsReportsDailyGet,
  getDailyRunsDetailApiV1OrganizationsReportsDailyRunsGet,
  getWorkflowOptionsApiV1OrganizationsReportsWorkflowsGet
} from '@/client/sdk.gen';
import type { WorkflowRunDetail } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Calendar as CalendarPicker } from '@/components/ui/calendar';
import { Card } from '@/components/ui/card';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { useUserConfig } from '@/context/UserConfigContext';
import { useAuth } from '@/lib/auth';

import { DispositionChart } from './components/DispositionChart';
import { DurationChart } from './components/DurationChart';
import { MetricsCards } from './components/MetricsCards';

interface WorkflowOption {
  id: number;
  name: string;
}

interface DailyReport {
  date: string;
  timezone: string;
  workflow_id: number | null;
  metrics: {
    total_runs: number;
    xfer_count: number;
  };
  disposition_distribution: Array<{
    disposition: string;
    count: number;
    percentage: number;
  }>;
  call_duration_distribution: Array<{
    bucket: string;
    range_start: number;
    range_end: number | null;
    count: number;
    percentage: number;
  }>;
}

export default function ReportsPage() {
  const [selectedDate, setSelectedDate] = useState<Date>(new Date());
  const [selectedWorkflow, setSelectedWorkflow] = useState<string>('all');
  const [workflows, setWorkflows] = useState<WorkflowOption[]>([]);
  const [report, setReport] = useState<DailyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { userConfig } = useUserConfig();
  const auth = useAuth();

  const timezone = userConfig?.timezone || 'America/New_York';

  // Fetch workflows on mount
  useEffect(() => {
    const fetchWorkflows = async () => {
      if (!auth.isAuthenticated) return;

      try {
        const response = await getWorkflowOptionsApiV1OrganizationsReportsWorkflowsGet({
        });
        if (response.data) {
          setWorkflows(response.data);
        }
      } catch (err) {
        console.error('Failed to fetch workflows:', err);
      }
    };
    fetchWorkflows();
  }, [auth.isAuthenticated]);

  // Fetch report data when date or workflow changes
  useEffect(() => {
    const fetchReport = async () => {
      if (!auth.isAuthenticated) return;

      setLoading(true);
      setError(null);

      try {
        const dateStr = format(selectedDate, 'yyyy-MM-dd');
        const workflowId = selectedWorkflow === 'all' ? undefined : parseInt(selectedWorkflow);

        const response = await getDailyReportApiV1OrganizationsReportsDailyGet({
          query: {
            date: dateStr,
            timezone,
            ...(workflowId && { workflow_id: workflowId })
          },
        });

        if (response.data) {
          setReport(response.data as DailyReport);
        }
      } catch (err) {
        console.error('Failed to fetch report:', err);
        setError('Failed to load report data');
      } finally {
        setLoading(false);
      }
    };

    fetchReport();
  }, [selectedDate, selectedWorkflow, timezone, auth.isAuthenticated]);

  const handlePreviousDay = () => {
    setSelectedDate(subDays(selectedDate, 1));
  };

  const handleNextDay = () => {
    setSelectedDate(addDays(selectedDate, 1));
  };

  const handleDownloadCSV = async () => {
    if (!auth.isAuthenticated) return;

    try {
      const dateStr = format(selectedDate, 'yyyy-MM-dd');
      const workflowId = selectedWorkflow === 'all' ? undefined : parseInt(selectedWorkflow);

      // Fetch detailed runs data
      const response = await getDailyRunsDetailApiV1OrganizationsReportsDailyRunsGet({
        query: {
          date: dateStr,
          timezone,
          ...(workflowId && { workflow_id: workflowId })
        },
      });

      if (response.data && response.data.length > 0) {
        // Prepare CSV content
        const headers = ['Phone Number', 'Disposition', 'Duration (seconds)', 'Workflow Run URL'];
        const rows = response.data.map((run: WorkflowRunDetail) => {
          const url = `${window.location.origin}/workflow/${run.workflow_id}/run/${run.run_id}`;
          return [
            run.phone_number || '',
            run.disposition || '',
            run.duration_seconds.toString(),
            url
          ];
        });

        // Create CSV content
        const csvContent = [
          headers.join(','),
          ...rows.map((row: string[]) => row.map((cell: string) => `"${cell}"`).join(','))
        ].join('\n');

        // Create blob and download
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);

        const workflowName = selectedWorkflow === 'all'
          ? 'all_workflows'
          : workflows.find(w => w.id.toString() === selectedWorkflow)?.name?.replace(/\s+/g, '_') || 'workflow';

        link.setAttribute('href', url);
        link.setAttribute('download', `workflow_runs_${dateStr}_${workflowName}.csv`);
        link.style.visibility = 'hidden';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
      } else {
        alert('No data available for download');
      }
    } catch (err) {
      console.error('Failed to download CSV:', err);
      alert('Failed to download CSV data');
    }
  };

  const isToday = format(selectedDate, 'yyyy-MM-dd') === format(new Date(), 'yyyy-MM-dd');

  return (
    <div className="container mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <h1 className="text-3xl font-bold">Daily Reports</h1>

        {/* Date Navigation & Workflow Selector */}
        <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center">
          {/* Workflow Selector */}
          <Select value={selectedWorkflow} onValueChange={setSelectedWorkflow}>
            <SelectTrigger className="w-[200px]">
              <SelectValue placeholder="Select workflow" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Workflows</SelectItem>
              {workflows.map((workflow) => (
                <SelectItem key={workflow.id} value={workflow.id.toString()}>
                  {workflow.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Date Navigation */}
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="icon"
              onClick={handlePreviousDay}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>

            <Popover>
              <PopoverTrigger asChild>
                <Button variant="outline" className="w-[200px]">
                  <Calendar className="mr-2 h-4 w-4" />
                  {format(selectedDate, 'MMM dd, yyyy')}
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-auto p-0">
                <CalendarPicker
                  mode="single"
                  selected={selectedDate}
                  onSelect={(date) => date && setSelectedDate(date)}
                  disabled={(date) => date > new Date()}
                />
              </PopoverContent>
            </Popover>

            <Button
              variant="outline"
              size="icon"
              onClick={handleNextDay}
              disabled={isToday}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>

      {/* Timezone Display and Download Button */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
        <div className="text-sm text-muted-foreground">
          Showing data for {timezone} timezone
          {selectedWorkflow !== 'all' && (
            <span> • Filtered by: {workflows.find(w => w.id.toString() === selectedWorkflow)?.name}</span>
          )}
        </div>

        {/* Download CSV Button */}
        {!loading && report && report.metrics.total_runs > 0 && (
          <Button
            variant="outline"
            size="sm"
            onClick={handleDownloadCSV}
            className="flex items-center gap-2"
          >
            <Download className="h-4 w-4" />
            Download CSV
          </Button>
        )}
      </div>

      {/* Loading State */}
      {loading && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Skeleton className="h-[120px]" />
            <Skeleton className="h-[120px]" />
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Skeleton className="h-[300px]" />
            <Skeleton className="h-[300px]" />
          </div>
        </div>
      )}

      {/* Error State */}
      {error && !loading && (
        <Card className="p-6">
          <p className="text-center text-red-500">{error}</p>
        </Card>
      )}

      {/* Report Content */}
      {report && !loading && !error && (
        <>
          {/* Metrics Cards */}
          <MetricsCards metrics={report.metrics} />

          {/* Charts */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <DispositionChart data={report.disposition_distribution} />
            <DurationChart data={report.call_duration_distribution} />
          </div>

          {/* No Data Message */}
          {report.metrics.total_runs === 0 && (
            <Card className="p-6">
              <p className="text-center text-muted-foreground">
                No workflow runs found for {format(selectedDate, 'MMMM dd, yyyy')}
                {selectedWorkflow !== 'all' && ' for the selected workflow'}
              </p>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
