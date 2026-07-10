import fs from "node:fs";
import path from "node:path";

// File-backed badge registry. Kept on globalThis so Next.js dev-mode module
// reloads don't lose in-flight state between requests.
const DATA_DIR = path.join(process.cwd(), ".data");
const DATA_FILE = path.join(DATA_DIR, "badges.json");
const STORE_KEY = Symbol.for("badge-editor.store");

function loadFromDisk() {
  try {
    return JSON.parse(fs.readFileSync(DATA_FILE, "utf8"));
  } catch {
    return {};
  }
}

function getBadges() {
  if (!globalThis[STORE_KEY]) {
    globalThis[STORE_KEY] = { badges: loadFromDisk() };
  }
  return globalThis[STORE_KEY].badges;
}

function persist() {
  const badges = getBadges();
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(DATA_FILE, JSON.stringify(badges, null, 2));
}

const ONLINE_WINDOW_MS = 12_000;

export function publicBadge(badge) {
  if (!badge) return null;
  return {
    ...badge,
    online: Date.now() - badge.lastSeen < ONLINE_WINDOW_MS,
    pending: badge.version > badge.appliedVersion,
  };
}

export function getBadge(id) {
  return getBadges()[id] ?? null;
}

export function listBadges() {
  return Object.values(getBadges()).sort((a, b) => b.lastSeen - a.lastSeen);
}

export function registerBadge(id, secrets, ip) {
  const badges = getBadges();
  const now = Date.now();
  const existing = badges[id];
  if (existing) {
    existing.lastSeen = now;
    existing.ip = ip;
    // Only accept the badge's copy of secrets.py when no edit is waiting to
    // be delivered, so a pending update never gets clobbered on re-register.
    if (existing.version === existing.appliedVersion && typeof secrets === "string") {
      existing.secrets = secrets;
    }
  } else {
    badges[id] = {
      id,
      secrets: typeof secrets === "string" ? secrets : "",
      version: 1,
      appliedVersion: 1,
      registeredAt: now,
      lastSeen: now,
      ip,
    };
  }
  persist();
  return badges[id];
}

export function saveSecrets(id, content) {
  const badge = getBadges()[id];
  if (!badge) return null;
  badge.secrets = content;
  badge.version += 1;
  persist();
  return badge;
}

export function touchBadge(id) {
  const badge = getBadges()[id];
  if (!badge) return null;
  badge.lastSeen = Date.now();
  persist();
  return badge;
}

export function markApplied(id, version) {
  const badge = getBadges()[id];
  if (!badge) return null;
  badge.appliedVersion = Math.max(badge.appliedVersion, version);
  badge.lastSeen = Date.now();
  persist();
  return badge;
}
