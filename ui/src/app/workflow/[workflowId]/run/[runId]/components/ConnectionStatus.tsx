import { Loader2 } from 'lucide-react';

interface ConnectionStatusProps {
    connectionStatus: 'idle' | 'connecting' | 'connected' | 'failed';
}

export const ConnectionStatus = ({ connectionStatus }: ConnectionStatusProps) => {
    const t = useTranslations("workflowList");
    if (connectionStatus === 'idle') return null;

    if (connectionStatus === 'connecting') {
        return (
            <div className="flex items-center justify-center space-x-2 text-blue-600">
                <Loader2 className="h-5 w-5 animate-spin" />
                <span className="text-sm font-medium">{t("establishingConnection")}</span>
            </div>
        );
    }

    if (connectionStatus === 'connected') {
        return (
            <div className="flex items-center justify-center space-x-2 text-green-600">
                <div className="h-2 w-2 bg-green-600 rounded-full animate-pulse" />
                <span className="text-sm font-medium">{t("connected")}</span>
            </div>
        );
    }

    if (connectionStatus === 'failed') {
        return (
            <div className="flex items-center justify-center space-x-2 text-red-600">
                <div className="h-2 w-2 bg-red-600 rounded-full" />
                <span className="text-sm font-medium">{t("connectionFailed")}</span>
            </div>
        );
    }

    return null;
};
