import React, { ReactNode } from 'react'

import AppLayout from '@/components/layout/AppLayout'

interface LoopTalkLayoutProps {
    children: ReactNode,
    headerActions?: ReactNode,
    backButton?: ReactNode,
}

const LoopTalkLayout: React.FC<LoopTalkLayoutProps> = ({ children, headerActions }) => {
    // backButton is kept in interface for backward compatibility
    // but not used with the new sidebar layout
    return (
        <AppLayout headerActions={headerActions}>
            {children}
        </AppLayout>
    )
}

export default LoopTalkLayout
