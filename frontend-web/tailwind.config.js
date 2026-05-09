/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: "#5b3df6",
          dark: "#3f25c9",
        },
        // Facebook brand blue (slice 3 / #39). Pulled into the theme so the
        // Facebook button can use `bg-facebook` / `hover:bg-facebook-dark`
        // / `focus:ring-facebook` instead of inline hex or arbitrary-value
        // classes — the brand fork is acknowledged, the workaround is not.
        // White text on `#1877F2` is contrast ratio 4.51:1 (passes WCAG AA
        // for normal text). `dark` is a darker shade for hover/focus.
        facebook: {
          DEFAULT: "#1877F2",
          dark: "#0E5FCB",
        },
      },
    },
  },
  plugins: [],
};
