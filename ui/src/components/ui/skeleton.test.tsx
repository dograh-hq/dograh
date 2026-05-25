import { describe, it, expect } from 'vitest';
import { render } from '@/test/test-utils';
import { Skeleton } from './skeleton';

describe('Skeleton', () => {
  it('renders a div with animate-pulse', () => {
    const { container } = render(<Skeleton />);
    expect(container.firstChild).toHaveClass('animate-pulse');
  });

  it('applies custom className', () => {
    const { container } = render(<Skeleton className="custom-skeleton" />);
    expect(container.firstChild).toHaveClass('custom-skeleton');
  });

  it('renders children', () => {
    render(<Skeleton>Loading</Skeleton>);
    expect(document.querySelector('[data-slot="skeleton"]')).toBeInTheDocument();
  });
});
