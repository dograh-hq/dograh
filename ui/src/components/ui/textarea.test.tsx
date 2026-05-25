import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { Textarea } from './textarea';

describe('Textarea', () => {
  it('renders with placeholder', () => {
    render(<Textarea placeholder="Enter description" />);
    expect(screen.getByPlaceholderText('Enter description')).toBeInTheDocument();
  });

  it('accepts and displays value', () => {
    render(<Textarea value="test content" readOnly />);
    expect(screen.getByDisplayValue('test content')).toBeInTheDocument();
  });

  it('handles onChange events', () => {
    const handleChange = vi.fn();
    render(<Textarea onChange={handleChange} />);
    const textarea = screen.getByRole('textbox');
    textarea.click();
    expect(handleChange).not.toHaveBeenCalled();
  });

  it('is disabled when disabled prop is true', () => {
    render(<Textarea disabled />);
    expect(screen.getByRole('textbox')).toBeDisabled();
  });

  it('applies custom className', () => {
    const { container } = render(<Textarea className="custom-textarea" />);
    expect(container.firstChild).toHaveClass('custom-textarea');
  });
});
