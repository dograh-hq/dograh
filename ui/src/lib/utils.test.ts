import { describe, it, expect, vi } from 'vitest';
import { cn, getRandomId, getNextNodeId, debounce } from './utils';

describe('cn', () => {
  it('merges class names with tailwind', () => {
    expect(cn('px-2', 'py-1')).toBe('px-2 py-1');
  });

  it('handles conditional classes', () => {
    expect(cn('base', false && 'hidden', true && 'visible')).toBe('base visible');
  });

  it('merges conflicting tailwind classes', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4');
  });

  it('handles empty inputs', () => {
    expect(cn()).toBe('');
  });

  it('handles undefined and null values', () => {
    expect(cn('base', undefined, null, 'extra')).toBe('base extra');
  });
});

describe('getRandomId', () => {
  it('returns a number between 0 and 9999', () => {
    const id = getRandomId();
    expect(id).toBeGreaterThanOrEqual(0);
    expect(id).toBeLessThan(10000);
  });

  it('returns different values on multiple calls', () => {
    const ids = new Set(Array.from({ length: 20 }, getRandomId));
    expect(ids.size).toBeGreaterThan(1);
  });
});

describe('getNextNodeId', () => {
  it('returns "1" for empty array', () => {
    expect(getNextNodeId([])).toBe('1');
  });

  it('returns next id after max existing', () => {
    expect(getNextNodeId([{ id: '1' }, { id: '3' }])).toBe('4');
  });

  it('ignores non-numeric ids', () => {
    expect(getNextNodeId([{ id: 'abc' }, { id: '5' }])).toBe('6');
  });
});

describe('debounce', () => {
  it('delays function execution', () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const debounced = debounce(fn, 100);

    debounced();
    expect(fn).not.toHaveBeenCalled();

    vi.advanceTimersByTime(100);
    expect(fn).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it('cancels previous call on rapid invocations', () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const debounced = debounce(fn, 100);

    debounced();
    debounced();
    debounced();
    expect(fn).not.toHaveBeenCalled();

    vi.advanceTimersByTime(100);
    expect(fn).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
