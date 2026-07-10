import { NextResponse } from "next/server";
import { markApplied, publicBadge } from "@/lib/store";

export const dynamic = "force-dynamic";

// Badge confirms it wrote the given version to flash.
export async function POST(request, { params }) {
  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON" }, { status: 400 });
  }
  const version = Number(body.version);
  if (!Number.isFinite(version) || version < 1) {
    return NextResponse.json({ error: "invalid version" }, { status: 400 });
  }
  const badge = markApplied(params.id, version);
  if (!badge) {
    return NextResponse.json({ error: "unknown badge" }, { status: 404 });
  }
  return NextResponse.json({ ok: true, badge: publicBadge(badge) });
}
