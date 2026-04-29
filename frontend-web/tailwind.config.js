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
      },
    },
  },
  plugins: [],
};
