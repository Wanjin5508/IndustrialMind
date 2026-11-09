import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "PrecisionMotion · RFQ 工作台",
  description: "证据驱动的内部报价工作台",
};
export default function Layout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
