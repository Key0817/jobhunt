/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Colores oscuros basados en la imagen
        dark: {
          900: '#0F0F11', // Fondo principal
          800: '#161618', // Fondo de tarjetas
          700: '#232325', // Bordes
        }
      }
    },
  },
  plugins: [],
}