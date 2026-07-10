import { NextResponse } from "next/server";
import { registerBadge, publicBadge } from "@/lib/store";

export const dynamic = "force-dynamic";

// Badge calls this on boot with its persistent id and current secrets.py.
export async function POST(request) {
  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON" }, { status: 400 });
  }
  const id = String(body.id ?? "").trim();
  if (!/^[a-zA-Z0-9_-]{4,64}$/.test(id)) {
    return NextResponse.json({ error: "invalid badge id" }, { status: 400 });
  }
  const ip = request.headers.get("x-forwarded-for") ?? body.ip ?? null;
  const badge = registerBadge(id, body.secrets, ip);
  return NextResponse.json({
    ok: true,
    id: badge.id,
    version: badge.version,
    appliedVersion: badge.appliedVersion,
    pending: publicBadge(badge).pending,
  });
}
