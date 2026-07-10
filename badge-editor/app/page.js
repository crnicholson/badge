"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

function timeAgo(ms) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.round(m / 60)}h ago`;
}

export default function Home() {
  const [badges, setBadges] = useState(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch("/api/badges");
        const data = await res.json();
        if (alive) setBadges(data.badges);
      } catch {
        // Server hiccup — keep the last list on screen and retry.
      }
    };
    load();
    const t = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  return (
    <main className="mx-auto max-w-2xl px-6 py-12">
      <header className="mb-10">
        <p className="text-xs uppercase tracking-[0.3em] text-phosphor">
          badge editor
        </p>
        <h1 className="mt-2 text-2xl font-bold text-mist">Connected badges</h1>
        <p className="mt-2 text-sm leading-relaxed text-faded">
          Open the <span className="text-mist">Editor</span> app on your badge.
          It registers here, then shows a QR code that opens its settings page
          on your phone.
        </p>
      </header>

      {badges === null ? (
        <p className="text-sm text-faded">Looking for badges…</p>
      ) : badges.length === 0 ? (
        <div className="rounded-md border border-dashed border-edge p-8 text-center">
          <p className="text-sm text-faded">
            No badges yet. Start the Editor app on a badge connected to this
            network and it will appear here.
          </p>
        </div>
      ) : (
        <ul className="space-y-3">
          {badges.map((b) => (
            <li key={b.id}>
              <Link
                href={`/b/${b.id}`}
                className="flex items-center justify-between rounded-md border border-edge bg-panel px-4 py-3 transition-colors hover:border-phosphor"
              >
                <span className="flex items-center gap-3">
                  <span
                    className={`h-2.5 w-2.5 rounded-full ${
                      b.online ? "bg-good led-online" : "bg-edge"
                    }`}
                    aria-label={b.online ? "online" : "offline"}
                  />
                  <span className="text-sm text-mist">{b.id}</span>
                </span>
                <span className="text-xs text-faded">
                  {b.pending ? (
                    <span className="text-warn">update pending · </span>
                  ) : null}
                  v{b.version} · seen {timeAgo(b.lastSeen)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
