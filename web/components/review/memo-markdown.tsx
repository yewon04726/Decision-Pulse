import { Fragment, type ReactNode } from "react";

/**
 * Review Memo용 작은 Markdown 렌더러 (#/##/### 제목, -/* /1. 목록, 문단, 인용, **굵게**, `코드`).
 * HTML로 해석하지 않고 React element만 만든다 (모델 출력이 HTML로 실행되지 않게). 표 등 그 밖의 문법은 원문 그대로 보인다.
 */
export function MemoMarkdown({ content }: { content: string }) {
  const blocks: ReactNode[] = [];
  // flushList(closure)가 null로 되돌리므로 초기값을 단언해 TS가 null로 좁히지 않게 한다
  let list = null as { ordered: boolean; items: { depth: number; text: string }[] } | null;
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push(
        <p key={blocks.length} className="text-sm leading-relaxed text-ink">
          {paragraph.map((line, i) => (
            <Fragment key={i}>
              {i > 0 && <br />}
              {inline(line)}
            </Fragment>
          ))}
        </p>,
      );
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      const Tag = list.ordered ? "ol" : "ul";
      blocks.push(
        <Tag key={blocks.length} className={`${list.ordered ? "list-decimal" : "list-disc"} space-y-1 pl-5 text-sm text-ink`}>
          {list.items.map((item, i) => (
            <li key={i} style={item.depth ? { marginLeft: `${item.depth * 1}rem` } : undefined}>
              {inline(item.text)}
            </li>
          ))}
        </Tag>,
      );
      list = null;
    }
  };

  for (const raw of content.split("\n")) {
    const line = raw.trimEnd();
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    const bullet = /^(\s*)[-*]\s+(.*)$/.exec(line);
    const numbered = /^(\s*)\d+[.)]\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length;
      const cls =
        level === 1
          ? "text-lg font-semibold text-ink"
          : level === 2
            ? "mt-2 border-b border-line pb-1 text-sm font-semibold text-ink"
            : "text-[13px] font-semibold text-ink-2";
      const Tag = level === 1 ? "h3" : level === 2 ? "h4" : "h5";
      blocks.push(
        <Tag key={blocks.length} className={cls}>
          {inline(heading[2])}
        </Tag>,
      );
    } else if (bullet || numbered) {
      flushParagraph();
      const m = (bullet ?? numbered)!;
      const ordered = !bullet;
      if (list && list.ordered !== ordered) flushList();
      list ??= { ordered, items: [] };
      list.items.push({ depth: Math.floor(m[1].replace(/\t/g, "  ").length / 2), text: m[2] });
    } else if (line.startsWith(">")) {
      flushParagraph();
      flushList();
      blocks.push(
        <blockquote key={blocks.length} className="border-l-2 border-line-strong pl-3 text-sm text-ink-2">
          {inline(line.replace(/^>\s?/, ""))}
        </blockquote>,
      );
    } else if (!line.trim()) {
      flushParagraph();
      flushList();
    } else {
      flushList();
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushList();
  return <div className="space-y-3">{blocks}</div>;
}

function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const token = m[0];
    parts.push(
      token.startsWith("**") ? (
        <strong key={m.index} className="font-semibold text-ink">
          {token.slice(2, -2)}
        </strong>
      ) : (
        <code key={m.index} className="rounded bg-soft px-1 font-mono text-[12px]">
          {token.slice(1, -1)}
        </code>
      ),
    );
    last = m.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}
