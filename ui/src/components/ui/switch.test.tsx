import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { Switch } from './switch';

describe('Switch', () => {
  it('renders unchecked by default', () => {
    render(<Switch aria-label="Toggle" />);
    expect(screen.getByRole('switch')).not.toBeChecked();
  });

  it('renders checked when checked prop is true', () => {
    render(<Switch checked aria-label="Toggle" />);
    expect(screen.getByRole('switch')).toBeChecked();
  });

  it('handles onCheckedChange events', async () => {
    const handleChange = vi.fn();
    render(<Switch onCheckedChange={handleChange} aria-label="Toggle" />);
    await userEvent.click(screen.getByRole('switch'));
    await waitFor(() => expect(handleChange).toHaveBeenCalledTimes(1));
  });

  it('is disabled when disabled prop is true', () => {
    render(<Switch disabled aria-label="Toggle" />);
    expect(screen.getByRole('switch')).toBeDisabled();
  });
});
