import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { Popover, PopoverTrigger, PopoverContent } from './popover';

describe('Popover', () => {
  it('renders trigger', () => {
    render(
      <Popover>
        <PopoverTrigger>Click me</PopoverTrigger>
        <PopoverContent>Popover content</PopoverContent>
      </Popover>
    );
    expect(screen.getByText('Click me')).toBeInTheDocument();
  });

  it('renders content when open', () => {
    render(
      <Popover open>
        <PopoverTrigger>Click</PopoverTrigger>
        <PopoverContent>Content</PopoverContent>
      </Popover>
    );
    expect(screen.getByText('Content')).toBeInTheDocument();
  });
});
