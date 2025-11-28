import React, { ReactNode } from 'react'

interface WorkflowLayoutProps {
    children: ReactNode,
    headerActions?: ReactNode,
    backButton?: ReactNode,
    showFeaturesNav?: boolean,
}

const WorkflowLayout: React.FC<WorkflowLayoutProps> = ({ children }) => {
    // This component is kept for backward compatibility
    // AppLayout is now applied globally in the root layout
    return (
        <>
            {children}
        </>
    )
}

export default WorkflowLayout
