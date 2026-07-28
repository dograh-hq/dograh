export function getLocalTimezone() {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

type DateInput = Date | number | string;

export function formatDateTime(value: DateInput, timezone?: string | null) {
    return new Date(value).toLocaleString('en-US', {
        timeZone: timezone || getLocalTimezone(),
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true,
    });
}

export function formatDate(value: DateInput, timezone?: string | null) {
    return new Date(value).toLocaleDateString('en-US', {
        timeZone: timezone || getLocalTimezone(),
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    });
}

export function formatCalendarDate(value: string) {
    const calendarDate = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!calendarDate) {
        return formatDate(value, 'UTC');
    }

    const [, year, month, day] = calendarDate;
    return formatDate(
        Date.UTC(Number(year), Number(month) - 1, Number(day)),
        'UTC',
    );
}

export function formatLocalDateTime(value: Date) {
    return `${value.toLocaleDateString()} ${value.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
    })}`;
}
