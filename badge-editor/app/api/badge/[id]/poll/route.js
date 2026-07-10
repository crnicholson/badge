import { NextResponse } from "next/server";
import { getBadge, touchBadge } from "@/lib/store";

export const dynamic = "force-dynamic";

// Badge polls this with the version it has applied; a newer saved version
// comes back with the full secrets.py content to write to flash.
export async function GET(request, { params }) {
  const badge = getBadge(params.id);
  if (!badge) {
    return NextResponse.json({ error: "unknown badge" }, { status: 404 });
  }
  touchBadge(params.id);
  const have = Number(request.nextUrl.searchParams.get("have") ?? 0);
  if (badge.version > have) {
    return NextResponse.json({
      update: true,
      version: badge.version,
      content: badge.secrets,
    });
  }
  return NextResponse.json({ update: false, version: badge.version });
}
