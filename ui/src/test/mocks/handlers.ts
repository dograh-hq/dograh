import { http, HttpResponse } from 'msw';

export const handlers = [
  // Auth
  http.get('/api/auth/session', () => {
    return HttpResponse.json({ user: { id: '1', email: 'test@example.com' } });
  }),

  // App config
  http.get('/api/config/auth', () => {
    return HttpResponse.json({ provider: 'local' });
  }),

  http.get('/api/config/version', () => {
    return HttpResponse.json({ version: '1.31.0' });
  }),

  // User config
  http.get('/api/v1/user/configurations/user', () => {
    return HttpResponse.json({
      llm: { provider: 'openai', model: 'gpt-4.1', base_url: 'https://api.openai.com/v1' },
      tts: { provider: 'openai', model: 'gpt-4o-mini-tts' },
      stt: { provider: 'deepgram', model: 'nova-2' },
      embeddings: { provider: 'openai', model: 'text-embedding-3-small' },
    });
  }),

  http.put('/api/v1/user/configurations/user', async () => {
    return HttpResponse.json({ success: true });
  }),

  // Default configurations
  http.get('/api/v1/user/configurations/defaults', () => {
    return HttpResponse.json({
      llm: {
        openai: {
          properties: {
            provider: { const: 'openai', type: 'string' },
            api_key: { type: 'string' },
            model: { type: 'string', default: 'gpt-4.1' },
            base_url: { type: 'string', default: 'https://api.openai.com/v1' },
          },
        },
      },
      tts: {},
      stt: {},
      embeddings: {},
      default_providers: { llm: 'openai', tts: 'openai', stt: 'deepgram', embeddings: 'openai' },
    });
  }),

  // Workflows
  http.get('/api/v1/workflows', () => {
    return HttpResponse.json({ items: [], total: 0 });
  }),

  http.get('/api/v1/workflow/count', () => {
    return HttpResponse.json({ count: 0 });
  }),
];
