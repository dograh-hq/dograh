import React, { ReactNode } from 'react'

import BaseHeader from '@/components/header/BaseHeader'

interface WorkflowLayoutProps {
    children: ReactNode,
    headerActions?: ReactNode,
    backButton?: ReactNode,
    showFeaturesNav?: boolean,
    stickyTabs?: ReactNode
}

const WorkflowLayout: React.FC<WorkflowLayoutProps> = ({ children, headerActions, backButton, showFeaturesNav = true, stickyTabs }) => {
    return (
        <>
            <BaseHeader headerActions={headerActions} backButton={backButton} showFeaturesNav={showFeaturesNav} />
            {stickyTabs && (
                <div className="sticky top-[73px] z-40 bg-[#2a2e39] border-b border-gray-700">
                    <div className="container mx-auto px-4">
                        <div className="flex items-center justify-center py-2">
                            {stickyTabs}
                        </div>
                    </div>
                </div>
            )}
            {children}
        </>
    )
}

export default WorkflowLayout
