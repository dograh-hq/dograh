import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@/test/test-utils';
import { ChoiceChips } from './choice-chips';

describe('ChoiceChips', () => {
  const defaultProps = {
    options: [
      { value: 'option1', label: 'Option 1' },
      { value: 'option2', label: 'Option 2' },
      { value: 'option3', label: 'Option 3' },
    ],
    value: 'option1',
    onChange: vi.fn(),
  };

  it('renders all options', () => {
    render(<ChoiceChips {...defaultProps} />);
    expect(screen.getByText('Option 1')).toBeInTheDocument();
    expect(screen.getByText('Option 2')).toBeInTheDocument();
    expect(screen.getByText('Option 3')).toBeInTheDocument();
  });

  it('renders selected option with primary styles', () => {
    const { container } = render(<ChoiceChips {...defaultProps} />);
    const selectedButton = screen.getByText('Option 1');
    expect(selectedButton).toHaveClass('bg-primary');
    expect(selectedButton).toHaveClass('text-primary-foreground');
  });

  it('renders unselected options with secondary styles', () => {
    const { container } = render(<ChoiceChips {...defaultProps} />);
    const unselectedButton = screen.getByText('Option 2');
    expect(unselectedButton).toHaveClass('bg-secondary');
    expect(unselectedButton).toHaveClass('text-secondary-foreground');
  });

  it('calls onChange when clicking an option', () => {
    const onChange = vi.fn();
    render(<ChoiceChips {...defaultProps} onChange={onChange} />);
    
    fireEvent.click(screen.getByText('Option 2'));
    expect(onChange).toHaveBeenCalledWith('option2');
  });

  it('applies custom className', () => {
    const { container } = render(
      <ChoiceChips {...defaultProps} className="custom-class" />
    );
    expect(container.firstChild).toHaveClass('custom-class');
  });

  it('handles empty options array', () => {
    const { container } = render(
      <ChoiceChips options={[]} value="" onChange={vi.fn()} />
    );
    expect(container.firstChild).toBeInTheDocument();
  });

  it('handles single option', () => {
    render(
      <ChoiceChips 
        options={[{ value: 'only', label: 'Only Option' }]} 
        value="only" 
        onChange={vi.fn()} 
      />
    );
    expect(screen.getByText('Only Option')).toBeInTheDocument();
  });
});