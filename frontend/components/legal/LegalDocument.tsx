import Link from "next/link";
import type { ReactNode } from "react";

import { fillLegalValues, type LegalValues } from "@/config/legal";
import type { LegalBlock, LegalDocument as LegalDocumentData } from "@/content/legal";

// Unfilled operator placeholders such as [CONTACT E-MAIL] are highlighted so a
// draft can never be mistaken for final text.
const PLACEHOLDER = /(\[[A-Z0-9 /:.,-]+\])/;

function Text({ children, values }: { children: string; values: LegalValues }) {
  const parts = fillLegalValues(children, values).split(PLACEHOLDER);
  return (
    <>
      {parts.map((part, i) =>
        PLACEHOLDER.test(part) ? (
          <mark key={i} className="rounded bg-warn/20 px-1 text-foreground">
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}

function Block({ block, values }: { block: LegalBlock; values: LegalValues }): ReactNode {
  if (typeof block === "string") {
    return (
      <p>
        <Text values={values}>{block}</Text>
      </p>
    );
  }
  if ("list" in block) {
    return (
      <ul className="list-disc space-y-1 pl-6">
        {block.list.map((item) => (
          <li key={item}>
            <Text values={values}>{item}</Text>
          </li>
        ))}
      </ul>
    );
  }
  return (
    <ul className="space-y-2">
      {block.links.map(({ label, href, description }) => (
        <li key={href}>
          {href.startsWith("/") ? (
            <Link href={href} className="font-semibold text-brand underline-offset-2 hover:underline">
              {label}
            </Link>
          ) : (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="font-semibold text-brand underline-offset-2 hover:underline"
            >
              {label}
            </a>
          )}
          {description && <span className="text-muted-strong"> — {description}</span>}
        </li>
      ))}
    </ul>
  );
}

/** Renders one legal document: a single h1, one h2 per section, anchored by id. */
export function LegalDocument({ doc, values }: { doc: LegalDocumentData; values: LegalValues }) {
  return (
    <article aria-labelledby="legal-title" className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <h1 id="legal-title" className="text-2xl font-extrabold tracking-tight text-foreground">
          {doc.title}
        </h1>
        <p className="text-muted-strong">
          <Text values={values}>{doc.summary}</Text>
        </p>
      </header>
      {doc.sections.map((section) => (
        <section
          key={section.id}
          id={section.id}
          aria-labelledby={`${section.id}-heading`}
          className="flex scroll-mt-20 flex-col gap-3 text-sm leading-relaxed text-foreground"
        >
          <h2 id={`${section.id}-heading`} className="text-lg font-bold text-foreground">
            {section.heading}
          </h2>
          {section.blocks.map((block, i) => (
            <Block key={i} block={block} values={values} />
          ))}
        </section>
      ))}
    </article>
  );
}
