import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@/test/test-utils';
import { JsonEditor, validateJson } from './json-editor';

describe('JsonEditor', () => {
  const defaultProps = {
    value: '{"key": "value"}',
    onChange: vi.fn(),
  };

  it('renders with value', () => {
    render(<JsonEditor {...defaultProps} />);
    expect(screen.getByDisplayValue('{"key": "value"}')).toBeInTheDocument();
  });

  it('renders with label', () => {
    render(<JsonEditor {...defaultProps} label="JSON Config" />);
    expect(screen.getByText('JSON Config')).toBeInTheDocument();
  });

  it('renders with description', () => {
    render(<JsonEditor {...defaultProps} description="Enter JSON configuration" />);
    expect(screen.getByText('Enter JSON configuration')).toBeInTheDocument();
  });

  it('calls onChange when value changes', () => {
    const onChange = vi.fn();
    render(<JsonEditor {...defaultProps} onChange={onChange} />);
    
    const textarea = screen.getByDisplayValue('{"key": "value"}');
    fireEvent.change(textarea, { target: { value: '{"new": "value"}' } });
    
    expect(onChange).toHaveBeenCalledWith('{"new": "value"}');
  });

  it('shows error message for invalid JSON', () => {
    render(<JsonEditor {...defaultProps} value="invalid json" error="Invalid JSON" />);
    expect(screen.getByText('Invalid JSON')).toBeInTheDocument();
  });

  it('renders copy button when showCopyButton is true', () => {
    render(<JsonEditor {...defaultProps} showCopyButton />);
    expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(
      <JsonEditor {...defaultProps} className="custom-class" />
    );
    expect(container.firstChild).toHaveClass('custom-class');
  });

  it('applies custom minHeight', () => {
    render(<JsonEditor {...defaultProps} minHeight="200px" />);
    const textarea = screen.getByDisplayValue('{"key": "value"}');
    expect(textarea).toHaveStyle({ minHeight: '200px' });
  });
});

describe('validateJson', () => {
  it('returns valid for empty string', () => {
    const result = validateJson('');
    expect(result.valid).toBe(true);
  });

  it('returns valid for empty object', () => {
    const result = validateJson('{}');
    expect(result.valid).toBe(true);
    expect(result.parsed).toEqual({});
  });

  it('returns valid for empty array', () => {
    const result = validateJson('[]');
    expect(result.valid).toBe(true);
    expect(result.parsed).toEqual([]);
  });

  it('returns valid for valid JSON object', () => {
    const result = validateJson('{"key": "value"}');
    expect(result.valid).toBe(true);
    expect(result.parsed).toEqual({ key: 'value' });
  });

  it('returns valid for valid JSON array', () => {
    const result = validateJson('["a", "b", "c"]');
    expect(result.valid).toBe(true);
    expect(result.parsed).toEqual(['a', 'b', 'c']);
  });

  it('returns invalid for invalid JSON', () => {
    const result = validateJson('{invalid}');
    expect(result.valid).toBe(false);
    expect(result.error).toBeDefined();
  });

  it('detects unquoted template variables', () => {
    const result = validateJson('{"key": {{variable}}}');
    expect(result.valid).toBe(false);
    expect(result.error).toContain('Template variables must be quoted');
  });

  it('detects trailing comma', () => {
    const result = validateJson('{"key": "value",}');
    expect(result.valid).toBe(false);
    expect(result.error).toContain('Trailing comma');
  });

  it('detects single quotes', () => {
    const result = validateJson("{'key': 'value'}");
    expect(result.valid).toBe(false);
    expect(result.error).toContain('double quotes');
  });

  it('detects unquoted string values', () => {
    const result = validateJson('{key: value}');
    expect(result.valid).toBe(false);
    expect(result.error).toContain('quoted');
  });

  it('handles numbers', () => {
    const result = validateJson('{"count": 42}');
    expect(result.valid).toBe(true);
    expect(result.parsed).toEqual({ count: 42 });
  });

  it('handles booleans', () => {
    const result = validateJson('{"enabled": true}');
    expect(result.valid).toBe(true);
    expect(result.parsed).toEqual({ enabled: true });
  });

  it('handles null', () => {
    const result = validateJson('{"value": null}');
    expect(result.valid).toBe(true);
    expect(result.parsed).toEqual({ value: null });
  });

  it('handles nested objects', () => {
    const result = validateJson('{"outer": {"inner": "value"}}');
    expect(result.valid).toBe(true);
    expect(result.parsed).toEqual({ outer: { inner: 'value' } });
  });
});