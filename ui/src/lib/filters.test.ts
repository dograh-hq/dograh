import { describe, it, expect } from 'vitest';
import { 
  getDefaultValue, 
  validateFilter,
  encodeFiltersToURL,
  decodeFiltersFromURL,
  formatDateRange,
  formatNumberRange,
  getDatePresetValue
} from './filters';
import { 
  ActiveFilter, 
  DateRangeValue, 
  MultiSelectValue, 
  NumberRangeValue, 
  FilterAttribute 
} from '@/types/filters';

describe('filters', () => {
  describe('getDefaultValue', () => {
    it('returns { from: null, to: null } for dateRange', () => {
      expect(getDefaultValue('dateRange')).toEqual({ from: null, to: null });
    });

    it('returns { codes: [] } for multiSelect', () => {
      expect(getDefaultValue('multiSelect')).toEqual({ codes: [] });
    });

    it('returns { value: null } for number', () => {
      expect(getDefaultValue('number')).toEqual({ value: null });
    });

    it('returns { min: null, max: null } for numberRange', () => {
      expect(getDefaultValue('numberRange')).toEqual({ min: null, max: null });
    });

    it('returns { status: "all" } for radio', () => {
      expect(getDefaultValue('radio')).toEqual({ status: 'all' });
    });

    it('returns { codes: [] } for tags', () => {
      expect(getDefaultValue('tags')).toEqual({ codes: [] });
    });

    it('returns { value: "" } for text', () => {
      expect(getDefaultValue('text')).toEqual({ value: '' });
    });

    it('throws error for unknown type', () => {
      expect(() => getDefaultValue('unknown' as any)).toThrow('Unknown filter type: unknown');
    });
  });

  describe('validateFilter', () => {
    const createFilter = (type: FilterAttribute['type'], value: any, config: any = {}): ActiveFilter => ({
      attribute: {
        id: 'test',
        type,
        label: 'Test',
        config,
      },
      value,
      isValid: true,
    });

    describe('dateRange', () => {
      it('returns error when from is missing', () => {
        const filter = createFilter('dateRange', { from: null, to: new Date() });
        expect(validateFilter(filter)).toBe('Both dates are required');
      });

      it('returns error when to is missing', () => {
        const filter = createFilter('dateRange', { from: new Date(), to: null });
        expect(validateFilter(filter)).toBe('Both dates are required');
      });

      it('returns error when to is before from', () => {
        const from = new Date('2024-01-15');
        const to = new Date('2024-01-10');
        const filter = createFilter('dateRange', { from, to });
        expect(validateFilter(filter)).toBe('End date must be after start date');
      });

      it('returns error when range exceeds maxRangeDays', () => {
        const from = new Date('2024-01-01');
        const to = new Date('2024-02-01');
        const filter = createFilter('dateRange', { from, to }, { maxRangeDays: 30 });
        expect(validateFilter(filter)).toBe('Date range cannot exceed 30 days');
      });

      it('returns null for valid date range', () => {
        const from = new Date('2024-01-01');
        const to = new Date('2024-01-15');
        const filter = createFilter('dateRange', { from, to });
        expect(validateFilter(filter)).toBeNull();
      });
    });

    describe('multiSelect', () => {
      it('returns error when no codes selected', () => {
        const filter = createFilter('multiSelect', { codes: [] });
        expect(validateFilter(filter)).toBe('At least one option must be selected');
      });

      it('returns error when exceeds maxSelections', () => {
        const filter = createFilter('multiSelect', { codes: ['a', 'b', 'c'] }, { maxSelections: 2 });
        expect(validateFilter(filter)).toBe('Cannot select more than 2 options');
      });

      it('returns null for valid multiSelect', () => {
        const filter = createFilter('multiSelect', { codes: ['a', 'b'] });
        expect(validateFilter(filter)).toBeNull();
      });
    });

    describe('numberRange', () => {
      it('returns error when min is greater than max', () => {
        const filter = createFilter('numberRange', { min: 100, max: 50 });
        expect(validateFilter(filter)).toBe('Minimum must be less than maximum');
      });

      it('returns null for valid numberRange', () => {
        const filter = createFilter('numberRange', { min: 10, max: 100 });
        expect(validateFilter(filter)).toBeNull();
      });

      it('returns error when min and max are null', () => {
        const filter = createFilter('numberRange', { min: null, max: null });
        expect(validateFilter(filter)).toBe('Both values are required');
      });
    });

    describe('number', () => {
      it('returns error when value is below min', () => {
        const filter = createFilter('number', { value: 5 }, { min: 10, max: 100 });
        expect(validateFilter(filter)).toBe('Value cannot be less than 10');
      });

      it('returns error when value is above max', () => {
        const filter = createFilter('number', { value: 150 }, { min: 10, max: 100 });
        expect(validateFilter(filter)).toBe('Value cannot be greater than 100');
      });

      it('returns null for valid number', () => {
        const filter = createFilter('number', { value: 50 }, { min: 10, max: 100 });
        expect(validateFilter(filter)).toBeNull();
      });

      it('returns error when value is null', () => {
        const filter = createFilter('number', { value: null });
        expect(validateFilter(filter)).toBe('A value is required');
      });
    });

    describe('radio', () => {
      it('returns null for valid radio', () => {
        const filter = createFilter('radio', { status: 'completed' });
        expect(validateFilter(filter)).toBeNull();
      });
    });

    describe('text', () => {
      it('returns error when text is empty', () => {
        const filter = createFilter('text', { value: '' });
        expect(validateFilter(filter)).toBe('Text value is required');
      });

      it('returns error when text is whitespace only', () => {
        const filter = createFilter('text', { value: '   ' });
        expect(validateFilter(filter)).toBe('Text value is required');
      });

      it('returns null for valid text', () => {
        const filter = createFilter('text', { value: 'hello' });
        expect(validateFilter(filter)).toBeNull();
      });
    });
  });

  describe('encodeFiltersToURL', () => {
    it('returns empty string for empty filters', () => {
      expect(encodeFiltersToURL([])).toBe('');
    });

    it('encodes filters to URL params', () => {
      const filters: ActiveFilter[] = [
        {
          attribute: { id: 'date', type: 'dateRange', label: 'Date', config: {} },
          value: { from: new Date('2024-01-01'), to: new Date('2024-01-31') },
          isValid: true,
        },
      ];
      const result = encodeFiltersToURL(filters);
      expect(result).toContain('filters=');
    });

    it('encodes multiple filters', () => {
      const filters: ActiveFilter[] = [
        {
          attribute: { id: 'status', type: 'multiSelect', label: 'Status', config: { options: [] } },
          value: { codes: ['completed', 'failed'] },
          isValid: true,
        },
        {
          attribute: { id: 'text', type: 'text', label: 'Text', config: {} },
          value: { value: 'hello' },
          isValid: true,
        },
      ];
      const result = encodeFiltersToURL(filters);
      expect(result).toContain('filters=');
      // Should contain encoded JSON
      expect(decodeURIComponent(result)).toContain('status');
      expect(decodeURIComponent(result)).toContain('hello');
    });
  });

  describe('decodeFiltersFromURL', () => {
    it('returns empty array for empty params', () => {
      const params = new URLSearchParams();
      const availableAttributes: FilterAttribute[] = [];
      expect(decodeFiltersFromURL(params, availableAttributes)).toEqual([]);
    });

    it('decodes filters from URL', () => {
      const params = new URLSearchParams();
      params.set('filters', JSON.stringify([{ id: 'test', value: { value: 'hello' } }]));
      const availableAttributes: FilterAttribute[] = [
        { id: 'test', type: 'text', label: 'Test', config: {} }
      ];
      const result = decodeFiltersFromURL(params, availableAttributes);
      expect(result.length).toBe(1);
      expect(result[0].attribute.id).toBe('test');
    });
  });

  describe('formatDateRange', () => {
    it('returns "No date range selected" for null values', () => {
      expect(formatDateRange({ from: null, to: null })).toBe('No date range selected');
    });

    it('formats date range correctly', () => {
      const value: DateRangeValue = { 
        from: new Date('2024-01-15'), 
        to: new Date('2024-01-20') 
      };
      const result = formatDateRange(value);
      expect(result).toContain('2024');
    });
  });

  describe('formatNumberRange', () => {
    it('returns "No range selected" for null values', () => {
      expect(formatNumberRange({ min: null, max: null })).toBe('No range selected');
    });

    it('formats number range with unit', () => {
      const value: NumberRangeValue = { min: 10, max: 100 };
      const result = formatNumberRange(value, 'seconds');
      expect(result).toContain('10');
      expect(result).toContain('100');
      expect(result).toContain('seconds');
    });
  });

  describe('getDatePresetValue', () => {
    it('returns valid date range for "today"', () => {
      const result = getDatePresetValue('today');
      expect(result.from).toBeInstanceOf(Date);
      expect(result.to).toBeInstanceOf(Date);
    });

    it('returns valid date range for "yesterday"', () => {
      const result = getDatePresetValue('yesterday');
      expect(result.from).toBeInstanceOf(Date);
      expect(result.to).toBeInstanceOf(Date);
    });

    it('returns valid date range for "last7days"', () => {
      const result = getDatePresetValue('last7days');
      expect(result.from).toBeInstanceOf(Date);
      expect(result.to).toBeInstanceOf(Date);
    });

    it('returns valid date range for "last30days"', () => {
      const result = getDatePresetValue('last30days');
      expect(result.from).toBeInstanceOf(Date);
      expect(result.to).toBeInstanceOf(Date);
    });
  });
});