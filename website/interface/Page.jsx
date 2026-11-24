import React, { useState } from "react";
import { LayoutDashboard, AppWindow, Download, Terminal } from "lucide-react";
import { Action, Choices, Figure } from "./ui.jsx";
export const site = {
  name: "AgenticFan",
  lang: "zh",
  nav: [
    ["应用预览", "#preview"],
    ["功能", "#features"],
    ["安装", "#start"],
    ["兼容范围", "#limits"],
  ],
};
const views = [
  {
    id: "menu",
    label: "菜单栏",
    icon: AppWindow,
    src: "/assets/menu-bar.png",
    width: 620,
    height: 802,
    alt: "原生菜单栏示例，显示两组风扇转速与温度读数。",
  },
  {
    id: "rules",
    label: "温度与规则",
    icon: LayoutDashboard,
    src: "/assets/app-overview.png",
    width: 1560,
    height: 750,
    alt: "AgenticFan 原生窗口的温度、风扇与规则界面。",
  },
];
export function Hero() {
  const [view, setView] = useState("menu");
  return (
    <section className="pi-hero fan-hero" aria-labelledby="hero-title">
      <div className="pi-container">
        <div className="fan-product-line">
          <h1 id="hero-title">AgenticFan</h1>
          <Action href="/download" icon={Download}>
            下载最新 DMG
          </Action>
        </div>
        <div className="fan-instrument">
          <div className="fan-readout">
            <p className="fan-caption">菜单栏示例 · 风扇转速</p>
            <p className="fan-speed">
              1800 <span>RPM</span>
            </p>
            <h2>Mac 温度与风扇控制</h2>
            <p>在菜单栏读温度和转速。需要手动控扇时，为规则设置有效时间。</p>
            <a href="#start">
              安装与设置 <Terminal size={15} />
            </a>
            <small>macOS 15+ · 配有风扇的 Apple Silicon Mac</small>
          </div>
          <div className="fan-screen" data-view={view}>
            <Choices
              label="查看原生界面"
              tabs={views}
              value={view}
              onChange={setView}
            >
              {(item) => <Figure {...item} title={item.label} priority />}
            </Choices>
          </div>
        </div>
      </div>
    </section>
  );
}
