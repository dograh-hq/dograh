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
                <div className="sticky top-0 z-50 bg-white border-b">
                    <div className="flex justify-center relative">
                        {stickyTabs}
                    </div>
                </div>
            )}
            {children}
        </>
    )
}

export default WorkflowLayout
