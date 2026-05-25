import { describe, it, expect } from 'vitest';
import { render } from '@/test/test-utils';
import { Calendar } from './calendar';

describe('Calendar', () => {
  it('renders with default props', () => {
    const { container } = render(<Calendar />);
    expect(container.firstChild).toBeInTheDocument();
  });

  it('accepts custom className', () => {
    const { container } = render(<Calendar className="custom-calendar" />);
    expect(container.firstChild).toHaveClass('custom-calendar');
  });

  it('accepts showOutsideDays prop', () => {
    const { container } = render(<Calendar showOutsideDays={false} />);
    expect(container.firstChild).toBeInTheDocument();
  });

  it('accepts captionLayout prop', () => {
    const { container } = render(<Calendar captionLayout="dropdown" />);
    expect(container.firstChild).toBeInTheDocument();
  });

  it('accepts buttonVariant prop', () => {
    const { container } = render(<Calendar buttonVariant="outline" />);
    expect(container.firstChild).toBeInTheDocument();
  });

  it('accepts fromDate and toDate constraints', () => {
    const fromDate = new Date(2024, 0, 1);
    const toDate = new Date(2024, 11, 31);
    const { container } = render(<Calendar fromDate={fromDate} toDate={toDate} />);
    expect(container.firstChild).toBeInTheDocument();
  });

  it('accepts classNames prop', () => {
    const { container } = render(
      <Calendar classNames={{ root: 'custom-root' }} />
    );
    expect(container.firstChild).toBeInTheDocument();
  });

  it('accepts formatters prop', () => {
    const { container } = render(
      <Calendar formatters={{ formatMonthDropdown: (d) => d.toLocaleString('default', { month: 'long' }) }} />
    );
    expect(container.firstChild).toBeInTheDocument();
  });

  it('accepts components prop', () => {
    const { container } = render(<Calendar components={{}} />);
    expect(container.firstChild).toBeInTheDocument();
  });
});