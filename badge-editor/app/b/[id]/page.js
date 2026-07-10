"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

export default function BadgePage({ params }) {
  const { id } = params;
  const [badge, setBadge] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  // Version of our last save; drives the pending → flashed → RESET readout.
  const [savedVersion, setSavedVersion] = useState(null);
  const loadedRef = useRef(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(`/api/badge/${id}`);
        if (res.status === 404) {
          if (alive) setNotFound(true);
          return;
        }
        const data = await res.json();
        if (!alive) return;
        setBadge(data.badge);
        // Fill the editor once; afterwards the textarea belongs to the user.
        if (!loadedRef.current) {
          loadedRef.current = true;
          setContent(data.badge.secrets);
        }
      } catch {
        // Server unreachable — keep current state and retry.
      }
    };
    load();
    const t = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [id]);

  const save = useCallback(async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const res = await fetch(`/api/badge/${id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "save failed");
      setBadge(data.badge);
      setSavedVersion(data.badge.version);
      setDirty(false);
    } catch (err) {
      setSaveError(String(err.message ?? err));
    } finally {
      setSaving(false);
    }
  }, [id, content]);

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  if (notFound) {
    return (
      <main className="mx-auto max-w-2xl px-6 py-12">
        <p className="text-sm text-bad">Unknown badge “{id}”.</p>
        <p className="mt-2 text-sm text-faded">
          Start the Editor app on the badge so it registers, then scan its QR
          code again. <Link href="/" className="text-phosphor underline">All badges</Link>
        </p>
      </main>
    );
  }

  const online = badge?.online ?? false;
  const flashed =
    savedVersion !== null && badge && badge.appliedVersion >= savedVersion;
  const waitingForBadge =
    savedVersion !== null && badge && badge.appliedVersion < savedVersion;

  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <header className="mb-6">
        <Link
          href="/"
          className="text-xs uppercase tracking-[0.3em] text-faded hover:text-phosphor"
        >
          ← badge editor
        </Link>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-edge bg-panel px-4 py-3 text-xs">
          <span className="flex items-center gap-2">
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                online ? "bg-good led-online" : "bg-edge"
              }`}
            />
            <span className="text-mist">{id}</span>
          </span>
          <span className="text-faded">
            {online ? "online" : "offline — waiting for the badge to check in"}
          </span>
          {badge && (
            <span className="ml-auto text-faded">
              v{badge.version}
              {badge.pending ? " · delivery pending" : " · in sync"}
            </span>
          )}
        </div>
      </header>

      {flashed && (
        <div className="mb-4 rounded-md border border-good bg-good/10 px-4 py-3">
          <p className="text-sm font-bold text-good">
            Flashed to the badge.
          </p>
          <p className="mt-1 text-sm text-mist">
            Press <span className="rounded border border-edge bg-ink px-1.5 py-0.5 text-xs">RESET</span>{" "}
            on the back of the badge to load the new settings.
          </p>
        </div>
      )}
      {waitingForBadge && (
        <div className="mb-4 rounded-md border border-warn bg-warn/10 px-4 py-3">
          <p className="text-sm text-warn">
            Saved as v{savedVersion}. Waiting for the badge to fetch it
            {online ? "…" : " — it looks offline right now."}
          </p>
        </div>
      )}
      {saveError && (
        <div className="mb-4 rounded-md border border-bad bg-bad/10 px-4 py-3">
          <p className="text-sm text-bad">Save failed: {saveError}</p>
        </div>
      )}

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h1 className="text-sm text-faded">
            <span className="text-mist">secrets.py</span>
            {dirty && <span className="text-warn"> · unsaved changes</span>}
          </h1>
          <button
            onClick={save}
            disabled={saving || !badge}
            className="rounded-md bg-phosphor px-4 py-1.5 text-sm font-bold text-ink transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {saving ? "Saving…" : "Save & push"}
          </button>
        </div>
        <textarea
          value={content}
          onChange={(e) => {
            setContent(e.target.value);
            setDirty(true);
          }}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          rows={24}
          className="w-full resize-y rounded-md border border-edge bg-panel p-4 text-sm leading-relaxed text-mist caret-phosphor focus:border-phosphor focus:outline-none"
          placeholder={badge ? "" : "Loading secrets.py from the badge…"}
        />
        <p className="mt-2 text-xs text-faded">
          Saving pushes this file to the badge over the air. WiFi passwords and
          tokens stay on this machine — nothing leaves your network.
        </p>
      </section>
    </main>
  );
}
