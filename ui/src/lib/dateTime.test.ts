import { describe, expect, it } from 'vitest';

import { formatCalendarDate, formatDate, formatDateTime } from '@/lib/dateTime';

describe('dateTime', () => {
    it('formats an instant in the requested timezone', () => {
        expect(formatDateTime('2026-07-23T10:00:00Z', 'Asia/Kolkata')).toBe(
            'Jul 23, 2026, 03:30 PM',
        );
    });

    it('formats the date represented in the requested timezone', () => {
        expect(formatDate('2026-07-23T01:00:00Z', 'America/Los_Angeles')).toBe(
            'Jul 22, 2026',
        );
    });

    it('does not shift calendar-only dates across timezones', () => {
        expect(formatCalendarDate('2026-07-23')).toBe('Jul 23, 2026');
    });
});
