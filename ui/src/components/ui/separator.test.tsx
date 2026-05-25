import { describe, it, expect } from 'vitest';
import { render } from '@/test/test-utils';
import { Separator } from './separator';

describe('Separator', () => {
  it('renders horizontal separator by default', () => {
    const { container } = render(<Separator />);
    expect(container.firstChild).toHaveAttribute('data-orientation', 'horizontal');
  });

  it('renders vertical separator', () => {
    const { container } = render(<Separator orientation="vertical" />);
    expect(container.firstChild).toHaveAttribute('data-orientation', 'vertical');
  });

  it('has data-orientation attribute', () => {
    const { container } = render(<Separator />);
    expect(container.firstChild).toHaveAttribute('data-orientation', 'horizontal');
  });

  it('applies custom className', () => {
    const { container } = render(<Separator className="custom-sep" />);
    expect(container.firstChild).toHaveClass('custom-sep');
  });
});
