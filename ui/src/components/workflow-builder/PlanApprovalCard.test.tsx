import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { CallBrief } from '@/client/types.gen';

import { PlanApprovalCard } from './PlanApprovalCard';

const brief: CallBrief = {
    goal: 'Book appointments', audience: 'Customers', direction: 'inbound', trigger: 'An incoming call',
    conversation_steps: ['Greet the caller', 'Find a time', 'Confirm the booking'],
    data_operations: [{ purpose: 'Find availability', system: 'Calendar', timing: 'Before booking', inputs: ['Preferred date'], expected_result: 'Available times', failure_behavior: 'Offer a callback' }],
    assumptions: ['Calendar is connected'], success_criteria: ['An appointment is booked'], exit_conditions: ['Caller says goodbye'],
    acceptance_scenarios: ['No times available'],
};

describe('plan approval', () => {
    it('shows the flow, integrations and assumptions before submitting explicit approval', () => {
        const submit = vi.fn();
        render(<PlanApprovalCard interruptId="plan-1" revision={2} brief={brief} disabled={false} onSubmit={submit} />);
        expect(screen.getByText('Inbound')).toBeTruthy();
        const step = screen.getByRole('button', { name: 'Step 2: Find a time' });
        fireEvent.click(step);
        expect(step.getAttribute('aria-expanded')).toBe('true');
        expect(screen.getByText('Step 2.')).toBeTruthy();
        expect(screen.getByText('If it fails: Offer a callback')).toBeTruthy();
        expect(screen.getByText('Calendar is connected')).toBeTruthy();
        expect(submit).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Approve & build' }));
        expect(submit).toHaveBeenCalledWith({ interrupt_id: 'plan-1', brief_revision: 2, decision: 'approve', feedback: '' });
    });

    it('opens feedback on request and prevents approving with unsubmitted changes', () => {
        const submit = vi.fn();
        render(<PlanApprovalCard interruptId="plan-2" revision={3} brief={brief} disabled={false} onSubmit={submit} />);
        expect(screen.queryByLabelText('What would you like to change?')).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Request changes' }));
        const revise = screen.getByRole('button', { name: 'Submit changes' }) as HTMLButtonElement;
        expect(revise.disabled).toBe(true);
        fireEvent.change(screen.getByLabelText('What would you like to change?'), { target: { value: '  Collect a callback request instead  ' } });
        expect(screen.queryByRole('button', { name: 'Approve & build' })).toBeNull();
        fireEvent.click(revise);
        expect(submit).toHaveBeenCalledWith({ interrupt_id: 'plan-2', brief_revision: 3, decision: 'revise', feedback: 'Collect a callback request instead' });
    });

    it('disables decisions while a turn is running', () => {
        const submit = vi.fn();
        render(<PlanApprovalCard interruptId="plan-1" revision={1} brief={brief} disabled onSubmit={submit} />);
        fireEvent.click(screen.getByRole('button', { name: 'Approve & build' }));
        expect(submit).not.toHaveBeenCalled();
        expect((screen.getByRole('button', { name: 'Request changes' }) as HTMLButtonElement).disabled).toBe(true);
    });
});
