import { Button } from "@heroui/react/button";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

export function mountCopy(top, copy) {
  const button = top.querySelector("button[data-copy]");
  const caption = top.querySelector("span").textContent;
  const promptId = button.dataset.copy;
  const focused = document.activeElement === button;
  const root = createRoot(top);
  flushSync(() =>
    root.render(
      <>
        <span>{caption}</span>
        <Button
          onPress={() => copy(top)}
          render={(props) => (
            <button
              {...props}
              className="copy-button"
              type="button"
              data-copy={promptId}
            />
          )}
        >
          {button.textContent}
        </Button>
      </>,
    ),
  );
  if (focused) top.querySelector("button").focus({ preventScroll: true });
}
