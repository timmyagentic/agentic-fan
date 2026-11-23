import React, { useState } from "react";
import { Terminal, ClipboardList } from "lucide-react";
import { Choices, CopyAction } from "./ui.jsx";
import instructions from "./installation.json";
const modes = [
  { id: "agent", label: "交给 Agent", icon: ClipboardList },
  { id: "manual", label: "手动检查", icon: Terminal },
];
export function Installation() {
  const [method, setMethod] = useState("agent");
  return (
    <section className="pi-install-box" aria-label="选择安装说明">
      <Choices
        label="安装说明"
        tabs={modes}
        value={method}
        onChange={setMethod}
      >
        {(m) => (
          <CopyAction
            text={
              m.id === "agent" ? instructions.prompt : instructions.commands
            }
            label={m.id === "agent" ? "复制安装提示词" : "复制检查命令"}
          />
        )}
      </Choices>
    </section>
  );
}
