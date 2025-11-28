import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface ContextDisplayProps {
    title: string;
    context: Record<string, string | number | boolean | object> | null;
}

export const ContextDisplay = ({ title, context }: ContextDisplayProps) => {
    if (!context || Object.keys(context).length === 0) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle className="text-lg">{title}</CardTitle>
                </CardHeader>
                <CardContent>
                    <p className="text-sm text-muted-foreground">No {title.toLowerCase()} available</p>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card>
            <CardHeader>
                <CardTitle className="text-lg">{title}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
                {Object.entries(context).map(([key, value]) => (
                    <div key={key} className="space-y-1">
                        <label className="text-sm font-medium text-muted-foreground">
                            {key}
                        </label>
                        <div className="p-3 bg-muted border rounded-md">
                            <p className="text-sm whitespace-pre-wrap">
                                {typeof value === 'object' && value !== null ? JSON.stringify(value, null, 2) : (value || 'No value')}
                            </p>
                        </div>
                    </div>
                ))}
            </CardContent>
        </Card>
    );
};
