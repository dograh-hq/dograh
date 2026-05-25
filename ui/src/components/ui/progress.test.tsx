import { describe, it, expect } from 'vitest';
import { render } from '@/test/test-utils';
import { Progress } from './progress';

describe('Progress', () => {
  it('renders with default value', () => {
    const { container } = render(<Progress value={50} />);
    const indicator = container.querySelector('[data-slot="progress-indicator"]');
    expect(indicator).toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(<Progress value={30} className="custom-progress" />);
    expect(container.firstChild).toHaveClass('custom-progress');
  });

  it('renders with zero value', () => {
    const { container } = render(<Progress value={0} />);
    const indicator = container.querySelector('[data-slot="progress-indicator"]');
    expect(indicator).toHaveStyle('transform: translateX(-100%)');
  });

  it('renders with full value', () => {
    const { container } = render(<Progress value={100} />);
    const indicator = container.querySelector('[data-slot="progress-indicator"]');
    expect(indicator).toHaveStyle('transform: translateX(-0%)');
  });
});
