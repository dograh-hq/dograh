'use client';

import { useOrgConfig } from '@/context/OrgConfigContext';
import { getLocalTimezone } from '@/lib/dateTime';

export function useOrganizationTimezone() {
    const { organizationPreferences } = useOrgConfig();
    return organizationPreferences?.timezone || getLocalTimezone();
}
