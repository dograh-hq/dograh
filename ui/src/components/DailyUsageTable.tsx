import type { DailyUsageBreakdownResponse } from '@/client/types.gen';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
    Table,
    TableBody,
    TableCell,
    TableFooter,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table';
import { formatCalendarDate } from '@/lib/dateTime';

interface DailyUsageTableProps {
    data: DailyUsageBreakdownResponse | null;
    isLoading: boolean;
}

export function DailyUsageTable({ data, isLoading }: DailyUsageTableProps) {
    if (isLoading) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle>Daily Usage Breakdown</CardTitle>
                    <CardDescription>Last 7 days of usage</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="animate-pulse space-y-3">
                        {[...Array(7)].map((_, i) => (
                            <div key={i} className="h-12 bg-gray-200 rounded"></div>
                        ))}
                    </div>
                </CardContent>
            </Card>
        );
    }

    if (!data || !data.breakdown || data.breakdown.length === 0) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle>Daily Usage Breakdown</CardTitle>
                    <CardDescription>Last 7 days of usage</CardDescription>
                </CardHeader>
                <CardContent>
                    <p className="text-center py-8 text-gray-500">No usage data available</p>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card>
            <CardHeader>
                <CardTitle>Daily Usage Breakdown</CardTitle>
                <CardDescription>Last 7 days of usage</CardDescription>
            </CardHeader>
            <CardContent>
                <div className="bg-white border rounded-lg overflow-hidden shadow-sm">
                    <Table>
                        <TableHeader>
                            <TableRow className="bg-gray-50">
                                <TableHead className="font-semibold">Date</TableHead>
                                <TableHead className="font-semibold text-right">Usage (minutes)</TableHead>
                                <TableHead className="font-semibold text-right">Cost (USD)</TableHead>
                                <TableHead className="font-semibold text-right">Calls</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {data.breakdown.map((day) => (
                                <TableRow key={day.date}>
                                    <TableCell className="font-medium">
                                        {formatCalendarDate(day.date)}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        {day.minutes.toFixed(1)}
                                    </TableCell>
                                    <TableCell className="text-right font-medium">
                                        ${(day.cost_usd || 0).toFixed(2)}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        {day.call_count}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                        <TableFooter>
                            <TableRow className="bg-gray-50 font-semibold">
                                <TableCell>Total</TableCell>
                                <TableCell className="text-right">
                                    {data.total_minutes.toFixed(1)}
                                </TableCell>
                                <TableCell className="text-right">
                                    ${(data.total_cost_usd || 0).toFixed(2)}
                                </TableCell>
                                <TableCell className="text-right">
                                    {data.breakdown.reduce((sum, day) => sum + day.call_count, 0)}
                                </TableCell>
                            </TableRow>
                        </TableFooter>
                    </Table>
                </div>
            </CardContent>
        </Card>
    );
}
