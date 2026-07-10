import { NextResponse } from "next/server";
import { listBadges, publicBadge } from "@/lib/store";

export const dynamic = "force-dynamic";

export async function GET() {
  const badges = listBadges().map((b) => {
    const { secrets, ...rest } = publicBadge(b);
    return rest;
  });
  return NextResponse.json({ badges });
}
