import { Geist, Geist_Mono } from "next/font/google";
import { Inter } from "next/font/google"; // Correct: Destructuring the Inter font function
import "./globals.css";

const inter = Inter({subsets:["latin"]});

export const metadata = {
  title: "Finsight",
  description: "Your Financial Buddy",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className={`${inter.className}`}>
        {children}
      </body>
    </html>
  );
}
