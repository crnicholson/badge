import os from "node:os";

// The QR code must contain an address the *phone* can reach, so prefer a
// LAN IPv4 over whatever host the badge happened to connect with.
export function lanAddress(requestHost) {
  const interfaces = os.networkInterfaces();
  let lanIp = null;
  for (const name of Object.keys(interfaces)) {
    for (const net of interfaces[name] ?? []) {
      if (net.family === "IPv4" && !net.internal) {
        // Prefer common home-LAN ranges over link-local etc.
        if (/^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)/.test(net.address)) {
          return withPort(net.address, requestHost);
        }
        lanIp = lanIp ?? net.address;
      }
    }
  }
  if (lanIp) return withPort(lanIp, requestHost);
  return requestHost || "localhost:3000";
}

function withPort(ip, requestHost) {
  const port = requestHost?.includes(":") ? requestHost.split(":").pop() : "3000";
  return `${ip}:${port}`;
}
