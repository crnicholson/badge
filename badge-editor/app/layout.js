import "./globals.css";

export const metadata = {
  title: "Badge Editor",
  description: "Edit your badge's secrets.py over the air",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-mono">{children}</body>
    </html>
  );
}
