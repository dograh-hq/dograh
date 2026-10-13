import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { DocumentResponseSchema } from '@/client/types.gen';

import DocumentList from './DocumentList';

const mocks = vi.hoisted(() => ({
  query: '',
  list: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(mocks.query),
}));
vi.mock('@/client/sdk.gen', () => ({
  listDocumentsApiV1KnowledgeBaseDocumentsGet: mocks.list,
  deleteDocumentApiV1KnowledgeBaseDocumentsDocumentUuidDelete: vi.fn(),
  getDocumentContentApiV1KnowledgeBaseDocumentsDocumentUuidContentGet: vi.fn(),
  saveDocumentContentApiV1KnowledgeBaseDocumentsDocumentUuidContentPut: vi.fn(),
}));
vi.mock('@/hooks/useOrganizationTimezone', () => ({ useOrganizationTimezone: () => 'UTC' }));
vi.mock('@/lib/logger', () => ({ default: { error: vi.fn(), info: vi.fn() } }));
vi.mock('./DocumentEditor', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./DocumentEditor')>();
  return {
    ...actual,
    default: ({ doc }: { doc: DocumentResponseSchema | null }) =>
      doc ? <div data-testid="editor">Editing {doc.filename}</div> : null,
  };
});

const doc = (overrides: Partial<DocumentResponseSchema>): DocumentResponseSchema => ({
  id: 1,
  document_uuid: 'doc-uuid',
  filename: 'faq.md',
  file_size_bytes: 2048,
  file_hash: 'hash',
  mime_type: 'text/markdown',
  processing_status: 'completed',
  total_chunks: 3,
  custom_metadata: {},
  docling_metadata: {},
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  organization_id: 1,
  created_by: 1,
  is_active: true,
  ...overrides,
});

const markdown = doc({ id: 1, document_uuid: 'doc-md', filename: 'faq.md' });
const pdf = doc({ id: 2, document_uuid: 'doc-pdf', filename: 'pricing.pdf', mime_type: 'application/pdf' });

const scrollIntoView = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  mocks.query = '';
  Element.prototype.scrollIntoView = scrollIntoView;
  mocks.list.mockResolvedValue({ data: { documents: [markdown, pdf] } });
});

describe('DocumentList ?document= deep link', () => {
  it('opens the editor for an editable document and scrolls to its row', async () => {
    mocks.query = 'document=doc-md';
    render(<DocumentList refreshTrigger={0} />);

    expect((await screen.findByTestId('editor')).textContent).toBe('Editing faq.md');
    expect(scrollIntoView).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Edit faq.md' }).getAttribute('aria-current')).toBe('true');
  });

  it('only highlights a document that cannot be opened in the editor', async () => {
    mocks.query = 'document=doc-pdf';
    render(<DocumentList refreshTrigger={0} />);

    await screen.findByText('pricing.pdf');
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledOnce());
    expect(screen.queryByTestId('editor')).toBeNull();
    expect(screen.getByText('pricing.pdf').closest('[aria-current="true"]')).not.toBeNull();
  });

  it('does nothing without the query parameter', async () => {
    render(<DocumentList refreshTrigger={0} />);

    await screen.findByText('faq.md');
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(screen.queryByTestId('editor')).toBeNull();
    expect(document.querySelector('[aria-current="true"]')).toBeNull();
  });
});
