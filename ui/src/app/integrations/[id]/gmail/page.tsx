'use client';

import { useParams, useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { getIntegrationAccessTokenApiV1IntegrationIntegrationIdAccessTokenGet } from '@/client/sdk.gen';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';

interface Email {
    id: string;
    threadId: string;
    subject: string;
    from: string;
    snippet: string;
    date: string;
}

interface EmailDetail {
    id: string;
    threadId: string;
    subject: string;
    from: string;
    to: string;
    date: string;
    body: string;
}

interface GmailHeader {
    name: string;
    value: string;
}

interface GmailPayloadPart {
    mimeType: string;
    body: {
        data?: string;
    };
}

export default function GmailSearchPage() {
    const params = useParams();
    const router = useRouter();
    const integrationId = parseInt(params.id as string);
    const { getAccessToken, redirectToLogin } = useAuth();

    const [accessToken, setAccessToken] = useState<string | null>(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [emails, setEmails] = useState<Email[]>([]);
    const [selectedEmail, setSelectedEmail] = useState<EmailDetail | null>(null);
    const [replyText, setReplyText] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [sendingReply, setSendingReply] = useState(false);

    const fetchAccessToken = useCallback(async () => {
        try {
            const token = await getAccessToken();
            if (!token) {
                redirectToLogin();
                return;
            }

            const response = await getIntegrationAccessTokenApiV1IntegrationIntegrationIdAccessTokenGet({
                path: { integration_id: integrationId },
                headers: { Authorization: `Bearer ${token}` },
            });

            if (response.data?.access_token) {
                setAccessToken(response.data.access_token);
            } else {
                setError('Failed to get access token');
            }
        } catch (err) {
            logger.error('Error fetching access token:', err);
            setError('Failed to fetch access token. Please try again.');
        }
    }, [getAccessToken, redirectToLogin, integrationId]);

    useEffect(() => {
        fetchAccessToken();
    }, [fetchAccessToken]);

    const searchEmails = async () => {
        if (!accessToken || !searchQuery.trim()) return;

        setLoading(true);
        setError(null);
        setEmails([]);
        setSelectedEmail(null);

        try {
            const response = await fetch(
                `https://gmail.googleapis.com/gmail/v1/users/me/messages?q=${encodeURIComponent(searchQuery)}&maxResults=20`,
                {
                    headers: {
                        Authorization: `Bearer ${accessToken}`,
                    },
                }
            );

            if (!response.ok) {
                throw new Error(`Gmail API error: ${response.statusText}`);
            }

            const data = await response.json();

            if (!data.messages || data.messages.length === 0) {
                setEmails([]);
                return;
            }

            // Fetch details for each message
            const emailPromises = data.messages.map(async (msg: { id: string }) => {
                const msgResponse = await fetch(
                    `https://gmail.googleapis.com/gmail/v1/users/me/messages/${msg.id}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date`,
                    {
                        headers: {
                            Authorization: `Bearer ${accessToken}`,
                        },
                    }
                );
                return msgResponse.json();
            });

            const emailDetails = await Promise.all(emailPromises);

            const formattedEmails: Email[] = emailDetails.map((email) => {
                const headers = email.payload.headers as GmailHeader[];
                const subject = headers.find((h) => h.name === 'Subject')?.value || 'No Subject';
                const from = headers.find((h) => h.name === 'From')?.value || 'Unknown';
                const date = headers.find((h) => h.name === 'Date')?.value || '';

                return {
                    id: email.id,
                    threadId: email.threadId,
                    subject,
                    from,
                    snippet: email.snippet || '',
                    date,
                };
            });

            setEmails(formattedEmails);
        } catch (err) {
            logger.error('Error searching emails:', err);
            setError('Failed to search emails. Please try again.');
        } finally {
            setLoading(false);
        }
    };

    const loadEmailDetail = async (emailId: string) => {
        if (!accessToken) return;

        setLoading(true);
        setError(null);

        try {
            const response = await fetch(
                `https://gmail.googleapis.com/gmail/v1/users/me/messages/${emailId}?format=full`,
                {
                    headers: {
                        Authorization: `Bearer ${accessToken}`,
                    },
                }
            );

            if (!response.ok) {
                throw new Error(`Gmail API error: ${response.statusText}`);
            }

            const email = await response.json();
            const headers = email.payload.headers as GmailHeader[];

            const subject = headers.find((h) => h.name === 'Subject')?.value || 'No Subject';
            const from = headers.find((h) => h.name === 'From')?.value || 'Unknown';
            const to = headers.find((h) => h.name === 'To')?.value || 'Unknown';
            const date = headers.find((h) => h.name === 'Date')?.value || '';

            // Extract email body
            let body = '';
            if (email.payload.body.data) {
                body = atob(email.payload.body.data.replace(/-/g, '+').replace(/_/g, '/'));
            } else if (email.payload.parts) {
                const parts = email.payload.parts as GmailPayloadPart[];
                const textPart = parts.find((part) => part.mimeType === 'text/plain');
                if (textPart && textPart.body.data) {
                    body = atob(textPart.body.data.replace(/-/g, '+').replace(/_/g, '/'));
                }
            }

            setSelectedEmail({
                id: email.id,
                threadId: email.threadId,
                subject,
                from,
                to,
                date,
                body,
            });
        } catch (err) {
            logger.error('Error loading email detail:', err);
            setError('Failed to load email details. Please try again.');
        } finally {
            setLoading(false);
        }
    };

    const sendReply = async () => {
        if (!accessToken || !selectedEmail || !replyText.trim()) return;

        setSendingReply(true);
        setError(null);

        try {
            // Create the email message
            const to = selectedEmail.from.match(/<(.+)>/)?.[1] || selectedEmail.from;
            const subject = selectedEmail.subject.startsWith('Re:')
                ? selectedEmail.subject
                : `Re: ${selectedEmail.subject}`;

            const messageParts = [
                `To: ${to}`,
                `Subject: ${subject}`,
                `In-Reply-To: ${selectedEmail.id}`,
                `References: ${selectedEmail.id}`,
                '',
                replyText,
            ];

            const message = messageParts.join('\n');
            const encodedMessage = btoa(message)
                .replace(/\+/g, '-')
                .replace(/\//g, '_')
                .replace(/=+$/, '');

            const response = await fetch(
                `https://gmail.googleapis.com/gmail/v1/users/me/messages/send`,
                {
                    method: 'POST',
                    headers: {
                        Authorization: `Bearer ${accessToken}`,
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        raw: encodedMessage,
                        threadId: selectedEmail.threadId,
                    }),
                }
            );

            if (!response.ok) {
                throw new Error(`Gmail API error: ${response.statusText}`);
            }

            alert('Reply sent successfully!');
            setReplyText('');
        } catch (err) {
            logger.error('Error sending reply:', err);
            setError('Failed to send reply. Please try again.');
        } finally {
            setSendingReply(false);
        }
    };

    return (
        <div className="container mx-auto px-4 py-8">
            <div className="mb-6">
                <button
                    onClick={() => router.back()}
                    className="text-blue-600 hover:text-blue-800 mb-4"
                >
                    ← Back to Integrations
                </button>
                <h1 className="text-2xl font-bold">Gmail Search</h1>
            </div>

            {/* Search Section */}
            <div className="mb-6">
                <div className="flex gap-2">
                    <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        onKeyPress={(e) => e.key === 'Enter' && searchEmails()}
                        placeholder="Search emails (e.g., from:user@example.com, subject:meeting)"
                        className="flex-1 px-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                        disabled={!accessToken || loading}
                    />
                    <button
                        onClick={searchEmails}
                        disabled={!accessToken || loading || !searchQuery.trim()}
                        className="px-6 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
                    >
                        {loading ? 'Searching...' : 'Search'}
                    </button>
                </div>
            </div>

            {error && (
                <div className="mb-6 p-4 bg-red-50 border border-red-200 text-red-700 rounded-md">
                    {error}
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Email List */}
                <div className="bg-white border border-gray-200 rounded-lg">
                    <div className="p-4 border-b border-gray-200">
                        <h2 className="text-lg font-semibold">Search Results</h2>
                    </div>
                    <div className="divide-y divide-gray-200 max-h-[600px] overflow-y-auto">
                        {emails.length === 0 && !loading && (
                            <div className="p-8 text-center text-gray-500">
                                {searchQuery ? 'No emails found' : 'Enter a search query to find emails'}
                            </div>
                        )}
                        {emails.map((email) => (
                            <div
                                key={email.id}
                                onClick={() => loadEmailDetail(email.id)}
                                className={`p-4 cursor-pointer hover:bg-gray-50 ${
                                    selectedEmail?.id === email.id ? 'bg-blue-50' : ''
                                }`}
                            >
                                <div className="font-medium text-sm mb-1">{email.subject}</div>
                                <div className="text-xs text-gray-600 mb-1">{email.from}</div>
                                <div className="text-xs text-gray-500">{email.snippet}</div>
                                <div className="text-xs text-gray-400 mt-1">{email.date}</div>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Email Detail and Reply */}
                <div className="bg-white border border-gray-200 rounded-lg">
                    <div className="p-4 border-b border-gray-200">
                        <h2 className="text-lg font-semibold">Email Details</h2>
                    </div>
                    {selectedEmail ? (
                        <div className="p-4">
                            <div className="mb-4 pb-4 border-b border-gray-200">
                                <div className="mb-2">
                                    <span className="font-medium">Subject:</span> {selectedEmail.subject}
                                </div>
                                <div className="mb-2 text-sm">
                                    <span className="font-medium">From:</span> {selectedEmail.from}
                                </div>
                                <div className="mb-2 text-sm">
                                    <span className="font-medium">To:</span> {selectedEmail.to}
                                </div>
                                <div className="text-sm text-gray-500">
                                    <span className="font-medium">Date:</span> {selectedEmail.date}
                                </div>
                            </div>

                            <div className="mb-4 p-4 bg-gray-50 rounded max-h-[200px] overflow-y-auto">
                                <pre className="whitespace-pre-wrap text-sm">{selectedEmail.body}</pre>
                            </div>

                            <div className="border-t border-gray-200 pt-4">
                                <h3 className="font-medium mb-2">Reply</h3>
                                <textarea
                                    value={replyText}
                                    onChange={(e) => setReplyText(e.target.value)}
                                    placeholder="Type your reply here..."
                                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 mb-2"
                                    rows={6}
                                    disabled={sendingReply}
                                />
                                <button
                                    onClick={sendReply}
                                    disabled={sendingReply || !replyText.trim()}
                                    className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
                                >
                                    {sendingReply ? 'Sending...' : 'Send Reply'}
                                </button>
                            </div>
                        </div>
                    ) : (
                        <div className="p-8 text-center text-gray-500">
                            Select an email to view details and reply
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
