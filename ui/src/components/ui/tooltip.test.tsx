import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { Tooltip, TooltipTrigger, TooltipContent } from './tooltip';

describe('Tooltip', () => {
  it('renders tooltip trigger', () => {
    render(
      <Tooltip>
        <TooltipTrigger>Hover me</TooltipTrigger>
        <TooltipContent>Tooltip text</TooltipContent>
      </Tooltip>
    );
    expect(screen.getByText('Hover me')).toBeInTheDocument();
  });

  it('renders tooltip content when open', () => {
    render(
      <Tooltip open>
        <TooltipTrigger>Trigger</TooltipTrigger>
        <TooltipContent>Tooltip text</TooltipContent>
      </Tooltip>
    );
    expect(screen.getByRole('tooltip')).toHaveTextContent('Tooltip text');
  });
});
