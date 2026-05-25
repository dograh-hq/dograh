import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { Table, TableHeader, TableBody, TableFooter, TableRow, TableHead, TableCell, TableCaption } from './table';

describe('Table', () => {
  it('renders table with header, body, and rows', () => {
    render(
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Age</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>Alice</TableCell>
            <TableCell>30</TableCell>
          </TableRow>
        </TableBody>
      </Table>
    );
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.getByText('Alice')).toBeInTheDocument();
  });

  it('renders table caption', () => {
    render(
      <Table>
        <TableCaption>User list</TableCaption>
        <TableBody><TableRow><TableCell>Data</TableCell></TableRow></TableBody>
      </Table>
    );
    expect(screen.getByText('User list')).toBeInTheDocument();
  });

  it('renders table footer', () => {
    render(
      <Table>
        <TableFooter>
          <TableRow><TableCell>Total</TableCell></TableRow>
        </TableFooter>
      </Table>
    );
    expect(screen.getByText('Total')).toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(<Table className="custom-table" />);
    expect(container.querySelector('table')).toHaveClass('custom-table');
  });
});
