import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { RadioGroup, RadioGroupItem } from './radio-group';

describe('RadioGroup', () => {
  it('renders radio items', () => {
    render(
      <RadioGroup>
        <RadioGroupItem value="a" aria-label="Option A" />
        <RadioGroupItem value="b" aria-label="Option B" />
      </RadioGroup>
    );
    expect(screen.getAllByRole('radio')).toHaveLength(2);
  });

  it('checks item when clicked', async () => {
    const handleChange = vi.fn();
    render(
      <RadioGroup onValueChange={handleChange}>
        <RadioGroupItem value="a" aria-label="Option A" />
      </RadioGroup>
    );
    await userEvent.click(screen.getByRole('radio'));
    await waitFor(() => expect(handleChange).toHaveBeenCalledTimes(1));
  });

  it('applies custom className', () => {
    const { container } = render(
      <RadioGroup className="custom-group">
        <RadioGroupItem value="a" aria-label="A" />
      </RadioGroup>
    );
    expect(container.firstChild).toHaveClass('custom-group');
  });
});
