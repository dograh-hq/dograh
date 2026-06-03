import { NextRequest, NextResponse } from "next/server";

import { getServerBackendUrl } from "@/lib/apiClient";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const HOP_BY_HOP_HEADERS = [
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
];

function trimTrailingSlash(url: string) {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

function buildBackendUrl(request: NextRequest) {
  const backendUrl = trimTrailingSlash(getServerBackendUrl());
  return `${backendUrl}${request.nextUrl.pathname}${request.nextUrl.search}`;
}

function createRequestHeaders(request: NextRequest) {
  const headers = new Headers(request.headers);

  for (const header of HOP_BY_HOP_HEADERS) {
    headers.delete(header);
  }

  headers.delete("accept-encoding");
  headers.delete("content-length");
  headers.delete("host");

  return headers;
}

function createResponseHeaders(response: Response) {
  const headers = new Headers(response.headers);
  const setCookies = response.headers.getSetCookie();

  for (const header of HOP_BY_HOP_HEADERS) {
    headers.delete(header);
  }

  headers.delete("content-encoding");
  headers.delete("content-length");
  headers.delete("set-cookie");

  for (const cookie of setCookies) {
    headers.append("set-cookie", cookie);
  }

  return headers;
}

async function getRequestBody(request: NextRequest) {
  if (request.method === "GET" || request.method === "HEAD") {
    return undefined;
  }

  return request.arrayBuffer();
}

async function proxyRequest(request: NextRequest) {
  const backendUrl = buildBackendUrl(request);

  try {
    const response = await fetch(backendUrl, {
      method: request.method,
      headers: createRequestHeaders(request),
      body: await getRequestBody(request),
      cache: "no-store",
    });

    return new Response(request.method === "HEAD" ? null : response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: createResponseHeaders(response),
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Unknown backend proxy error";

    return NextResponse.json(
      {
        detail: `Backend request failed while proxying to ${backendUrl}: ${message}`,
      },
      { status: 502 },
    );
  }
}

export const GET = proxyRequest;
export const POST = proxyRequest;
export const PUT = proxyRequest;
export const PATCH = proxyRequest;
export const DELETE = proxyRequest;
export const OPTIONS = proxyRequest;
export const HEAD = proxyRequest;
