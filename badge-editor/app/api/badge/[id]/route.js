import { NextResponse } from "next/server";
import { getBadge, saveSecrets, publicBadge } from "@/lib/store";

export const dynamic = "force-dynamic";

export async function GET(_request, { params }) {
  const badge = getBadge(params.id);
  if (!badge) {
    return NextResponse.json({ error: "unknown badge" }, { status: 404 });
  }
  return NextResponse.json({ badge: publicBadge(badge) });
}

// Browser saves an edited secrets.py; bumps the version the badge polls for.
export async function POST(request, { params }) {
  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON" }, { status: 400 });
  }
  if (typeof body.content !== "string") {
    return NextResponse.json({ error: "content must be a string" }, { status: 400 });
  }
  if (body.content.length > 64 * 1024) {
    return NextResponse.json({ error: "content too large" }, { status: 413 });
  }
  const badge = saveSecrets(params.id, body.content);
  if (!badge) {
    return NextResponse.json({ error: "unknown badge" }, { status: 404 });
  }
  return NextResponse.json({ ok: true, badge: publicBadge(badge) });
}
