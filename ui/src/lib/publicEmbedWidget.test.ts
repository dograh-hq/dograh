import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const widgetSource = readFileSync(
    resolve(process.cwd(), 'public/embed/dograh-widget.js'),
    'utf8',
);

type WidgetWindow = Window & {
    DograhWidget?: {
        init: () => Promise<void>;
        start: () => Promise<void>;
        startChat: () => Promise<void>;
        endChat: () => Promise<unknown[] | null>;
        sendMessage: (text: string) => Promise<unknown[] | null>;
        onMessage: (callback: (text: string) => void) => void;
        getState: () => { chat: { status: string } };
    };
};

// The embed config endpoint resolves every visitor-facing label server-side, so
// the widget never carries defaults of its own. Mirror that here.
const WIDGET_TEXTS = {
    endChatText: 'End chat',
    endChatConfirmText: 'End this chat?',
    endChatCancelText: 'Cancel',
    endingChatText: 'Ending…',
    conversationEndedText: 'Conversation ended.',
    startNewChatText: 'Start new chat',
    chatRetryText: 'Retry',
    chatInputPlaceholder: 'Type a message…',
    sendMessageLabel: 'Send message',
    closeChatLabel: 'Close chat',
};

async function flushMicrotasks() {
    for (let i = 0; i < 5; i += 1) {
        await Promise.resolve();
    }
}

function encodeEvent(event: unknown) {
    return new TextEncoder().encode(`data: ${JSON.stringify(event)}\r\n\r\n`);
}

const initialSession = { revision: 2, state: 'running', is_completed: false, turns: [] };

function initStreamResponse() {
    return new Response(new ReadableStream({
        start(controller) {
            controller.enqueue(encodeEvent({ type: 'session', session_token: 'emb_session_TEST', workflow_run_id: 101, session: initialSession }));
            controller.enqueue(encodeEvent({ type: 'complete', session: initialSession }));
            controller.close();
        },
    }));
}

function createFetchMock(autoStart: boolean) {
    return vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/api/v1/public/embed/config/')) {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    workflow_id: 7,
                    settings: {
                        widgetType: 'chat',
                        embedMode: 'inline',
                        containerId: 'dograh-inline-container',
                    },
                    texts: WIDGET_TEXTS,
                    auto_start: autoStart,
                }),
            } as Response;
        }

        if (url.includes('/api/v1/public/embed/chat/') && url.endsWith('/end')) {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    revision: 3,
                    state: 'completed',
                    is_completed: true,
                    turns: [],
                }),
            } as Response;
        }

        return initStreamResponse();
    });
}

function countInitCalls(fetchMock: ReturnType<typeof createFetchMock>) {
    return fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith('/api/v1/public/embed/init/stream'),
    ).length;
}

async function loadWidget(fetchMock: ReturnType<typeof createFetchMock>) {
    vi.stubGlobal('fetch', fetchMock);
    window.eval(widgetSource);
    await flushMicrotasks();

    const widget = (window as WidgetWindow).DograhWidget;
    expect(widget).toBeDefined();
    if (fetchMock.mock.calls.length === 0) {
        await widget?.init();
    }
    await flushMicrotasks();
    return widget as NonNullable<WidgetWindow['DograhWidget']>;
}

describe('public embed widget chat lifecycle', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        document.head.innerHTML = '';
        document.body.innerHTML = `
            <script src="http://widget.test/embed/dograh-widget.js?token=emb_TEST"></script>
            <div id="dograh-inline-container"></div>
        `;
    });

    afterEach(() => {
        delete (window as WidgetWindow).DograhWidget;
        vi.useRealTimers();
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
        document.head.innerHTML = '';
        document.body.innerHTML = '';
    });

    it('auto-start replaces the inline CTA with the started conversation', async () => {
        const fetchMock = createFetchMock(true);
        await loadWidget(fetchMock);
        await vi.advanceTimersByTimeAsync(1000);
        await flushMicrotasks();

        expect(countInitCalls(fetchMock)).toBe(1);
        expect(document.querySelector('.dograh-chat-inline-cta')).toBeNull();
        expect(document.querySelector('.dograh-chat-panel--inline')).not.toBeNull();
    });

    it('public startChat opens the inline panel and reuses its session', async () => {
        const fetchMock = createFetchMock(false);
        const widget = await loadWidget(fetchMock);

        expect(document.querySelector('.dograh-chat-inline-cta')).not.toBeNull();
        expect(countInitCalls(fetchMock)).toBe(0);

        await widget.startChat();
        await flushMicrotasks();

        expect(countInitCalls(fetchMock)).toBe(1);
        expect(document.querySelector('.dograh-chat-inline-cta')).toBeNull();
        expect(document.querySelector('.dograh-chat-panel--inline')).not.toBeNull();

        await widget.startChat();
        await flushMicrotasks();
        expect(countInitCalls(fetchMock)).toBe(1);
    });

    it('shows an end-chat action that completes the server session', async () => {
        const fetchMock = createFetchMock(false);
        const widget = await loadWidget(fetchMock);

        await widget.startChat();
        await flushMicrotasks();

        const endButton = document.querySelector<HTMLButtonElement>('.dograh-chat-end');
        expect(endButton).not.toBeNull();
        expect(endButton?.disabled).toBe(false);

        endButton?.click();
        await flushMicrotasks();

        expect(fetchMock.mock.calls.some(([url]) =>
            String(url).endsWith('/api/v1/public/embed/chat/emb_session_TEST/end'),
        )).toBe(false);

        expect(document.querySelector('.dograh-chat-end-confirmation')?.textContent)
            .toContain(WIDGET_TEXTS.endChatConfirmText);
        expect(document.querySelector('.dograh-chat-end-confirm-cancel')?.textContent)
            .toBe(WIDGET_TEXTS.endChatCancelText);

        const confirmEndButton = document.querySelector<HTMLButtonElement>(
            '.dograh-chat-end-confirm-submit',
        );
        expect(confirmEndButton).not.toBeNull();
        confirmEndButton?.click();
        await flushMicrotasks();

        const endCalls = fetchMock.mock.calls.filter(([url]) =>
            String(url).endsWith('/api/v1/public/embed/chat/emb_session_TEST/end'),
        );
        expect(endCalls).toHaveLength(1);
        expect(widget.getState().chat.status).toBe('ended');
        expect(document.querySelector('.dograh-chat-banner')?.textContent).toContain('Conversation ended.');
        expect(document.querySelector<HTMLButtonElement>('.dograh-chat-send')?.disabled).toBe(true);
    });

    it('generic start waits for chat configuration before choosing a flow', async () => {
        let resolveConfig: (response: Response) => void = () => undefined;
        const configResponse = new Promise<Response>((resolve) => {
            resolveConfig = resolve;
        });
        const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
            const url = String(input);
            if (url.includes('/api/v1/public/embed/config/')) {
                return configResponse;
            }
            if (url.endsWith('/api/v1/public/embed/init/stream')) {
                return initStreamResponse();
            }
            if (url.includes('/turn-credentials/')) {
                return { ok: false, status: 503 } as Response;
            }
            throw new Error(`Unexpected request: ${url}`);
        });
        const getUserMedia = vi.fn().mockRejectedValue(
            Object.assign(new Error('permission denied'), { name: 'NotAllowedError' }),
        );
        vi.stubGlobal('fetch', fetchMock);
        vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });

        window.eval(widgetSource);
        await flushMicrotasks();
        const widget = (window as WidgetWindow).DograhWidget;
        expect(widget).toBeDefined();

        const startPromise = widget?.start();
        await flushMicrotasks();

        expect(countInitCalls(fetchMock)).toBe(0);
        expect(getUserMedia).not.toHaveBeenCalled();

        resolveConfig({
            ok: true,
            status: 200,
            json: async () => ({
                workflow_id: 7,
                settings: {
                    widgetType: 'chat',
                    embedMode: 'inline',
                    containerId: 'dograh-inline-container',
                },
                auto_start: false,
            }),
        } as Response);
        await startPromise;
        await flushMicrotasks();

        const configCalls = fetchMock.mock.calls.filter(([url]) =>
            String(url).includes('/api/v1/public/embed/config/'),
        );
        expect(configCalls).toHaveLength(1);
        expect(countInitCalls(fetchMock)).toBe(1);
        expect(getUserMedia).not.toHaveBeenCalled();
        expect(document.querySelector('.dograh-chat-panel--inline')).not.toBeNull();
    });

    it('renders and notifies the announcement before a delayed result, without duplicates', async () => {
        const fetchMock = createFetchMock(false);
        const widget = await loadWidget(fetchMock);
        await widget.startChat();
        const onMessage = vi.fn();
        widget.onMessage(onMessage);
        let controller!: ReadableStreamDefaultController<Uint8Array>;
        const response = new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; } }));
        fetchMock.mockResolvedValueOnce(response);
        const sending = widget.sendMessage('Look it up');
        const turn = { id: 'turn1', status: 'pending', user_message: { text: 'Look it up' }, assistant_messages: [] };
        const pending = { ...initialSession, revision: 3, turns: [turn] };
        controller.enqueue(encodeEvent({ type: 'session', session: pending }));
        const announcement = { text: 'I’ll check…' };
        const bytes = encodeEvent({ type: 'message', turn_id: turn.id, index: 0, message: announcement });
        // Split inside a multibyte character and the CRLF frame separator.
        const split = bytes.findIndex((byte) => byte >= 128) + 1;
        controller.enqueue(bytes.slice(0, split));
        controller.enqueue(bytes.slice(split, -1));
        controller.enqueue(bytes.slice(-1));
        await vi.advanceTimersByTimeAsync(0);
        expect(document.querySelectorAll('.dograh-chat-bubble--assistant')).toHaveLength(1);
        expect(document.querySelector('.dograh-chat-bubble--assistant')?.textContent).toBe(announcement.text);
        expect(widget.getState().chat.status).toBe('waiting');
        expect(document.querySelector<HTMLButtonElement>('.dograh-chat-send')?.disabled).toBe(true);
        expect(onMessage.mock.calls.map(([text]) => text)).toEqual([announcement.text]);

        const result = { text: 'Found it.' };
        controller.enqueue(encodeEvent({ type: 'message', turn_id: turn.id, index: 1, message: result }));
        controller.enqueue(encodeEvent({ type: 'complete', session: {
            ...pending, revision: 4, turns: [{ ...turn, status: 'completed',
                assistant_messages: [announcement, result], assistant_message: { text: 'I’ll check… Found it.' } }],
        } }));
        controller.close();
        await sending;
        expect(Array.from(document.querySelectorAll('.dograh-chat-bubble--assistant')).map((el) => el.textContent))
            .toEqual([announcement.text, result.text]);
        expect(onMessage.mock.calls.map(([text]) => text)).toEqual([announcement.text, result.text]);
        expect(widget.getState().chat.status).toBe('ready');
        expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/messages/stream'))).toHaveLength(1);
    });

    it('recovers an interrupted accepted turn with GET while preserving delivered speech', async () => {
        const fetchMock = createFetchMock(false);
        const widget = await loadWidget(fetchMock);
        await widget.startChat();
        const onMessage = vi.fn();
        widget.onMessage(onMessage);
        let controller!: ReadableStreamDefaultController<Uint8Array>;
        fetchMock.mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; } })));
        const turn = { id: 'turn1', status: 'pending', user_message: { text: 'Look it up' }, assistant_messages: [] };
        const pending = { ...initialSession, revision: 3, turns: [turn] };
        const announcement = { text: 'Checking.' };
        const result = { text: 'Done.' };
        fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(pending)));
        fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ...pending, revision: 4, turns: [{ ...turn, status: 'completed', assistant_messages: [announcement, result] }] })));
        const sending = widget.sendMessage('Look it up');
        controller.enqueue(encodeEvent({ type: 'session', session: pending }));
        controller.enqueue(encodeEvent({ type: 'message', turn_id: turn.id, index: 0, message: announcement }));
        await vi.advanceTimersByTimeAsync(0);
        controller.close();
        await vi.advanceTimersByTimeAsync(0);
        expect(document.querySelector('.dograh-chat-bubble--assistant')?.textContent).toBe(announcement.text);
        expect(widget.getState().chat.status).toBe('waiting');
        await widget.sendMessage('duplicate');
        await vi.advanceTimersByTimeAsync(1000);
        await sending;
        expect(onMessage.mock.calls.map(([text]) => text)).toEqual([announcement.text, result.text]);
        expect(widget.getState().chat.status).toBe('ready');
        expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/messages/stream'))).toHaveLength(1);
        expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/chat/emb_session_TEST'))).toHaveLength(2);
    });

    it('streams the opening announcement while initialization is still running', async () => {
        const fetchMock = createFetchMock(false);
        const widget = await loadWidget(fetchMock);
        let controller!: ReadableStreamDefaultController<Uint8Array>;
        fetchMock.mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; } })));
        const starting = widget.startChat();
        const turn = { id: 'greeting', status: 'pending', assistant_messages: [] };
        const pending = { ...initialSession, turns: [turn] };
        const greeting = { text: 'Let me check your account.' };
        controller.enqueue(encodeEvent({ type: 'session', session_token: 'emb_session_TEST', session: pending }));
        controller.enqueue(encodeEvent({ type: 'message', turn_id: turn.id, index: 0, message: greeting }));
        await vi.advanceTimersByTimeAsync(0);
        expect(document.querySelector('.dograh-chat-bubble--assistant')?.textContent).toBe(greeting.text);
        expect(widget.getState().chat.status).toBe('starting');
        controller.enqueue(encodeEvent({ type: 'complete', session: { ...pending, turns: [{ ...turn, status: 'completed', assistant_messages: [greeting] }] } }));
        controller.close();
        await starting;
        expect(widget.getState().chat.status).toBe('ready');
    });


    it.each([false, true])('restores a lost draft only when GET confirms no accepted turn (accepted=%s)', async (accepted) => {
        const fetchMock = createFetchMock(false);
        const widget = await loadWidget(fetchMock);
        await widget.startChat();
        fetchMock.mockRejectedValueOnce(new Error('Connection dropped before an SSE event'));
        fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(accepted ? {
            ...initialSession, revision: 4, turns: [{ id: 'turn1', status: 'completed',
                user_message: { text: 'Look it up' }, assistant_messages: [{ text: 'Done.' }] }],
        } : initialSession)));
        const input = document.querySelector<HTMLTextAreaElement>('.dograh-chat-input')!;
        input.value = 'Look it up';
        input.dispatchEvent(new Event('input'));
        document.querySelector<HTMLButtonElement>('.dograh-chat-send')!.click();
        await vi.advanceTimersByTimeAsync(0);
        expect(input.value).toBe(accepted ? '' : 'Look it up');
        expect(widget.getState().chat.status).toBe('ready');
        if (!accepted) expect(document.querySelector('.dograh-chat-banner')?.textContent).toContain('Message not sent');
        expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/messages/stream'))).toHaveLength(1);
    });

    it.each([false, true])('retains the submitted draft through failed recovery reads (accepted=%s)', async (accepted) => {
        const fetchMock = createFetchMock(false);
        const widget = await loadWidget(fetchMock);
        await widget.startChat();
        fetchMock.mockRejectedValueOnce(new Error('Connection dropped'));
        for (let i = 0; i < 3; i += 1) fetchMock.mockRejectedValueOnce(new Error('Offline'));
        const input = document.querySelector<HTMLTextAreaElement>('.dograh-chat-input')!;
        input.value = 'Look it up';
        input.dispatchEvent(new Event('input'));
        document.querySelector<HTMLButtonElement>('.dograh-chat-send')!.click();
        await vi.advanceTimersByTimeAsync(2000);
        expect(widget.getState().chat.status).toBe('recovering');
        expect(document.querySelector<HTMLButtonElement>('.dograh-chat-send')?.disabled).toBe(true);
        await widget.sendMessage('Do it again');
        await widget.endChat();
        expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/messages/stream'))).toHaveLength(1);
        expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/end'))).toBe(false);
        fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(accepted
            ? { ...initialSession, revision: 4, turns: [{ id: 'turn1', status: 'completed', assistant_messages: [{ text: 'Done.' }] }] }
            : initialSession)));
        await widget.startChat();
        expect(widget.getState().chat.status).toBe('ready');
        expect(input.value).toBe(accepted ? '' : 'Look it up');
        if (accepted) expect(document.querySelector('.dograh-chat-bubble--assistant')?.textContent).toBe('Done.');
        expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/messages/stream'))).toHaveLength(1);
    });

});
