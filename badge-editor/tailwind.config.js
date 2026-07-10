/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,jsx}", "./components/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0d1117",
        panel: "#161b22",
        edge: "#30363d",
        phosphor: "#d3fa37",
        mist: "#ebf5ff",
        faded: "#8b949e",
        good: "#2ea043",
        bad: "#f85149",
        warn: "#d29922",
      },
      fontFamily: {
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
