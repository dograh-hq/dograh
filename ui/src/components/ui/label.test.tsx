import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { Label } from './label';

describe('Label', () => {
  it('renders children text', () => {
    render(<Label>Email</Label>);
    expect(screen.getByText('Email')).toBeInTheDocument();
  });

  it('associates with input via htmlFor', () => {
    render(<Label htmlFor="email-input">Email</Label>);
    expect(screen.getByText('Email')).toHaveAttribute('for', 'email-input');
  });

  it('applies custom className', () => {
    const { container } = render(<Label className="custom-label">Text</Label>);
    expect(container.firstChild).toHaveClass('custom-label');
  });
});
