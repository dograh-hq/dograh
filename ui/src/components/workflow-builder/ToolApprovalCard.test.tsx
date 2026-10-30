import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ToolApprovalCard } from './ToolApprovalCard';

const createTool = { name: 'Create ticket', category: 'http_api', definition: { type: 'http_api' } };

describe('tool approval', () => {
    it('shows the summary and category, and approves a new tool explicitly', () => {
        const submit = vi.fn();
        render(<ToolApprovalCard interruptId="tool-1" operation="create" tool={createTool} plainLanguage="Creates a maintenance ticket after the call." disabled={false} onSubmit={submit} />);
        expect(screen.getByText('New tool')).toBeTruthy();
        expect(screen.getByText('API call')).toBeTruthy();
        expect(screen.getByText('Creates a maintenance ticket after the call.')).toBeTruthy();
        expect(submit).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Approve & create' }));
        expect(submit).toHaveBeenCalledWith({ interrupt_id: 'tool-1', decision: 'approve', feedback: '' });
    });

    it('warns that editing a shared tool affects every workflow and sends deny feedback', () => {
        const submit = vi.fn();
        render(<ToolApprovalCard interruptId="tool-2" operation="update" tool={{ description: 'Clearer wording' }} toolUuid="tool-xyz" usedByCount={3} disabled={false} onSubmit={submit} />);
        expect(screen.getByText(/every workflow that uses it \(3 workflows\)/)).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
        fireEvent.change(screen.getByLabelText('What should change? (optional)'), { target: { value: '  Make a separate tool instead  ' } });
        expect(screen.queryByRole('button', { name: 'Approve edit' })).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Send feedback' }));
        expect(submit).toHaveBeenCalledWith({ interrupt_id: 'tool-2', decision: 'deny', feedback: 'Make a separate tool instead' });
    });

    it('disables decisions while a turn is running', () => {
        const submit = vi.fn();
        render(<ToolApprovalCard interruptId="tool-1" operation="create" tool={createTool} disabled onSubmit={submit} />);
        fireEvent.click(screen.getByRole('button', { name: 'Approve & create' }));
        expect(submit).not.toHaveBeenCalled();
        expect((screen.getByRole('button', { name: 'Deny' }) as HTMLButtonElement).disabled).toBe(true);
    });
});
