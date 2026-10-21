import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { QuestionCards } from './QuestionCards';

describe('builder question cards', () => {
    it('submits multiple selections and a custom answer against the pending interrupt', () => {
        const submit = vi.fn();
        render(<QuestionCards disabled={false} onSubmit={submit} pending={[{ id: 'pending-1', kind: 'questions', questions: [{ id: 'fields', title: 'What should it collect?', kind: 'multiple', options: ['Name', 'Email'], allow_custom: true }] }]} />);
        expect((screen.getByText('Continue') as HTMLButtonElement).disabled).toBe(true);
        fireEvent.click(screen.getByText('Name'));
        fireEvent.click(screen.getByText('Email'));
        fireEvent.change(screen.getByLabelText('Custom answer: What should it collect?'), { target: { value: 'Appointment time\nTreatment interest\nName' } });
        fireEvent.click(screen.getByText('Continue'));
        expect(submit).toHaveBeenCalledWith({ 'pending-1': { fields: ['Name', 'Email', 'Appointment time', 'Treatment interest'] } });
    });

    it('replaces a single choice when the user types a custom answer', () => {
        const submit = vi.fn();
        render(<QuestionCards disabled={false} onSubmit={submit} pending={[{ id: 'pending-2', kind: 'questions', questions: [{ id: 'structure', title: 'How should it work?', kind: 'single', options: ['One prompt', 'Separate stages'], allow_custom: true }] }]} />);
        fireEvent.click(screen.getByText('One prompt'));
        fireEvent.change(screen.getByLabelText('Custom answer: How should it work?'), { target: { value: 'Choose for me' } });
        fireEvent.click(screen.getByText('Continue'));
        expect(submit).toHaveBeenCalledWith({ 'pending-2': { structure: ['Choose for me'] } });
    });
});
