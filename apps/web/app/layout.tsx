import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "PrecisionMotion · RFQ 工作台",
  description: "工程询价与报价管理",
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
