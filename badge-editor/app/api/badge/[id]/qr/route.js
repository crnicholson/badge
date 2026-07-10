import { NextResponse } from "next/server";
import QRCode from "qrcode";
import { getBadge } from "@/lib/store";
import { lanAddress } from "@/lib/lanip";

export const dynamic = "force-dynamic";

// Returns the QR module matrix as JSON so the badge can draw it directly —
// no QR encoder or PNG decoding needed on the MicroPython side.
export async function GET(request, { params }) {
  const badge = getBadge(params.id);
  if (!badge) {
    return NextResponse.json({ error: "unknown badge" }, { status: 404 });
  }
  const host = lanAddress(request.headers.get("host"));
  const url = `http://${host}/b/${params.id}`;
  const qr = QRCode.create(url, { errorCorrectionLevel: "M" });
  const size = qr.modules.size;
  const rows = [];
  for (let r = 0; r < size; r++) {
    let row = "";
    for (let c = 0; c < size; c++) {
      row += qr.modules.get(r, c) ? "1" : "0";
    }
    rows.push(row);
  }
  return NextResponse.json({ url, size, rows });
}
