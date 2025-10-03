import BaseHeader from "@/components/header/BaseHeader"

export default function ConfigureTelephonyLayout({
    children,
}: {
    children: React.ReactNode
}) {
    return (
        <>
            <BaseHeader />
            {children}
        </>
    )
}
