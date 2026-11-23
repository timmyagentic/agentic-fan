import React from "react";
import { Header, FAQ, Notifications, useLanguage } from "./ui.jsx";
import { site, Hero } from "./Page.jsx";
import { Installation } from "./Installation.jsx";
import questions from "./questions.json";

export function Surface({ part }) {
  const lang = useLanguage(site.lang);
  if (part === "header") return <Header site={site} lang={lang} />;
  if (part === "install") return <Installation />;
  if (part === "faq") return <FAQ items={questions} lang={lang} />;
  return (
    <>
      <Hero lang={lang} />
      <Notifications lang={lang} />
    </>
  );
}
