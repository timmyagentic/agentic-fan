import React, { useState } from "react";
import {
  Fan,
  Thermometer,
  LayoutDashboard,
  AppWindow,
  Download,
  Terminal,
} from "lucide-react";
import { Action, Choices, Figure, HeroCopy, Mark } from "./ui.jsx";
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
    <section className="pi-hero fan-hero">
      <div className="pi-container pi-split">
        <HeroCopy
          mark={<Mark Icon={Fan}>Apple Silicon · macOS 15+</Mark>}
          title={
            <>
              看懂 Mac 的温度。
              <br />
              掌握风扇转速。
            </>
          }
          description="从菜单栏读传感器，在应用里设置有时限的控扇规则，也可以通过 CLI 查看同一组读数。"
        >
          <div className="pi-actions">
            <Action href="/download" icon={Download}>
              下载最新 DMG
            </Action>
            <Action href="#start" tone="ghost" icon={Terminal}>
              用 Agent 安装
            </Action>
          </div>
          <p className="fan-compat">适用于配有风扇的 Apple Silicon Mac</p>
        </HeroCopy>
        <section className="fan-console" aria-label="原生控扇界面">
          <div className="fan-console-head">
            <span>
              <Thermometer size={20} />
              传感器与控扇
            </span>
            <small>应用演示数据</small>
          </div>
          <Choices
            label="查看原生界面"
            tabs={views}
            value={view}
            onChange={setView}
          >
            {(item) => <Figure {...item} title={item.label} priority />}
          </Choices>
        </section>
      </div>
    </section>
  );
}
