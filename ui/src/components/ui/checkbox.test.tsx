import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { Checkbox } from './checkbox';

describe('Checkbox', () => {
  it('renders unchecked by default', () => {
    render(<Checkbox aria-label="Accept terms" />);
    expect(screen.getByRole('checkbox')).not.toBeChecked();
  });

  it('renders checked when checked prop is true', () => {
    render(<Checkbox checked aria-label="Accept terms" />);
    expect(screen.getByRole('checkbox')).toBeChecked();
  });

  it('handles onCheckedChange events', async () => {
    const handleChange = vi.fn();
    render(<Checkbox onCheckedChange={handleChange} aria-label="Accept terms" />);
    await userEvent.click(screen.getByRole('checkbox'));
    await waitFor(() => expect(handleChange).toHaveBeenCalledTimes(1));
  });

  it('is disabled when disabled prop is true', () => {
    render(<Checkbox disabled aria-label="Accept terms" />);
    expect(screen.getByRole('checkbox')).toBeDisabled();
  });
});
