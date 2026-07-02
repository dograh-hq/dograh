'use client';

import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';

interface WorkflowCardProps {
    id: number;
    name: string;
    createdAt: string;
}

export function WorkflowCard({ id, name, createdAt }: WorkflowCardProps) {
    const t = useTranslations("workflowList");
    const router = useRouter();

    const handleClick = () => {
        router.push(`/workflow/${id}`);
    };

    return (
        <div
            className="bg-white border rounded-lg overflow-hidden shadow-sm hover:shadow-md transition-all duration-200 p-4 cursor-pointer transform hover:-translate-y-1"
            onClick={handleClick}
        >
            <div>
                <h3 className="text-lg font-semibold mb-2">{name}</h3>
                <p className="text-gray-600 mb-2">
                    {t("created")} {new Date(createdAt).toLocaleDateString()}
                </p>
            </div>
        </div>
    );
}
