"use client";

import { CircleDollarSign, HelpCircle,Star } from 'lucide-react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import React from 'react';

import { useUserConfig } from '@/context/UserConfigContext';
import { useAuth } from '@/lib/auth';

// Conditionally load Stack components only when using Stack auth
const StackUserButton = React.lazy(() =>
    import('@stackframe/stack').then(mod => ({ default: mod.UserButton }))
);
const StackTeamSwitcher = React.lazy(() =>
    import('@stackframe/stack').then(mod => ({ default: mod.SelectedTeamSwitcher }))
);

interface BaseHeaderProps {
    headerActions?: React.ReactNode,
    backButton?: React.ReactNode,
    showFeaturesNav?: boolean
}

export default function BaseHeader({ headerActions, backButton, showFeaturesNav = true }: BaseHeaderProps) {
    const { permissions } = useUserConfig();
    const { provider, user } = useAuth();
    const pathname = usePathname();
    const router = useRouter();

    const isActive = (path: string) => {
        return pathname.startsWith(path);
    };

    const hasAdminPermission = Array.isArray(permissions) && permissions.some(p => p.id === 'admin');


    return (
        <header className="sticky top-0 z-50 w-full border-b border-gray-200 bg-white">
            <div className="container mx-auto px-4 py-4">
                <nav className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <Link href="/" className="text-xl font-semibold text-gray-800 hover:text-gray-600">
                            Dograh
                        </Link>
                        {backButton}
                        {showFeaturesNav && (
                            <div className="flex items-center gap-4 ml-8">
                                {hasAdminPermission && (
                                    <>
                                        <Link
                                            href="/workflow"
                                            className={`text-sm font-medium transition-colors hover:text-primary ${isActive('/workflow') ? 'text-primary' : 'text-gray-600'
                                                }`}
                                        >
                                            Workflows
                                        </Link>
                                        <Link
                                            href="/campaigns"
                                            className={`text-sm font-medium transition-colors hover:text-primary ${isActive('/campaigns') ? 'text-primary' : 'text-gray-600'
                                                }`}
                                        >
                                            Campaigns
                                        </Link>
                                        <Link
                                            href="/automation"
                                            className={`text-sm font-medium transition-colors hover:text-primary ${isActive('/automation') ? 'text-primary' : 'text-gray-600'
                                                }`}
                                        >
                                            Automation
                                        </Link>
                                        <Link
                                            href="/looptalk"
                                            className={`text-sm font-medium transition-colors hover:text-primary ${isActive('/looptalk') ? 'text-primary' : 'text-gray-600'
                                                }`}
                                        >
                                            LoopTalk
                                        </Link>
                                    </>
                                )}
                                <Link
                                    href="/usage"
                                    className={`text-sm font-medium transition-colors hover:text-primary ${isActive('/usage') ? 'text-primary' : 'text-gray-600'
                                        }`}
                                >
                                    Usage
                                </Link>
                                <Link
                                    href="/reports"
                                    className={`text-sm font-medium transition-colors hover:text-primary ${isActive('/reports') ? 'text-primary' : 'text-gray-600'
                                        }`}
                                >
                                    Reports
                                </Link>
                                <Link
                                    href="/api-keys"
                                    className={`text-sm font-medium transition-colors hover:text-primary ${isActive('/api-keys') ? 'text-primary' : 'text-gray-600'
                                        }`}
                                >
                                    Developers
                                </Link>
                            </div>
                        )}
                    </div>
                    <div className="flex-1 flex justify-center">
                        {headerActions}
                    </div>

                    {/* Use key to force remount when user changes to avoid hooks issues */}
                    <div className="flex items-center gap-5" key={user ? 'logged-in' : 'logged-out'}>
                        {provider === 'stack' ? (
                            <React.Suspense fallback={
                                <div className="flex items-center gap-5">
                                    {/* Match StackTeamSwitcher's internal skeleton */}
                                    <div className="h-9 w-40 animate-pulse bg-gray-100 rounded" />
                                    {/* Match StackUserButton dimensions: h-[34px] w-[34px] */}
                                    <div className="h-[34px] w-[34px] animate-pulse bg-gray-100 rounded-full" />
                                </div>
                            }>
                                <div className="w-40 shrink-0">
                                    <StackTeamSwitcher
                                        onChange={() => {
                                            router.refresh();
                                        }}
                                    />
                                </div>
                                <StackUserButton
                                    extraItems={[{
                                        text: 'Usage',
                                        icon: <CircleDollarSign strokeWidth={2} size={16} />,
                                        onClick: () => router.push('/usage')
                                    }]}
                                />
                            </React.Suspense>
                        ) : (
                            <>
                                <a
                                    href="https://github.com/dograh-hq/dograh/issues/new/choose"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 transition-colors"
                                >
                                    <HelpCircle className="w-4 h-4" />
                                    Get Help
                                </a>
                                <a
                                    href="https://github.com/dograh-hq/dograh"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 transition-colors"
                                >
                                    <Star className="w-4 h-4 fill-yellow-400 text-yellow-400" />
                                    Star us on GitHub
                                </a>
                            </>
                        )}
                    </div>
                </nav>
            </div>
        </header>
    );
}
